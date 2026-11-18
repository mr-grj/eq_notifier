"""Polled feeds: agency FDSN event services, queried concurrently on a wall-clock schedule.

To add a source: name it in `SourceName`, subclass `FdsnSource` and register it in `SOURCES`.
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from eq_notifier.config import BoundingBox, Settings, SourceName
from eq_notifier.events import Earthquake, Sink

log = logging.getLogger(__name__)

FETCH_LIMIT = 100
WAKE_CHECK_SECONDS = 1.0


class SourceError(Exception):
    """The source answered, but not with data we can use."""


class Source(Protocol):
    @property
    def name(self) -> str: ...

    def fetch(
        self, client: httpx.Client, since: datetime, bbox: BoundingBox
    ) -> list[Earthquake]: ...


def describe_error(exc: BaseException) -> str:
    """Short error text for logs: the exception type plus its message, if any."""
    kind = type(exc).__name__
    return f"{kind}: {exc}" if str(exc) else kind


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_emsc_feature(feature: dict[str, Any]) -> Earthquake:
    """Normalise one EMSC GeoJSON feature, as served by both the FDSN API and the stream."""
    props = feature["properties"]
    return Earthquake(
        source=SourceName.EMSC,
        event_id=str(props["unid"]),
        time=_parse_utc(props["time"]),
        latitude=float(props["lat"]),
        longitude=float(props["lon"]),
        depth_km=float(props["depth"]) if props.get("depth") is not None else None,
        magnitude=float(props["mag"]),
        region=str(props.get("flynn_region") or ""),
        url=f"https://www.seismicportal.eu/eventdetails.html?unid={props['unid']}",
    )


class FdsnSource[R](ABC):
    """An FDSN event web service; agencies differ only in response format and record shape."""

    name: SourceName
    url: str
    format: str

    def fetch(self, client: httpx.Client, since: datetime, bbox: BoundingBox) -> list[Earthquake]:
        """Recent events inside the box, skipping records that cannot be read."""
        params = {
            "format": self.format,
            "orderby": "time",
            "limit": FETCH_LIMIT,
            "starttime": since.strftime("%Y-%m-%dT%H:%M:%S"),
            "minlatitude": bbox.min_latitude,
            "maxlatitude": bbox.max_latitude,
            "minlongitude": bbox.min_longitude,
            "maxlongitude": bbox.max_longitude,
        }
        response = client.get(self.url, params=params)
        if response.status_code == 204:
            return []
        if response.is_error:
            raise SourceError(f"HTTP {response.status_code}")
        events: list[Earthquake] = []
        for record in self.records(response):
            try:
                events.append(self.normalise(record))
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                log.warning("%s: skipping malformed record (%s)", self.name, exc)
        return events

    @abstractmethod
    def records(self, response: httpx.Response) -> list[R]:
        """The raw event records in a successful response."""

    @abstractmethod
    def normalise(self, record: R) -> Earthquake:
        """One raw record as an earthquake report."""


class GeoJsonSource(FdsnSource[dict[str, Any]]):
    """An FDSN event service answering with a GeoJSON FeatureCollection."""

    def records(self, response: httpx.Response) -> list[dict[str, Any]]:
        try:
            body = response.json()
        except ValueError as exc:
            raise SourceError(f"response is not JSON: {exc}") from exc
        if not isinstance(body, dict) or not isinstance(body.get("features"), list):
            raise SourceError("response is not a GeoJSON FeatureCollection")
        return body["features"]


class InfpSource(FdsnSource[str]):
    """INFP (Romania's National Institute for Earth Physics) FDSN event service.

    The service only offers QuakeML and the pipe-separated FDSN text format; text is
    far simpler to parse and carries everything we need.
    """

    name = SourceName.INFP
    url = "https://eida-sc3.infp.ro/fdsnws/event/1/query"
    format = "text"

    def records(self, response: httpx.Response) -> list[str]:
        return [line for line in response.text.splitlines() if line and not line.startswith("#")]

    def normalise(self, record: str) -> Earthquake:
        columns = record.split("|")
        if len(columns) < 13:
            raise ValueError(f"expected 13+ columns, got {len(columns)}")
        depth = columns[4].strip()
        return Earthquake(
            source=self.name,
            event_id=columns[0].strip(),
            time=_parse_utc(columns[1].strip()),
            latitude=float(columns[2]),
            longitude=float(columns[3]),
            depth_km=float(depth) if depth else None,
            magnitude=float(columns[10]),
            region=columns[12].strip(),
        )


class EmscSource(GeoJsonSource):
    """EMSC (European-Mediterranean Seismological Centre) FDSN event service."""

    name = SourceName.EMSC
    url = "https://www.seismicportal.eu/fdsnws/event/1/query"
    format = "json"

    def normalise(self, record: dict[str, Any]) -> Earthquake:
        return parse_emsc_feature(record)


class UsgsSource(GeoJsonSource):
    """USGS earthquake catalog FDSN event service."""

    name = SourceName.USGS
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"
    format = "geojson"

    def normalise(self, record: dict[str, Any]) -> Earthquake:
        props = record["properties"]
        longitude, latitude, depth = record["geometry"]["coordinates"][:3]
        return Earthquake(
            source=self.name,
            event_id=str(record["id"]),
            time=datetime.fromtimestamp(props["time"] / 1000, tz=UTC),
            latitude=float(latitude),
            longitude=float(longitude),
            depth_km=float(depth) if depth is not None else None,
            magnitude=float(props["mag"]),
            region=str(props.get("place") or ""),
            url=props.get("url"),
        )


SOURCES: dict[SourceName, Source] = {
    source.name: source for source in (InfpSource(), EmscSource(), UsgsSource())
}


def build_sources(settings: Settings) -> list[Source]:
    """The polled feeds enabled in the settings, in priority order."""
    return [SOURCES[name] for name in settings.sources]


@dataclass(frozen=True, slots=True)
class FetchResult:
    source: str
    events: list[Earthquake] = field(default_factory=list)
    error: str | None = None


def _fetch_one(
    source: Source, client: httpx.Client, since: datetime, bbox: BoundingBox
) -> FetchResult:
    try:
        return FetchResult(source.name, source.fetch(client, since, bbox))
    except (httpx.HTTPError, SourceError) as exc:
        return FetchResult(source.name, error=describe_error(exc))


def fetch_each(
    sources: Sequence[Source], client: httpx.Client, since: datetime, bbox: BoundingBox
) -> Iterator[FetchResult]:
    """Query every source concurrently and yield each result the moment it arrives."""
    if not sources:
        return
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        futures = [pool.submit(_fetch_one, source, client, since, bbox) for source in sources]
        for future in as_completed(futures):
            yield future.result()


def fetch_all(
    sources: Sequence[Source], client: httpx.Client, since: datetime, bbox: BoundingBox
) -> list[FetchResult]:
    """Query every source concurrently and return the results in source order."""
    order = {source.name: position for position, source in enumerate(sources)}
    return sorted(fetch_each(sources, client, since, bbox), key=lambda r: order[r.source])


def sleep_until(deadline: float, stop: threading.Event) -> None:
    """Wait for a wall-clock deadline, returning promptly after system sleep or on `stop`."""
    while (remaining := deadline - time.time()) > 0:
        if stop.wait(min(remaining, WAKE_CHECK_SECONDS)):
            return


@dataclass(frozen=True, slots=True)
class Poller:
    """The polled sources as one feed, queried on a wall-clock schedule."""

    settings: Settings
    sources: Sequence[Source]
    client: httpx.Client
    name: str = "poller"

    def run(self, sink: Sink, stop: threading.Event) -> None:
        """Poll until `stop` is set, warning when cycles were missed."""
        interval = self.settings.poll_interval_seconds
        names = ", ".join(source.name for source in self.sources) or "nothing"
        log.info("Polling %s every %.0fs", names, interval)
        previous: float | None = None
        while not stop.is_set():
            started = time.time()
            if previous is not None and (gap := started - previous) > 2 * interval:
                log.warning(
                    "No poll for %.0fs (machine asleep?); alerts may have been delayed", gap
                )
            previous = started
            try:
                self.poll_once(sink)
            except Exception:
                log.exception("Polling cycle failed; continuing")
            sleep_until(started + interval, stop)

    def poll_once(self, sink: Sink) -> None:
        """Query every source once, handing over each source's reports as soon as it answers."""
        since = datetime.now(UTC) - self.settings.max_event_age
        failed = 0
        for result in fetch_each(self.sources, self.client, since, self.settings.bbox):
            if result.error:
                failed += 1
                log.warning("Source %s unavailable: %s", result.source, result.error)
            elif result.events:
                sink(result.events)
        if self.sources and failed == len(self.sources):
            log.error("Every source failed this cycle; will try again")
