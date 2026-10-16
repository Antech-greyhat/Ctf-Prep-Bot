import os
from datetime import time
from pathlib import Path

import pytest

from ctfbot import __main__ as entry
from ctfbot.config import ConfigError, load_config, read_environment
from tests.helpers import FAKE_TOKEN, env


def test_valid_config_uses_defaults() -> None:
    config = load_config(env())
    assert config.owner_id == 424242
    assert config.timezone.key == "Africa/Nairobi"
    assert config.lookahead_days == 14
    assert config.digest_day == "mon"
    assert config.digest_time == time(8, 0)
    assert config.events_file == Path("events.toml")
    assert config.database_path == Path("data/ctfbot.db")
    assert config.categories_file == Path("categories.toml")
    assert config.inactivity_days == 3
    assert config.nudge_time == time(18, 0)
    assert config.reminder_hours == 24


def test_empty_optional_values_fall_back_to_defaults() -> None:
    assert load_config(env(TIMEZONE="", LOOKAHEAD_DAYS="")).lookahead_days == 14


def test_token_is_not_in_repr() -> None:
    config = load_config(env())
    assert FAKE_TOKEN not in repr(config)
    assert FAKE_TOKEN not in str(config)


@pytest.mark.parametrize("token", ["", "   ", "not-a-token", "123:short", "abc:" + "A" * 35])
def test_missing_or_malformed_token_fails_without_echoing_it(token: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_config(env(TELEGRAM_BOT_TOKEN=token))
    assert "TELEGRAM_BOT_TOKEN" in str(info.value)
    if token.strip():
        assert token not in str(info.value)


def test_missing_token_variable_fails() -> None:
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config({"OWNER_ID": "1"})


@pytest.mark.parametrize("owner_id", ["", "0", "-5", "12.5", "abc", "1e9", "²", "١٢"])
def test_bad_owner_id_fails_without_echoing_it(owner_id: str) -> None:
    with pytest.raises(ConfigError) as info:
        load_config(env(OWNER_ID=owner_id))
    assert "OWNER_ID" in str(info.value)
    if owner_id:
        assert owner_id not in str(info.value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TIMEZONE", "Mars/Olympus"),
        ("TIMEZONE", "../../etc/passwd"),
        ("LOOKAHEAD_DAYS", "0"),
        ("LOOKAHEAD_DAYS", "366"),
        ("LOOKAHEAD_DAYS", "two"),
        ("DIGEST_DAY", "monday"),
        ("DIGEST_TIME", "24:00"),
        ("DIGEST_TIME", "8am"),
        ("INACTIVITY_DAYS", "31"),
        ("INACTIVITY_DAYS", "-1"),
        ("NUDGE_TIME", "7pm"),
        ("REMINDER_HOURS", "169"),
        ("REMINDER_HOURS", "1.5"),
    ],
)
def test_bad_optional_values_fail_naming_the_variable(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        load_config(env(**{name: value}))


def test_zero_turns_nudge_and_reminders_off() -> None:
    config = load_config(env(INACTIVITY_DAYS="0", REMINDER_HOURS="0"))
    assert (config.inactivity_days, config.reminder_hours) == (0, 0)


def test_digest_day_is_case_insensitive() -> None:
    assert load_config(env(DIGEST_DAY="FRI")).digest_day == "fri"


def test_real_environment_wins_over_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("TIMEZONE=From/DotEnv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TIMEZONE", "Europe/Berlin")
    read_environment()
    assert os.environ["TIMEZONE"] == "Europe/Berlin"


def test_main_fails_closed_on_missing_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(entry, "read_environment", lambda: {})
    assert entry.main([]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_main_fails_closed_on_bad_owner_without_leaking_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(entry, "read_environment", lambda: env(OWNER_ID="-1"))
    assert entry.main([]) == 2
    captured = capsys.readouterr()
    assert "OWNER_ID" in captured.err
    assert FAKE_TOKEN not in captured.err + captured.out
