from datetime import timedelta

from eq_notifier.events import group_duplicates, haversine_km

from .conftest import make_quake


def test_haversine_bucharest_to_cluj_is_about_320_km() -> None:
    assert 300 < haversine_km(44.43, 26.10, 46.77, 23.60) < 340


def test_same_event_tolerates_small_time_and_location_differences() -> None:
    emsc = make_quake(source="emsc", event_id="e1")
    usgs = make_quake(
        source="usgs",
        event_id="u1",
        time=emsc.time + timedelta(seconds=40),
        latitude=emsc.latitude + 0.3,
        magnitude=4.2,
    )
    assert emsc.is_same_event(usgs)


def test_different_events_are_not_merged() -> None:
    base = make_quake()
    later = make_quake(source="usgs", event_id="u1", time=base.time + timedelta(minutes=10))
    far = make_quake(source="usgs", event_id="u2", latitude=base.latitude + 3)
    assert not base.is_same_event(later)
    assert not base.is_same_event(far)


def test_group_duplicates_merges_three_sources_and_picks_priority_primary() -> None:
    infp = make_quake(source="infp", event_id="i1", magnitude=4.4)
    emsc = make_quake(source="emsc", event_id="e1", time=infp.time + timedelta(seconds=20))
    usgs = make_quake(source="usgs", event_id="u1", time=infp.time - timedelta(seconds=15))
    other = make_quake(source="emsc", event_id="e2", time=infp.time - timedelta(minutes=30))

    groups = group_duplicates([usgs, other, emsc, infp], ["infp", "emsc", "usgs"])

    assert len(groups) == 2
    merged = next(g for g in groups if len(g.reports) == 3)
    assert merged.primary is infp
    assert merged.sources == ("infp", "emsc", "usgs")
    assert [g.primary for g in groups if len(g.reports) == 1] == [other]


def test_group_duplicates_ranks_unknown_sources_last() -> None:
    known = make_quake(source="usgs", event_id="u1")
    unknown = make_quake(source="mystery", event_id="m1")
    [group] = group_duplicates([unknown, known], ["usgs"])
    assert group.primary is known
