import json

import httpx
import pytest

from eq_notifier.config import Settings
from eq_notifier.notifiers import (
    NotificationError,
    NtfyNotifier,
    TelegramNotifier,
    TwilioNotifier,
    build_notifiers,
    send_all,
)


def test_ntfy_publishes_urgent_json_with_token(mock_client) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "abc"})

    with mock_client(handler) as client:
        NtfyNotifier("https://ntfy.sh", "quakes-xyz", token="tk_secret").send(client, "T", "body")

    [request] = requests
    assert str(request.url) == "https://ntfy.sh"
    assert request.headers["authorization"] == "Bearer tk_secret"
    payload = json.loads(request.content)
    assert payload["topic"] == "quakes-xyz"
    assert payload["title"] == "T"
    assert payload["priority"] == 5


def test_telegram_rejection_is_reported_without_leaking_the_token(mock_client) -> None:
    handler = lambda request: httpx.Response(  # noqa: E731
        400, json={"ok": False, "description": "Bad Request: chat not found"}
    )
    with mock_client(handler) as client, pytest.raises(NotificationError) as info:
        TelegramNotifier("123:SECRET", "42").send(client, "T", "body")
    assert "chat not found" in str(info.value)
    assert "SECRET" not in str(info.value)


def test_twilio_uses_basic_auth_and_form_fields(mock_client) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"sid": "SM1", "status": "queued"})

    with mock_client(handler) as client:
        TwilioNotifier("AC1", "tok", "+15550001", "+40700000").send(client, "T", "body")

    [request] = requests
    assert request.url.path == "/2010-04-01/Accounts/AC1/Messages.json"
    assert request.headers["authorization"].startswith("Basic ")
    form = dict(httpx.QueryParams(request.content.decode()))
    assert form == {"To": "+40700000", "From": "+15550001", "Body": "T\nbody"}


def test_send_all_retries_only_transient_failures_and_continues_past_broken_notifier(
    mock_client,
) -> None:
    calls = {"ntfy": 0, "telegram": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ntfy.sh":
            calls["ntfy"] += 1
            return httpx.Response(503 if calls["ntfy"] == 1 else 200)
        calls["telegram"] += 1
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    notifiers = [TelegramNotifier("1:bad", "42"), NtfyNotifier("https://ntfy.sh", "topic")]
    with mock_client(handler) as client:
        delivered = send_all(notifiers, client, "T", "body", attempts=3, retry_delay=0)

    assert delivered == ["ntfy"]
    assert calls == {"ntfy": 2, "telegram": 1}


def test_build_notifiers_follows_settings() -> None:
    settings = Settings.from_env(
        {
            "EQ_NOTIFIERS": "telegram,ntfy",
            "TELEGRAM_BOT_TOKEN": "t",
            "TELEGRAM_CHAT_ID": "1",
            "NTFY_TOPIC": "x",
        }
    )
    assert [n.name for n in build_notifiers(settings)] == ["telegram", "ntfy"]
