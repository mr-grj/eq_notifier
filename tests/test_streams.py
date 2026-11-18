import json
import socket
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from websockets.sync.server import ServerConnection, serve

from eq_notifier.config import Settings
from eq_notifier.events import Earthquake
from eq_notifier.streams import EmscStream, build_streams, parse_emsc_message

PROPERTIES = {
    "source_catalog": "EMSC-RTS",
    "lastupdate": "2026-11-18T00:51:02.880856Z",
    "time": "2026-11-18T00:48:55.2Z",
    "flynn_region": "ROMANIA",
    "lat": 45.6687,
    "lon": 26.5612,
    "depth": 120.8,
    "evtype": "ke",
    "auth": "EMSC",
    "mag": 5.3,
    "magtype": "mw",
    "unid": "20261118_0000012",
}


def message(action: str = "create", **props) -> str:
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [26.5612, 45.6687, -120.8]},
        "id": PROPERTIES["unid"],
        "properties": {**PROPERTIES, **props},
    }
    return json.dumps({"action": action, "data": feature})


@contextmanager
def server(handler: Callable[[ServerConnection], None]) -> Iterator[str]:
    with serve(handler, "127.0.0.1", 0) as ws_server:
        thread = threading.Thread(target=ws_server.serve_forever, daemon=True)
        thread.start()
        host, port = ws_server.socket.getsockname()
        yield f"ws://{host}:{port}"
        ws_server.shutdown()


class Collector:
    def __init__(self, stop: threading.Event, until: int) -> None:
        self.stop = stop
        self.until = until
        self.reports: list[Earthquake] = []

    def __call__(self, events: list[Earthquake]) -> None:
        self.reports.extend(events)
        if len(self.reports) >= self.until:
            self.stop.set()


def run_in_background(stream: EmscStream, sink: Collector) -> threading.Thread:
    thread = threading.Thread(target=stream.run, args=(sink, sink.stop), daemon=True)
    thread.start()
    return thread


def test_create_and_update_messages_become_emsc_reports() -> None:
    created = parse_emsc_message(message("create"))
    updated = parse_emsc_message(message("update", mag=5.4))

    assert created is not None and updated is not None
    assert created.key == updated.key == "emsc:20261118_0000012"
    assert created.time == datetime(2026, 11, 18, 0, 48, 55, 200000, tzinfo=UTC)
    assert (created.latitude, created.longitude, created.depth_km) == (45.6687, 26.5612, 120.8)
    assert (created.magnitude, updated.magnitude) == (5.3, 5.4)
    assert created.region == "ROMANIA"


@pytest.mark.parametrize(
    "raw",
    [
        message("delete"),
        "not json",
        "[1, 2]",
        json.dumps({"action": "create"}),
        json.dumps({"action": "create", "data": {"properties": {"unid": "x"}}}),
        message("create", mag=None),
    ],
)
def test_other_or_malformed_messages_are_skipped(raw: str) -> None:
    assert parse_emsc_message(raw) is None


def test_stream_delivers_reports_and_skips_garbage_over_a_real_socket() -> None:
    def handler(websocket: ServerConnection) -> None:
        websocket.send("garbage")
        websocket.send(message("create", mag=4.1))
        websocket.send(message("update", mag=4.4))
        websocket.recv()

    stop = threading.Event()
    sink = Collector(stop, until=2)
    with server(handler) as url:
        thread = run_in_background(EmscStream(url=url), sink)
        assert stop.wait(5)
        thread.join(5)

    assert [report.magnitude for report in sink.reports] == [4.1, 4.4]
    assert not thread.is_alive()


def test_stream_reconnects_after_the_server_drops_it(monkeypatch) -> None:
    monkeypatch.setattr("eq_notifier.streams.RECONNECT_MIN_SECONDS", 0.05)
    connections = 0

    def handler(websocket: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        websocket.send(message(mag=4.0 + connections / 10))

    stop = threading.Event()
    sink = Collector(stop, until=2)
    with server(handler) as url:
        run_in_background(EmscStream(url=url), sink)
        assert stop.wait(5)

    assert connections >= 2
    assert [report.magnitude for report in sink.reports[:2]] == [4.1, 4.2]


def test_silent_stream_is_reconnected(monkeypatch) -> None:
    monkeypatch.setattr("eq_notifier.streams.RECONNECT_MIN_SECONDS", 0.05)
    monkeypatch.setattr("eq_notifier.streams.RECEIVE_TICK_SECONDS", 0.05)
    monkeypatch.setattr("eq_notifier.streams.SILENCE_LIMIT_SECONDS", 0.2)
    connections = 0

    def handler(websocket: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        if connections == 2:
            websocket.send(message())
        websocket.recv()

    stop = threading.Event()
    sink = Collector(stop, until=1)
    with server(handler) as url:
        run_in_background(EmscStream(url=url), sink)
        assert stop.wait(5)

    assert connections == 2


def test_stream_keeps_retrying_while_the_server_is_unreachable(monkeypatch) -> None:
    monkeypatch.setattr("eq_notifier.streams.RECONNECT_MIN_SECONDS", 0.01)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    stop = threading.Event()
    thread = run_in_background(EmscStream(url=f"ws://127.0.0.1:{port}"), Collector(stop, 1))

    thread.join(0.5)
    assert thread.is_alive()
    stop.set()
    thread.join(5)
    assert not thread.is_alive()


def test_probe_reports_health() -> None:
    with server(lambda websocket: None) as url:
        assert EmscStream(url=url).probe() is None
    error = EmscStream(url=url).probe()
    assert error is not None and "refused" in error.lower()


def test_stream_can_be_disabled() -> None:
    assert [s.name for s in build_streams(Settings())] == ["emsc-stream"]
    assert build_streams(Settings(emsc_stream=False)) == []
