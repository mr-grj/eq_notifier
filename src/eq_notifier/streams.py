"""Push feeds: stay connected and hand over each earthquake the moment it is published."""

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from eq_notifier import USER_AGENT
from eq_notifier.config import Settings
from eq_notifier.events import Earthquake, Sink
from eq_notifier.sources import describe_error, parse_emsc_feature

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 10.0
RECEIVE_TICK_SECONDS = 1.0
SILENCE_LIMIT_SECONDS = 600.0
RECONNECT_MIN_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 60.0


class Stream(Protocol):
    @property
    def name(self) -> str: ...

    def run(self, sink: Sink, stop: threading.Event) -> None: ...

    def probe(self) -> str | None: ...


def parse_emsc_message(raw: str | bytes) -> Earthquake | None:
    """Return the earthquake a stream message announces, or None for anything else."""
    try:
        message = json.loads(raw)
        if message["action"] not in ("create", "update"):
            return None
        return parse_emsc_feature(message["data"])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        log.warning("emsc-stream: skipping malformed message (%s)", describe_error(exc))
        return None


@dataclass(frozen=True, slots=True)
class EmscStream:
    """EMSC's real-time websocket: every new or revised event worldwide, as it is published."""

    url: str = "wss://www.seismicportal.eu/standing_order/websocket"
    name: str = "emsc-stream"

    def run(self, sink: Sink, stop: threading.Event) -> None:
        """Stay subscribed until `stop` is set, reconnecting with exponential backoff."""
        delay = RECONNECT_MIN_SECONDS
        while not stop.is_set():
            connected_at = time.monotonic()
            try:
                self.listen(sink, stop)
            except (OSError, WebSocketException) as exc:
                log.warning("%s disconnected: %s", self.name, describe_error(exc))
            except Exception:
                log.exception("%s failed", self.name)
            if time.monotonic() - connected_at > RECONNECT_MAX_SECONDS:
                delay = RECONNECT_MIN_SECONDS
            if stop.wait(delay):
                return
            delay = min(delay * 2, RECONNECT_MAX_SECONDS)

    def listen(self, sink: Sink, stop: threading.Event) -> None:
        """Hand over earthquakes from one connection until it drops, falls silent or stops."""
        with self._connect() as websocket:
            log.info("%s connected", self.name)
            last_message = time.monotonic()
            while not stop.is_set():
                try:
                    raw = websocket.recv(timeout=RECEIVE_TICK_SECONDS)
                except TimeoutError:
                    if time.monotonic() - last_message > SILENCE_LIMIT_SECONDS:
                        log.warning(
                            "%s silent for %.0fs; reconnecting", self.name, SILENCE_LIMIT_SECONDS
                        )
                        return
                    continue
                last_message = time.monotonic()
                if quake := parse_emsc_message(raw):
                    log.debug("%s: %s M%.1f", self.name, quake.key, quake.magnitude)
                    sink([quake])

    def probe(self) -> str | None:
        """Open and close one connection; return what went wrong, or None when healthy."""
        try:
            with self._connect():
                return None
        except (OSError, WebSocketException) as exc:
            return describe_error(exc)

    def _connect(self) -> ClientConnection:
        return connect(self.url, open_timeout=CONNECT_TIMEOUT_SECONDS, user_agent_header=USER_AGENT)


def build_streams(settings: Settings) -> list[Stream]:
    """The push feeds enabled in the settings."""
    return [EmscStream()] if settings.emsc_stream else []
