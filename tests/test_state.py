from datetime import timedelta
from pathlib import Path

from eq_notifier.state import SeenEvents

from .conftest import NOW, make_quake


def test_seen_events_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    quake = make_quake()
    SeenEvents(path).add(quake)

    after_restart = SeenEvents(path)
    assert quake in after_restart
    assert make_quake(event_id="other") not in SeenEvents(tmp_path / "missing.json")


def test_report_from_another_source_counts_as_seen(tmp_path: Path) -> None:
    seen = SeenEvents(tmp_path / "seen.json")
    seen.add(make_quake(source="emsc", event_id="e1"))

    usgs_report = make_quake(
        source="usgs", event_id="u1", time=NOW - timedelta(minutes=3, seconds=30), magnitude=4.3
    )
    assert usgs_report in seen


def test_prune_forgets_old_events(tmp_path: Path) -> None:
    seen = SeenEvents(tmp_path / "seen.json")
    old = make_quake(event_id="old", time=NOW - timedelta(hours=5))
    fresh = make_quake(event_id="fresh")
    seen.add(old, fresh)

    seen.prune(timedelta(hours=2), now=NOW)

    assert old not in seen
    assert fresh in seen
    assert fresh in SeenEvents(seen.path)


def test_corrupt_state_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "seen.json"
    path.write_text("{not json")
    seen = SeenEvents(path)
    assert make_quake() not in seen
    seen.add(make_quake())
    assert make_quake() in SeenEvents(path)
