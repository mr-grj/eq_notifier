import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from eq_notifier.config import BoundingBox
from eq_notifier.events import Earthquake, Sink

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

# A typical Vrancea event, the seismic zone that matters most for Romania.
BASE_QUAKE = Earthquake(
    source="emsc",
    event_id="20260918_0000001",
    time=NOW - timedelta(minutes=3),
    magnitude=4.5,
    latitude=45.60,
    longitude=26.50,
    depth_km=120.0,
    region="ROMANIA",
)


def make_quake(**overrides: Any) -> Earthquake:
    return replace(BASE_QUAKE, **overrides)


@pytest.fixture
def mock_client() -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.Client]:
    def build(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return build


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))


class FakeSource:
    def __init__(
        self,
        name: str,
        events: list[Earthquake] | Exception,
        gate: threading.Event | None = None,
    ) -> None:
        self.name = name
        self.events = events
        self.gate = gate

    def fetch(self, client: httpx.Client, since: datetime, bbox: BoundingBox) -> list[Earthquake]:
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if isinstance(self.events, Exception):
            raise self.events
        return list(self.events)


class FakeNotifier:
    name = "fake"

    def __init__(self, fail: bool = False, on_send: threading.Event | None = None) -> None:
        self.fail = fail
        self.on_send = on_send
        self.sent: list[tuple[str, str]] = []

    def send(self, client: httpx.Client, title: str, message: str) -> None:
        if self.fail:
            raise httpx.ConnectError("down")
        self.sent.append((title, message))
        if self.on_send is not None:
            self.on_send.set()


class FakeStream:
    def __init__(self, reports: list[Earthquake], name: str = "fake-stream") -> None:
        self.name = name
        self.reports = reports

    def run(self, sink: Sink, stop: threading.Event) -> None:
        for report in self.reports:
            sink([report])
        stop.wait()

    def probe(self) -> str | None:
        return None
