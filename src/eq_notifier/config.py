import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

KNOWN_SOURCES = ("infp", "emsc", "usgs")
KNOWN_NOTIFIERS = ("ntfy", "telegram", "twilio")

DEFAULT_STATE_FILE = Path.home() / ".local" / "state" / "eq-notifier" / "seen-events.json"


class ConfigError(Exception):
    """Configuration is missing or invalid; the message lists every problem found."""


@dataclass(frozen=True, slots=True)
class Location:
    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class BoundingBox:
    min_latitude: float
    max_latitude: float
    min_longitude: float
    max_longitude: float


# Romania plus a margin, used when no user location is configured.
ROMANIA_BBOX = BoundingBox(
    min_latitude=42.5, max_latitude=49.0, min_longitude=19.5, max_longitude=31.0
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Settings:
    min_magnitude: float = 4.0
    location: Location | None = None
    radius_km: float = 300.0
    poll_interval_seconds: float = 30.0
    max_event_age: timedelta = timedelta(minutes=60)
    sources: tuple[str, ...] = KNOWN_SOURCES
    notifiers: tuple[str, ...] = ()
    state_file: Path = DEFAULT_STATE_FILE
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from: str = ""
    twilio_to: str = ""

    @property
    def bbox(self) -> BoundingBox:
        """Query window for the feeds: the alert radius around the user, else Romania."""
        if self.location is None:
            return ROMANIA_BBOX
        lat, lon = self.location.latitude, self.location.longitude
        dlat = self.radius_km / 111.0
        dlon = self.radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.1))
        return BoundingBox(
            min_latitude=max(lat - dlat, -90.0),
            max_latitude=min(lat + dlat, 90.0),
            min_longitude=max(lon - dlon, -180.0),
            max_longitude=min(lon + dlon, 180.0),
        )

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        problems: list[str] = []

        def number(name: str, default: float) -> float:
            raw = env.get(name, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                problems.append(f"{name} must be a number, got {raw!r}")
                return default

        def names(name: str, default: tuple[str, ...], known: tuple[str, ...]) -> tuple[str, ...]:
            raw = env.get(name, "").strip()
            if not raw:
                return default
            chosen = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
            if unknown := [n for n in chosen if n not in known]:
                problems.append(
                    f"{name} contains unknown names {unknown}; choose from {list(known)}"
                )
            return chosen

        def credential(name: str, notifier: str) -> str:
            value = env.get(name, "").strip()
            if notifier in notifiers and not value:
                problems.append(f"{name} is required when {notifier} is enabled")
            return value

        location = None
        lat_raw, lon_raw = env.get("EQ_LATITUDE", "").strip(), env.get("EQ_LONGITUDE", "").strip()
        if lat_raw or lon_raw:
            if not (lat_raw and lon_raw):
                problems.append("EQ_LATITUDE and EQ_LONGITUDE must be set together")
            else:
                location = Location(number("EQ_LATITUDE", 0.0), number("EQ_LONGITUDE", 0.0))
                if not (-90 <= location.latitude <= 90 and -180 <= location.longitude <= 180):
                    problems.append("EQ_LATITUDE/EQ_LONGITUDE are out of range")

        notifiers = names("EQ_NOTIFIERS", (), KNOWN_NOTIFIERS)
        settings = cls(
            min_magnitude=number("EQ_MIN_MAGNITUDE", 4.0),
            location=location,
            radius_km=number("EQ_RADIUS_KM", 300.0),
            poll_interval_seconds=number("EQ_POLL_INTERVAL_SECONDS", 30.0),
            max_event_age=timedelta(minutes=number("EQ_MAX_EVENT_AGE_MINUTES", 60.0)),
            sources=names("EQ_SOURCES", KNOWN_SOURCES, KNOWN_SOURCES),
            notifiers=notifiers,
            state_file=Path(
                env.get("EQ_STATE_FILE", "").strip() or DEFAULT_STATE_FILE
            ).expanduser(),
            ntfy_server=env.get("NTFY_SERVER", "").strip().rstrip("/") or "https://ntfy.sh",
            ntfy_topic=credential("NTFY_TOPIC", "ntfy"),
            ntfy_token=env.get("NTFY_TOKEN", "").strip(),
            telegram_bot_token=credential("TELEGRAM_BOT_TOKEN", "telegram"),
            telegram_chat_id=credential("TELEGRAM_CHAT_ID", "telegram"),
            twilio_account_sid=credential("TWILIO_ACCOUNT_SID", "twilio"),
            twilio_auth_token=credential("TWILIO_AUTH_TOKEN", "twilio"),
            twilio_from=credential("TWILIO_FROM", "twilio"),
            twilio_to=credential("TWILIO_TO", "twilio"),
        )

        if settings.poll_interval_seconds < 5:
            problems.append("EQ_POLL_INTERVAL_SECONDS must be at least 5 (be kind to public feeds)")
        if settings.radius_km <= 0:
            problems.append("EQ_RADIUS_KM must be positive")
        if settings.max_event_age <= timedelta(0):
            problems.append("EQ_MAX_EVENT_AGE_MINUTES must be positive")
        if problems:
            raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(problems))
        return settings
