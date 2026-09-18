"""Earthquake data sources. Each source fetches recent events and normalises them.

To add a source: implement the `Source` protocol and register it in `SOURCES`.
"""

import logging
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from eq_notifier.config import BoundingBox
from eq_notifier.events import Earthquake

log = logging.getLogger(__name__)

FETCH_LIMIT = 100


class SourceError(Exception):
    """The source answered, but not with data we can use."""


class Source(Protocol):
    @property
    def name(self) -> str: ...

    def fetch(
        self, client: httpx.Client, since: datetime, bbox: BoundingBox
    ) -> list[Earthquake]: ...


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> httpx.Response | None:
    """Return the response, or None for the FDSN "204 No Content" convention."""
    response = client.get(url, params=params)
    if response.status_code == 204:
        return None
    if response.is_error:
        raise SourceError(f"HTTP {response.status_code}")
    return response


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(f"response is not JSON: {exc}") from exc


def _normalise_all[T](
    name: str, raw_items: Iterable[T], normalise: Callable[[T], Earthquake]
) -> list[Earthquake]:
    events: list[Earthquake] = []
    for item in raw_items:
        try:
            events.append(normalise(item))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            log.warning("%s: skipping malformed record (%s)", name, exc)
    return events


class InfpSource:
    """INFP (Romania's National Institute for Earth Physics) FDSN event service.

    The service only offers QuakeML and the pipe-separated FDSN text format; text is
    far simpler to parse and carries everything we need.
    """

    name = "infp"
    url = "https://eida-sc3.infp.ro/fdsnws/event/1/query"

    def fetch(self, client: httpx.Client, since: datetime, bbox: BoundingBox) -> list[Earthquake]:
        params = {
            "format": "text",
            "orderby": "time",
            "limit": FETCH_LIMIT,
            "starttime": since.strftime("%Y-%m-%dT%H:%M:%S"),
            "minlat": bbox.min_latitude,
            "maxlat": bbox.max_latitude,
            "minlon": bbox.min_longitude,
            "maxlon": bbox.max_longitude,
        }
        if (response := _get(client, self.url, params)) is None:
            return []
        lines = [line for line in response.text.splitlines() if line and not line.startswith("#")]
        return _normalise_all(self.name, lines, self._normalise)

    def _normalise(self, line: str) -> Earthquake:
        columns = line.split("|")
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


class EmscSource:
    """EMSC (European-Mediterranean Seismological Centre) FDSN event service, GeoJSON."""

    name = "emsc"
    url = "https://www.seismicportal.eu/fdsnws/event/1/query"

    def fetch(self, client: httpx.Client, since: datetime, bbox: BoundingBox) -> list[Earthquake]:
        params = {
            "format": "json",
            "orderby": "time",
            "limit": FETCH_LIMIT,
            "starttime": since.strftime("%Y-%m-%dT%H:%M:%S"),
            "minlat": bbox.min_latitude,
            "maxlat": bbox.max_latitude,
            "minlon": bbox.min_longitude,
            "maxlon": bbox.max_longitude,
        }
        if (response := _get(client, self.url, params)) is None:
            return []
        return _normalise_all(self.name, _features(_json(response)), self._normalise)

    def _normalise(self, feature: dict[str, Any]) -> Earthquake:
        props = feature["properties"]
        return Earthquake(
            source=self.name,
            event_id=str(props["unid"]),
            time=_parse_utc(props["time"]),
            latitude=float(props["lat"]),
            longitude=float(props["lon"]),
            depth_km=float(props["depth"]) if props.get("depth") is not None else None,
            magnitude=float(props["mag"]),
            region=str(props.get("flynn_region") or ""),
            url=f"https://www.seismicportal.eu/eventdetails.html?unid={props['unid']}",
        )


class UsgsSource:
    """USGS earthquake catalog FDSN event service, GeoJSON."""

    name = "usgs"
    url = "https://earthquake.usgs.gov/fdsnws/event/1/query"

    def fetch(self, client: httpx.Client, since: datetime, bbox: BoundingBox) -> list[Earthquake]:
        params = {
            "format": "geojson",
            "orderby": "time",
            "limit": FETCH_LIMIT,
            "starttime": since.strftime("%Y-%m-%dT%H:%M:%S"),
            "minlatitude": bbox.min_latitude,
            "maxlatitude": bbox.max_latitude,
            "minlongitude": bbox.min_longitude,
            "maxlongitude": bbox.max_longitude,
        }
        if (response := _get(client, self.url, params)) is None:
            return []
        return _normalise_all(self.name, _features(_json(response)), self._normalise)

    def _normalise(self, feature: dict[str, Any]) -> Earthquake:
        props = feature["properties"]
        longitude, latitude, depth = feature["geometry"]["coordinates"][:3]
        return Earthquake(
            source=self.name,
            event_id=str(feature["id"]),
            time=datetime.fromtimestamp(props["time"] / 1000, tz=UTC),
            latitude=float(latitude),
            longitude=float(longitude),
            depth_km=float(depth) if depth is not None else None,
            magnitude=float(props["mag"]),
            region=str(props.get("place") or ""),
            url=props.get("url"),
        )


def _features(body: Any) -> list[Any]:
    if not isinstance(body, dict) or not isinstance(body.get("features"), list):
        raise SourceError("response is not a GeoJSON FeatureCollection")
    return body["features"]


SOURCES: dict[str, Source] = {
    source.name: source for source in (InfpSource(), EmscSource(), UsgsSource())
}


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
        detail = str(exc)
        kind = type(exc).__name__
        return FetchResult(source.name, error=f"{kind}: {detail}" if detail else kind)


def fetch_all(
    sources: Sequence[Source], client: httpx.Client, since: datetime, bbox: BoundingBox
) -> list[FetchResult]:
    """Query every source concurrently; a failing source yields an error, never an exception."""
    if not sources:
        return []
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        return list(pool.map(lambda source: _fetch_one(source, client, since, bbox), sources))
