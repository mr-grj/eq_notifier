"""Notification channels. Each notifier delivers one (title, message) pair.

To add a channel: name it in `NotifierName`, implement the `Notifier` protocol and wire it
up in `build_notifier`.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from eq_notifier.config import NotifierName, Settings

log = logging.getLogger(__name__)


class NotificationError(Exception):
    """The provider refused or failed to deliver the message.

    `retryable` marks server-side trouble worth another attempt; rejected credentials,
    unknown chats and similar 4xx answers are not.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Notifier(Protocol):
    @property
    def name(self) -> str: ...

    def send(self, client: httpx.Client, title: str, message: str) -> None: ...


def _json_body(response: httpx.Response) -> dict[str, Any]:
    """The response's JSON object, or an empty one when it carries none."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@dataclass(frozen=True, slots=True)
class NtfyNotifier:
    server: str
    topic: str
    token: str = ""
    name: str = NotifierName.NTFY

    def send(self, client: httpx.Client, title: str, message: str) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        payload = {
            "topic": self.topic,
            "title": title,
            "message": message,
            "priority": 5,
            "tags": ["warning"],
        }
        response = client.post(self.server, json=payload, headers=headers)
        if response.is_error:
            raise NotificationError(
                f"ntfy: HTTP {response.status_code}", retryable=response.is_server_error
            )


@dataclass(frozen=True, slots=True)
class TelegramNotifier:
    bot_token: str
    chat_id: str
    name: str = NotifierName.TELEGRAM

    def send(self, client: httpx.Client, title: str, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        response = client.post(url, json={"chat_id": self.chat_id, "text": f"{title}\n\n{message}"})
        body = _json_body(response)
        if response.is_error or not body.get("ok"):
            detail = body.get("description") or f"HTTP {response.status_code}"
            raise NotificationError(f"telegram: {detail}", retryable=response.is_server_error)


@dataclass(frozen=True, slots=True)
class TwilioNotifier:
    account_sid: str
    auth_token: str
    from_number: str
    to_number: str
    name: str = NotifierName.TWILIO

    def send(self, client: httpx.Client, title: str, message: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        response = client.post(
            url,
            auth=(self.account_sid, self.auth_token),
            data={"To": self.to_number, "From": self.from_number, "Body": f"{title}\n{message}"},
        )
        if response.is_error:
            detail = _json_body(response).get("message") or f"HTTP {response.status_code}"
            raise NotificationError(f"twilio: {detail}", retryable=response.is_server_error)


def build_notifier(name: NotifierName, settings: Settings) -> Notifier:
    """The named channel, configured from the settings."""
    match name:
        case NotifierName.NTFY:
            return NtfyNotifier(settings.ntfy_server, settings.ntfy_topic, settings.ntfy_token)
        case NotifierName.TELEGRAM:
            return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        case NotifierName.TWILIO:
            return TwilioNotifier(
                settings.twilio_account_sid,
                settings.twilio_auth_token,
                settings.twilio_from,
                settings.twilio_to,
            )


def build_notifiers(settings: Settings) -> list[Notifier]:
    """The channels enabled in the settings."""
    return [build_notifier(name, settings) for name in settings.notifiers]


def send_with_retries(
    notifier: Notifier,
    client: httpx.Client,
    title: str,
    message: str,
    *,
    attempts: int,
    retry_delay: float,
) -> bool:
    """Deliver through one channel, retrying only failures worth another attempt.

    Error text never includes provider URLs, because some of them (Telegram) carry the secret.
    """
    for attempt in range(1, attempts + 1):
        try:
            notifier.send(client, title, message)
        except httpx.HTTPError as exc:
            reason, retryable = type(exc).__name__, True
        except NotificationError as exc:
            reason, retryable = str(exc), exc.retryable
        else:
            log.info("Notification sent via %s", notifier.name)
            return True
        log.warning(
            "Notification via %s failed (attempt %d/%d): %s",
            notifier.name,
            attempt,
            attempts,
            reason,
        )
        if not retryable:
            return False
        if attempt < attempts:
            time.sleep(retry_delay)
    return False


def send_all(
    notifiers: Sequence[Notifier],
    client: httpx.Client,
    title: str,
    message: str,
    *,
    attempts: int = 3,
    retry_delay: float = 2.0,
) -> list[str]:
    """Send through every notifier; return the names of those that delivered."""
    return [
        notifier.name
        for notifier in notifiers
        if send_with_retries(
            notifier, client, title, message, attempts=attempts, retry_delay=retry_delay
        )
    ]
