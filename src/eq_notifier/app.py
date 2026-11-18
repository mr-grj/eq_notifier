"""Runtime: feed threads hand reports to one alerter, which decides, notifies and remembers."""

import logging
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from queue import Empty, Queue

import httpx

from eq_notifier import USER_AGENT
from eq_notifier.config import Settings
from eq_notifier.events import Earthquake, EventGroup, Sink, group_duplicates
from eq_notifier.notifiers import Notifier, send_all
from eq_notifier.sources import Source, fetch_each
from eq_notifier.state import SeenEvents
from eq_notifier.streams import Stream

log = logging.getLogger(__name__)

WAKE_CHECK_SECONDS = 1.0
FEED_CHECK_SECONDS = 1.0


def make_client() -> httpx.Client:
    """HTTP client shared by every source and notifier."""
    return httpx.Client(
        timeout=httpx.Timeout(10.0, connect=5.0),
        transport=httpx.HTTPTransport(retries=1),
        headers={"User-Agent": USER_AGENT},
    )


def is_relevant(event: Earthquake, settings: Settings) -> bool:
    """Whether a report is strong enough and close enough to alert on."""
    if event.magnitude < settings.min_magnitude:
        return False
    if (location := settings.location) is None:
        return settings.bbox.contains(event.latitude, event.longitude)
    return event.distance_km(location.latitude, location.longitude) <= settings.radius_km


def format_alert(group: EventGroup, settings: Settings, now: datetime) -> tuple[str, str]:
    """Title and body of the alert for one earthquake."""
    quake = group.primary
    where = quake.region or f"{quake.latitude:.2f}, {quake.longitude:.2f}"
    title = f"Earthquake M{quake.magnitude:.1f} - {where}"
    depth = f"{quake.depth_km:.0f} km" if quake.depth_km is not None else "unknown"
    minutes_ago = max(int(quake.age(now).total_seconds() // 60), 0)
    lines = [
        f"Magnitude {quake.magnitude:.1f}, depth {depth}",
        f"Epicentre: {where} ({quake.latitude:.2f}, {quake.longitude:.2f})",
        f"Origin time: {quake.time:%Y-%m-%d %H:%M:%S} UTC ({minutes_ago} min ago)",
    ]
    if settings.location is not None:
        distance = quake.distance_km(settings.location.latitude, settings.location.longitude)
        lines.append(f"Distance from you: {distance:.0f} km")
    lines.append(
        "Reported by: " + ", ".join(f"{r.source} (M{r.magnitude:.1f})" for r in group.reports)
    )
    if quake.url:
        lines.append(quake.url)
    return title, "\n".join(lines)


class Verdict(StrEnum):
    IGNORE = "ignored"
    SEEN = "already alerted"
    ALERT = "would alert"


@dataclass(frozen=True, slots=True)
class Alerter:
    """Turns incoming reports into exactly one delivered alert per relevant earthquake."""

    settings: Settings
    notifiers: Sequence[Notifier]
    seen: SeenEvents
    client: httpx.Client

    def recent_groups(self, reports: Iterable[Earthquake], now: datetime) -> list[EventGroup]:
        """Reports young enough to matter, merged into one group per earthquake."""
        since = now - self.settings.max_event_age
        fresh = [report for report in reports if report.time >= since]
        return group_duplicates(fresh, self.settings.sources)

    def verdict(self, group: EventGroup) -> Verdict:
        """What should happen to one earthquake right now."""
        if not any(is_relevant(report, self.settings) for report in group.reports):
            return Verdict.IGNORE
        if any(report in self.seen for report in group.reports):
            return Verdict.SEEN
        return Verdict.ALERT

    def handle(
        self, reports: Iterable[Earthquake], now: datetime | None = None
    ) -> list[EventGroup]:
        """Alert on each new relevant earthquake in the reports; return those delivered."""
        now = now or datetime.now(UTC)
        delivered = [
            group
            for group in self.recent_groups(reports, now)
            if self.verdict(group) is Verdict.ALERT and self._deliver(group, now)
        ]
        self.seen.prune(self.settings.max_event_age * 2, now)
        return delivered

    def _deliver(self, group: EventGroup, now: datetime) -> bool:
        quake = group.primary
        log.info(
            "Earthquake detected %.0fs after origin: M%.1f %s at %s, reported by %s",
            quake.age(now).total_seconds(),
            quake.magnitude,
            quake.region or "(no region)",
            quake.time.isoformat(),
            group.sources,
        )
        title, message = format_alert(group, self.settings, now)
        if not send_all(self.notifiers, self.client, title, message):
            log.error("Alert for %s was not delivered; will retry on its next report", quake.key)
            return False
        self.seen.add(*group.reports)
        return True


def sleep_until(deadline: float, stop: threading.Event) -> None:
    """Wait for a wall-clock deadline, returning promptly after system sleep or on `stop`."""
    while (remaining := deadline - time.time()) > 0:
        if stop.wait(min(remaining, WAKE_CHECK_SECONDS)):
            return


def poll_once(
    settings: Settings, sources: Sequence[Source], client: httpx.Client, sink: Sink
) -> None:
    """Query every source once, handing over each source's reports as soon as it answers."""
    since = datetime.now(UTC) - settings.max_event_age
    failed = 0
    for result in fetch_each(sources, client, since, settings.bbox):
        if result.error:
            failed += 1
            log.warning("Source %s unavailable: %s", result.source, result.error)
        elif result.events:
            sink(result.events)
    if sources and failed == len(sources):
        log.error("Every source failed this cycle; will try again")


def poll_forever(
    settings: Settings,
    sources: Sequence[Source],
    client: httpx.Client,
    sink: Sink,
    stop: threading.Event,
) -> None:
    """Poll on a wall-clock schedule until `stop` is set, warning when cycles were missed."""
    interval = settings.poll_interval_seconds
    previous: float | None = None
    while not stop.is_set():
        started = time.time()
        if previous is not None and (gap := started - previous) > 2 * interval:
            log.warning("No poll for %.0fs (machine asleep?); alerts may have been delayed", gap)
        previous = started
        try:
            poll_once(settings, sources, client, sink)
        except Exception:
            log.exception("Polling cycle failed; continuing")
        sleep_until(started + interval, stop)


class FeedDiedError(RuntimeError):
    """A feed thread ended on its own; the supervisor should restart the process."""


def run(
    alerter: Alerter,
    sources: Sequence[Source],
    streams: Sequence[Stream],
    *,
    stop: threading.Event | None = None,
) -> None:
    """Run every feed in its own thread and alert on their reports until `stop` is set."""
    stop = stop if stop is not None else threading.Event()
    reports: Queue[list[Earthquake]] = Queue()
    settings = alerter.settings
    feeds = [
        threading.Thread(
            target=poll_forever,
            args=(settings, sources, alerter.client, reports.put, stop),
            name="poller",
            daemon=True,
        ),
        *(
            threading.Thread(
                target=stream.run, args=(reports.put, stop), name=stream.name, daemon=True
            )
            for stream in streams
        ),
    ]
    log.info(
        "Streaming %s; polling %s every %.0fs; alert when M>=%.1f%s; notifying via %s",
        ", ".join(s.name for s in streams) or "nothing",
        ", ".join(s.name for s in sources) or "nothing",
        settings.poll_interval_seconds,
        settings.min_magnitude,
        f" within {settings.radius_km:.0f} km" if settings.location else "",
        ", ".join(n.name for n in alerter.notifiers),
    )
    for feed in feeds:
        feed.start()
    try:
        while not stop.is_set():
            if dead := [feed.name for feed in feeds if not feed.is_alive()]:
                raise FeedDiedError(f"feed stopped unexpectedly: {', '.join(dead)}")
            try:
                batch = reports.get(timeout=FEED_CHECK_SECONDS)
            except Empty:
                continue
            try:
                alerter.handle(batch)
            except Exception:
                log.exception("Handling reports failed; continuing")
    finally:
        stop.set()
