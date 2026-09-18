import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

EARTH_RADIUS_KM = 6371.0

# Two reports are treated as the same earthquake when both origin time and epicentre
# agree within these tolerances. Agencies routinely differ by tens of seconds and tens
# of kilometres (especially for deep Vrancea events), so these are deliberately loose.
SAME_EVENT_TIME_TOLERANCE = timedelta(seconds=90)
SAME_EVENT_DISTANCE_KM = 100.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass(frozen=True, slots=True, kw_only=True)
class Earthquake:
    """One provider's report of an earthquake, normalised to a common shape."""

    source: str
    event_id: str
    time: datetime
    magnitude: float
    latitude: float
    longitude: float
    depth_km: float | None
    region: str
    url: str | None = None

    def __post_init__(self) -> None:
        if self.time.tzinfo is None:
            raise ValueError("Earthquake.time must be timezone-aware")

    @property
    def key(self) -> str:
        return f"{self.source}:{self.event_id}"

    def distance_km(self, latitude: float, longitude: float) -> float:
        return haversine_km(self.latitude, self.longitude, latitude, longitude)

    def age(self, now: datetime | None = None) -> timedelta:
        return (now or datetime.now(UTC)) - self.time

    def is_same_event(self, other: Earthquake) -> bool:
        if self.key == other.key:
            return True
        close_in_time = abs(self.time - other.time) <= SAME_EVENT_TIME_TOLERANCE
        return close_in_time and (
            self.distance_km(other.latitude, other.longitude) <= SAME_EVENT_DISTANCE_KM
        )


@dataclass(frozen=True, slots=True)
class EventGroup:
    """All reports of one physical earthquake; `primary` comes from the highest-priority source."""

    primary: Earthquake
    reports: tuple[Earthquake, ...]

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(report.source for report in self.reports)


def group_duplicates(
    events: Iterable[Earthquake], source_priority: Sequence[str]
) -> list[EventGroup]:
    """Merge reports of the same earthquake from different sources into one group each.

    Reports are visited in source-priority order, so the first report of a physical event
    becomes the group's primary and later reports from lower-priority sources attach to it.
    Within a source, newer events come first.
    """

    def rank(event: Earthquake) -> tuple[int, float]:
        position = (
            source_priority.index(event.source)
            if event.source in source_priority
            else len(source_priority)
        )
        return (position, -event.time.timestamp())

    groups: list[list[Earthquake]] = []
    for event in sorted(events, key=rank):
        for group in groups:
            if group[0].is_same_event(event):
                group.append(event)
                break
        else:
            groups.append([event])
    return [EventGroup(primary=group[0], reports=tuple(group)) for group in groups]
