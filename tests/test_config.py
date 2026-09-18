import pytest

from eq_notifier.config import ROMANIA_BBOX, ConfigError, Settings


def test_defaults_when_env_is_empty() -> None:
    settings = Settings.from_env({})
    assert settings.min_magnitude == 4.0
    assert settings.location is None
    assert settings.sources == ("infp", "emsc", "usgs")
    assert settings.notifiers == ()
    assert settings.bbox == ROMANIA_BBOX


def test_full_configuration_is_parsed() -> None:
    settings = Settings.from_env(
        {
            "EQ_MIN_MAGNITUDE": "3.5",
            "EQ_LATITUDE": "44.43",
            "EQ_LONGITUDE": "26.10",
            "EQ_RADIUS_KM": "250",
            "EQ_SOURCES": "emsc, usgs",
            "EQ_NOTIFIERS": "ntfy,telegram",
            "NTFY_TOPIC": "my-secret-topic",
            "TELEGRAM_BOT_TOKEN": "123:abc",
            "TELEGRAM_CHAT_ID": "42",
        }
    )
    assert settings.min_magnitude == 3.5
    assert settings.location is not None
    assert settings.sources == ("emsc", "usgs")
    assert settings.notifiers == ("ntfy", "telegram")
    bbox = settings.bbox
    assert bbox.min_latitude < 44.43 < bbox.max_latitude
    assert bbox.min_longitude < 26.10 < bbox.max_longitude
    assert bbox.max_latitude - bbox.min_latitude == pytest.approx(2 * 250 / 111, rel=0.01)


def test_enabled_notifier_without_credentials_is_reported() -> None:
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is required"):
        Settings.from_env({"EQ_NOTIFIERS": "telegram", "TELEGRAM_CHAT_ID": "42"})


def test_all_problems_are_listed_at_once() -> None:
    env = {
        "EQ_MIN_MAGNITUDE": "big",
        "EQ_SOURCES": "infp,bogus",
        "EQ_LATITUDE": "44.4",
        "EQ_POLL_INTERVAL_SECONDS": "1",
    }
    with pytest.raises(ConfigError) as info:
        Settings.from_env(env)
    text = str(info.value)
    assert "EQ_MIN_MAGNITUDE must be a number" in text
    assert "bogus" in text
    assert "EQ_LATITUDE and EQ_LONGITUDE must be set together" in text
    assert "EQ_POLL_INTERVAL_SECONDS must be at least 5" in text
