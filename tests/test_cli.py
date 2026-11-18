from datetime import UTC, datetime, timedelta

import httpx
import pytest

from eq_notifier.app import Alerter
from eq_notifier.cli import check, main
from eq_notifier.config import Settings
from eq_notifier.sources import SourceError
from eq_notifier.state import SeenEvents

from .conftest import make_quake
from .test_app import FakeSource, FakeStream


def test_run_without_a_notifier_fails_clearly(capsys, tmp_path) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--env-file", str(tmp_path / "none.env"), "run"])
    assert info.value.code == 2
    assert "EQ_NOTIFIERS" in capsys.readouterr().err


def test_invalid_configuration_lists_problems(capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EQ_NOTIFIERS", "telegram")
    monkeypatch.setenv("EQ_MIN_MAGNITUDE", "loud")
    with pytest.raises(SystemExit) as info:
        main(["--env-file", str(tmp_path / "none.env"), "check"])
    err = capsys.readouterr().err
    assert info.value.code == 2
    assert "TELEGRAM_BOT_TOKEN is required" in err
    assert "EQ_MIN_MAGNITUDE must be a number" in err


def test_check_shows_feed_health_and_verdicts_without_sending(capsys, tmp_path) -> None:
    recent = datetime.now(UTC) - timedelta(minutes=5)
    seen = SeenEvents(tmp_path / "seen.json")
    seen.add(make_quake(event_id="old-news", time=recent - timedelta(minutes=20)))
    sources = [
        FakeSource("infp", SourceError("HTTP 502")),
        FakeSource(
            "emsc",
            [
                make_quake(event_id="new", time=recent),
                make_quake(event_id="old-news", time=recent - timedelta(minutes=20)),
                make_quake(event_id="weak", time=recent - timedelta(minutes=10), magnitude=2.0),
            ],
        ),
    ]
    with httpx.Client() as client:
        alerter = Alerter(Settings(sources=("infp", "emsc")), [], seen, client)
        check(alerter, sources, [FakeStream([])])

    out = capsys.readouterr().out
    assert "infp         ERROR SourceError: HTTP 502" in out
    assert "emsc         ok, 3 events" in out
    assert "fake-stream  ok, connected" in out
    assert "5 min ago via emsc - would alert" in out
    assert "via emsc - already alerted" in out
    assert "via emsc - ignored" in out
