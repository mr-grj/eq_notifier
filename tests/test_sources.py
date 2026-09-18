import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from eq_notifier.config import ROMANIA_BBOX
from eq_notifier.sources import (
    EmscSource,
    InfpSource,
    SourceError,
    UsgsSource,
    fetch_all,
)

SINCE = datetime(2026, 9, 18, 11, 0, tzinfo=UTC)

INFP_TEXT = """#EventID|Time|Latitude|Longitude|Depth/km|Author|Catalog|Contributor|ContributorID|MagType|Magnitude|MagAuthor|EventLocationName|EventType
quakeml:apiv2.infp.ro/event/atlas24/2025253/10130024|2025-09-10T13:00:24.678|44.979400|27.207000|18.888|NIEP:rtMl||niep|quakeml:apiv2.infp.ro/event/atlas24/2025253/10130024|ml|3.09|dbevproc||earthquake
this line is broken
quakeml:apiv2.infp.ro/event/atlas24/2025253/00000031|2025-09-10T12:33:24.194|44.127900|22.124800||NIEP:rtMl||niep|quakeml:apiv2.infp.ro/event/atlas24/2025253/00000031|ml|1.65|dbevproc|Oltenia|earthquake
"""

EMSC_JSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [25.9858, 47.3824, -21.4]},
            "id": "20260918_0000016",
            "properties": {
                "source_id": "2061804",
                "time": "2026-09-18T01:09:39.39Z",
                "flynn_region": "ROMANIA",
                "lat": 47.3824,
                "lon": 25.9858,
                "depth": 21.4,
                "mag": 3.1,
                "magtype": "ml",
                "unid": "20260918_0000016",
            },
        },
        {"type": "Feature", "geometry": {}, "id": "x", "properties": {"unid": "x"}},
    ],
}

USGS_JSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "mag": 4.1,
                "place": "4 km ENE of Tudor Vladimirescu, Romania",
                "time": 1788732538648,
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us7000tgp8",
                "type": "earthquake",
            },
            "geometry": {"type": "Point", "coordinates": [27.7057, 45.5869, 10]},
            "id": "us7000tgp8",
        }
    ],
}


def respond_with(body: str | dict, status: int = 200, content_type: str = "application/json"):
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(body) if isinstance(body, dict) else body
        return httpx.Response(status, content=content, headers={"content-type": content_type})

    return handler


def test_infp_text_is_parsed_and_broken_lines_skipped(mock_client) -> None:
    with mock_client(respond_with(INFP_TEXT, content_type="text/plain")) as client:
        events = InfpSource().fetch(client, SINCE, ROMANIA_BBOX)

    assert [e.magnitude for e in events] == [3.09, 1.65]
    first, second = events
    assert first.source == "infp"
    assert first.time == datetime(2025, 9, 10, 13, 0, 24, 678000, tzinfo=UTC)
    assert (first.latitude, first.longitude, first.depth_km) == (44.9794, 27.207, 18.888)
    assert second.depth_km is None
    assert second.region == "Oltenia"


def test_fdsn_no_content_means_no_events(mock_client) -> None:
    with mock_client(lambda request: httpx.Response(204)) as client:
        assert InfpSource().fetch(client, SINCE, ROMANIA_BBOX) == []
        assert EmscSource().fetch(client, SINCE, ROMANIA_BBOX) == []


def test_emsc_geojson_is_normalised(mock_client) -> None:
    with mock_client(respond_with(EMSC_JSON)) as client:
        [event] = EmscSource().fetch(client, SINCE, ROMANIA_BBOX)

    assert event.key == "emsc:20260918_0000016"
    assert event.time == datetime(2026, 9, 18, 1, 9, 39, 390000, tzinfo=UTC)
    assert (event.latitude, event.longitude, event.depth_km) == (47.3824, 25.9858, 21.4)
    assert event.magnitude == 3.1
    assert event.region == "ROMANIA"
    assert event.url is not None and event.url.endswith("unid=20260918_0000016")


def test_usgs_geojson_is_normalised(mock_client) -> None:
    with mock_client(respond_with(USGS_JSON)) as client:
        [event] = UsgsSource().fetch(client, SINCE, ROMANIA_BBOX)

    assert event.key == "usgs:us7000tgp8"
    assert event.time == datetime.fromtimestamp(1788732538.648, tz=UTC)
    assert (event.latitude, event.longitude, event.depth_km) == (45.5869, 27.7057, 10.0)
    assert event.magnitude == 4.1
    assert event.region.endswith("Romania")


def test_query_window_is_sent_to_the_feed(mock_client) -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(204)

    with mock_client(handler) as client:
        UsgsSource().fetch(client, SINCE, ROMANIA_BBOX)

    params = seen[0].params
    assert params["starttime"] == "2026-09-18T11:00:00"
    assert float(params["minlatitude"]) == ROMANIA_BBOX.min_latitude
    assert params["orderby"] == "time"


@pytest.mark.parametrize(
    "handler",
    [
        respond_with("<html>maintenance</html>", content_type="text/html"),
        respond_with({"unexpected": "shape"}),
        respond_with("", status=503),
    ],
)
def test_unusable_responses_raise_source_error(mock_client, handler) -> None:
    with mock_client(handler) as client, pytest.raises(SourceError):
        EmscSource().fetch(client, SINCE, ROMANIA_BBOX)


def test_fetch_all_isolates_failing_sources(mock_client) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        match request.url.host:
            case "earthquake.usgs.gov":
                return httpx.Response(200, json=USGS_JSON)
            case "www.seismicportal.eu":
                raise httpx.ReadTimeout("slow")
            case _:
                return httpx.Response(500)

    with mock_client(handler) as client:
        results = fetch_all([InfpSource(), EmscSource(), UsgsSource()], client, SINCE, ROMANIA_BBOX)

    by_name = {r.source: r for r in results}
    assert [r.source for r in results] == ["infp", "emsc", "usgs"]
    assert by_name["infp"].error == "SourceError: HTTP 500"
    assert by_name["emsc"].error is not None and "ReadTimeout" in by_name["emsc"].error
    assert by_name["usgs"].error is None
    assert len(by_name["usgs"].events) == 1
    assert SINCE + timedelta(0) == SINCE
