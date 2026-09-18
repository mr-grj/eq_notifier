from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from eq_notifier.events import Earthquake

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
