import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from eq_notifier.app import Alerter, FeedDiedError, format_alert, run
from eq_notifier.config import Location, Settings, SourceName
from eq_notifier.events import EventGroup, Sink
from eq_notifier.state import SeenEvents

from .conftest import NOW, FakeNotifier, FakeStream, make_quake

SETTINGS = Settings()


class DyingStream(FakeStream):
    def run(self, sink: Sink, stop: threading.Event) -> None:
        return None


@pytest.fixture
def seen(tmp_path: Path) -> SeenEvents:
    return SeenEvents(tmp_path / "seen.json")


def make_alerter(seen: SeenEvents, client: httpx.Client, *notifiers, settings=SETTINGS) -> Alerter:
    return Alerter(settings, notifiers, seen, client)


def test_same_quake_from_three_sources_is_one_alert_and_only_once(seen, client, caplog) -> None:
    caplog.set_level(logging.INFO)
    notifier = FakeNotifier()
    alerter = make_alerter(seen, client, notifier)
    batch = [
        make_quake(source=SourceName.USGS, event_id="u1", magnitude=4.5),
        make_quake(source=SourceName.INFP, event_id="i1", magnitude=4.4),
        make_quake(source=SourceName.EMSC, event_id="e1", magnitude=4.6),
    ]

    first = alerter.handle(batch, NOW)
    second = alerter.handle([make_quake(source="usgs", event_id="u1", magnitude=4.7)], NOW)

    assert [group.sources for group in first] == [("infp", "emsc", "usgs")]
    assert second == []
    [(title, message)] = notifier.sent
    assert title.startswith("Earthquake M4.4")
    assert "infp (M4.4), emsc (M4.6), usgs (M4.5)" in message
    assert "reported by infp, emsc, usgs" in caplog.text


def test_first_report_alerts_and_slower_feeds_never_repeat_it(seen, client) -> None:
    notifier = FakeNotifier()
    alerter = make_alerter(seen, client, notifier)

    streamed = alerter.handle([make_quake(source="emsc", event_id="e1")], NOW)
    polled = [
        make_quake(source="emsc", event_id="e1", magnitude=4.7),
        make_quake(source="usgs", event_id="u1", time=NOW - timedelta(minutes=3, seconds=40)),
    ]
    later = alerter.handle(polled, NOW + timedelta(seconds=30))

    assert len(streamed) == 1 and later == []
    assert len(notifier.sent) == 1


def test_restart_does_not_resend_the_same_alert(tmp_path: Path, client) -> None:
    path = tmp_path / "seen.json"
    make_alerter(SeenEvents(path), client, FakeNotifier()).handle([make_quake()], NOW)

    notifier = FakeNotifier()
    late_report = make_quake(source="usgs", event_id="u9", magnitude=4.7)
    make_alerter(SeenEvents(path), client, notifier).handle([late_report], NOW)

    assert notifier.sent == []


def test_weak_stale_and_far_events_are_ignored(seen, client) -> None:
    settings = Settings(location=Location(44.43, 26.10), radius_km=150, sources=(SourceName.EMSC,))
    notifier = FakeNotifier()
    reports = [
        make_quake(event_id="weak", magnitude=3.9),
        make_quake(event_id="stale", time=NOW - timedelta(hours=2)),
        make_quake(event_id="far", latitude=47.9, longitude=21.8, time=NOW - timedelta(minutes=20)),
        make_quake(event_id="near", time=NOW - timedelta(minutes=40)),
    ]

    alerts = make_alerter(seen, client, notifier, settings=settings).handle(reports, NOW)

    assert [group.primary.event_id for group in alerts] == ["near"]
    assert "Distance from you: 134 km" in notifier.sent[0][1]


def test_without_a_location_only_the_romania_region_alerts(seen, client) -> None:
    notifier = FakeNotifier()
    japan = make_quake(event_id="jp", latitude=32.3, longitude=130.4, magnitude=6.5)
    vrancea = make_quake(event_id="ro")

    alerts = make_alerter(seen, client, notifier).handle([japan, vrancea], NOW)

    assert [group.primary.event_id for group in alerts] == ["ro"]


def test_revised_magnitude_crossing_the_threshold_alerts_once(seen, client) -> None:
    notifier = FakeNotifier()
    alerter = make_alerter(seen, client, notifier)

    first = alerter.handle([make_quake(magnitude=3.8)], NOW)
    revised = alerter.handle([make_quake(magnitude=4.2)], NOW + timedelta(seconds=40))
    again = alerter.handle([make_quake(magnitude=4.3)], NOW + timedelta(seconds=80))

    assert (len(first), len(revised), len(again)) == (0, 1, 0)


def test_failed_delivery_is_retried_on_the_next_report(seen, client, monkeypatch) -> None:
    monkeypatch.setattr("eq_notifier.notifiers.time.sleep", lambda s: None)
    working = FakeNotifier()

    failed = make_alerter(seen, client, FakeNotifier(fail=True)).handle([make_quake()], NOW)
    retried = make_alerter(seen, client, working).handle([make_quake()], NOW)

    assert failed == [] and len(retried) == 1
    assert len(working.sent) == 1


def test_alert_text_is_informative() -> None:
    quake = make_quake(url="https://example.org/e1")
    title, message = format_alert(EventGroup((quake,)), Settings(), NOW)
    assert title == "Earthquake M4.5 - ROMANIA"
    assert "depth 120 km" in message
    assert "Origin time: 2026-09-18 11:57:00 UTC (3 min ago)" in message
    assert message.endswith("https://example.org/e1")


def test_run_delivers_streamed_reports_through_the_alerter(seen, client) -> None:
    stop = threading.Event()
    notifier = FakeNotifier(on_send=stop)
    just_now = datetime.now(UTC) - timedelta(seconds=20)
    stream = FakeStream([make_quake(magnitude=3.0, time=just_now), make_quake(time=just_now)])

    run(make_alerter(seen, client, notifier), [stream], stop=stop)

    [(title, _)] = notifier.sent
    assert title == "Earthquake M4.5 - ROMANIA"
    assert make_quake(time=just_now) in seen


def test_run_fails_fast_when_a_feed_dies(seen, client) -> None:
    alerter = make_alerter(seen, client, FakeNotifier())
    with pytest.raises(FeedDiedError, match="dying-stream"):
        run(alerter, [DyingStream([], name="dying-stream")])
