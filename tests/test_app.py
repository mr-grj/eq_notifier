from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from eq_notifier.app import format_alert, poll_once
from eq_notifier.config import BoundingBox, Location, Settings
from eq_notifier.events import Earthquake, EventGroup
from eq_notifier.sources import SourceError
from eq_notifier.state import SeenEvents

from .conftest import NOW, make_quake


class FakeSource:
    def __init__(self, name: str, events: list[Earthquake] | Exception) -> None:
        self.name = name
        self.events = events

    def fetch(self, client: httpx.Client, since: datetime, bbox: BoundingBox) -> list[Earthquake]:
        if isinstance(self.events, Exception):
            raise self.events
        return list(self.events)


class FakeNotifier:
    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    def send(self, client: httpx.Client, title: str, message: str) -> None:
        if self.fail:
            raise httpx.ConnectError("down")
        self.sent.append((title, message))


@pytest.fixture
def seen(tmp_path: Path) -> SeenEvents:
    return SeenEvents(tmp_path / "seen.json")


@pytest.fixture
def client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))


SETTINGS = Settings(sources=("infp", "emsc", "usgs"), notifiers=("fake",))


def test_same_quake_from_three_sources_is_one_alert_and_only_once(seen, client) -> None:
    sources = [
        FakeSource("infp", [make_quake(source="infp", event_id="i1", magnitude=4.4)]),
        FakeSource("emsc", [make_quake(source="emsc", event_id="e1", magnitude=4.6)]),
        FakeSource("usgs", [make_quake(source="usgs", event_id="u1", magnitude=4.5)]),
    ]
    notifier = FakeNotifier()

    first = poll_once(SETTINGS, sources, [notifier], seen, client, now=NOW)
    second = poll_once(SETTINGS, sources, [notifier], seen, client, now=NOW + timedelta(seconds=30))

    assert len(first.alerts) == 1 and first.alerts[0].sources == ("infp", "emsc", "usgs")
    assert second.alerts == []
    [(title, message)] = notifier.sent
    assert title.startswith("Earthquake M4.4")
    assert "infp (M4.4), emsc (M4.6), usgs (M4.5)" in message


def test_restart_does_not_resend_the_same_alert(tmp_path: Path, client) -> None:
    path = tmp_path / "seen.json"
    sources = [FakeSource("emsc", [make_quake()])]
    poll_once(SETTINGS, sources, [FakeNotifier()], SeenEvents(path), client, now=NOW)

    notifier = FakeNotifier()
    later = [FakeSource("usgs", [make_quake(source="usgs", event_id="u9", magnitude=4.7)])]
    poll_once(SETTINGS, later, [notifier], SeenEvents(path), client, now=NOW + timedelta(minutes=2))

    assert notifier.sent == []


def test_below_threshold_stale_and_far_events_are_ignored(seen, client) -> None:
    settings = Settings(
        location=Location(44.43, 26.10), radius_km=150, notifiers=("fake",), sources=("emsc",)
    )
    reports = [
        make_quake(event_id="weak", magnitude=3.9),
        make_quake(event_id="stale", time=NOW - timedelta(hours=2)),
        make_quake(event_id="far", latitude=47.9, longitude=21.8, time=NOW - timedelta(minutes=20)),
        make_quake(event_id="near", time=NOW - timedelta(minutes=40)),
    ]
    notifier = FakeNotifier()

    report = poll_once(settings, [FakeSource("emsc", reports)], [notifier], seen, client, now=NOW)

    assert [g.primary.event_id for g in report.alerts] == ["near"]
    assert "Distance from you: 134 km" in notifier.sent[0][1]


def test_one_source_down_does_not_block_alerts_from_the_others(seen, client) -> None:
    sources = [
        FakeSource("infp", SourceError("HTTP 503")),
        FakeSource("emsc", httpx.ReadTimeout("slow")),
        FakeSource("usgs", [make_quake(source="usgs", event_id="u1")]),
    ]
    notifier = FakeNotifier()

    report = poll_once(SETTINGS, sources, [notifier], seen, client, now=NOW)

    assert [r.error is None for r in report.fetched] == [False, False, True]
    assert len(notifier.sent) == 1


def test_failed_delivery_is_retried_next_cycle(seen, client) -> None:
    sources = [FakeSource("emsc", [make_quake()])]
    broken, working = FakeNotifier(fail=True), FakeNotifier()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("eq_notifier.notifiers.time.sleep", lambda s: None)
        first = poll_once(SETTINGS, sources, [broken], seen, client, now=NOW)
    second = poll_once(SETTINGS, sources, [working], seen, client, now=NOW + timedelta(seconds=30))

    assert first.alerts == []
    assert len(second.alerts) == 1
    assert len(working.sent) == 1


def test_dry_run_decides_but_neither_sends_nor_remembers(seen, client) -> None:
    sources = [FakeSource("emsc", [make_quake()])]
    notifier = FakeNotifier()

    report = poll_once(SETTINGS, sources, [notifier], seen, client, now=NOW, dry_run=True)
    again = poll_once(SETTINGS, sources, [notifier], seen, client, now=NOW, dry_run=True)

    assert len(report.alerts) == len(again.alerts) == 1
    assert notifier.sent == []


def test_alert_text_is_informative() -> None:
    quake = make_quake(url="https://example.org/e1")
    title, message = format_alert(EventGroup(primary=quake, reports=(quake,)), Settings(), NOW)
    assert title == "Earthquake M4.5 - ROMANIA"
    assert "depth 120 km" in message
    assert "Origin time: 2026-09-18 11:57:00 UTC (3 min ago)" in message
    assert message.endswith("https://example.org/e1")
