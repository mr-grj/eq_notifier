import pytest

from eq_notifier.cli import main


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
