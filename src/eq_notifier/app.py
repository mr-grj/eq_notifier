"""One polling cycle: fetch → normalise → deduplicate → decide → notify → remember."""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from eq_notifier import __version__
from eq_notifier.config import Settings
from eq_notifier.events import Earthquake, EventGroup, group_duplicates
from eq_notifier.notifiers import Notifier, send_all
from eq_notifier.sources import FetchResult, Source, fetch_all
from eq_notifier.state import SeenEvents

log = logging.getLogger(__name__)


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(10.0, connect=5.0),
        transport=httpx.HTTPTransport(retries=1),
        headers={
            "User-Agent": f"eq-notifier/{__version__} (https://github.com/mr-grj/eq_notifier)"
        },
    )


def is_relevant(event: Earthquake, settings: Settings) -> bool:
    if event.magnitude < settings.min_magnitude:
        return False
    if settings.location is None:
        return True
    distance = event.distance_km(settings.location.latitude, settings.location.longitude)
    return distance <= settings.radius_km


def format_alert(group: EventGroup, settings: Settings, now: datetime) -> tuple[str, str]:
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


@dataclass(frozen=True, slots=True)
class CycleReport:
    fetched: list[FetchResult]
    groups: list[EventGroup]
    alerts: list[EventGroup] = field(default_factory=list)


def poll_once(
    settings: Settings,
    sources: Sequence[Source],
    notifiers: Sequence[Notifier],
    seen: SeenEvents,
    client: httpx.Client,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CycleReport:
    """Run one cycle. With `dry_run`, decide but neither notify nor remember."""
    now = now or datetime.now(UTC)
    since = now - settings.max_event_age

    fetched = fetch_all(sources, client, since, settings.bbox)
    for result in fetched:
        if result.error:
            log.warning("Source %s unavailable: %s", result.source, result.error)
    if fetched and all(result.error for result in fetched):
        log.error("Every source failed this cycle; will try again")

    recent = [e for result in fetched for e in result.events if e.time >= since]
    groups = group_duplicates(recent, settings.sources)

    alerts: list[EventGroup] = []
    for group in groups:
        if not any(is_relevant(report, settings) for report in group.reports):
            continue
        if any(report in seen for report in group.reports):
            log.debug("Already alerted, ignoring %s", group.primary.key)
            continue
        if dry_run:
            alerts.append(group)
            continue
        quake = group.primary
        log.info(
            "Earthquake detected: M%.1f %s at %s, reported by %s",
            quake.magnitude,
            quake.region or "(no region)",
            quake.time.isoformat(),
            group.sources,
        )
        title, message = format_alert(group, settings, now)
        if send_all(notifiers, client, title, message):
            seen.add(*group.reports)
            alerts.append(group)
        else:
            log.error("Alert for %s was not delivered; will retry next cycle", quake.key)

    if not dry_run:
        seen.prune(settings.max_event_age * 2, now)
    return CycleReport(fetched=fetched, groups=groups, alerts=alerts)


def run_forever(
    settings: Settings,
    sources: Sequence[Source],
    notifiers: Sequence[Notifier],
    seen: SeenEvents,
    client: httpx.Client,
) -> None:
    log.info(
        "Polling %s every %.0fs; alert when M>=%.1f%s; notifying via %s",
        ", ".join(s.name for s in sources),
        settings.poll_interval_seconds,
        settings.min_magnitude,
        f" within {settings.radius_km:.0f} km" if settings.location else "",
        ", ".join(n.name for n in notifiers),
    )
    while True:
        started = time.monotonic()
        try:
            poll_once(settings, sources, notifiers, seen, client)
        except Exception:
            log.exception("Polling cycle failed; continuing")
        elapsed = time.monotonic() - started
        time.sleep(max(settings.poll_interval_seconds - elapsed, 0.0))
