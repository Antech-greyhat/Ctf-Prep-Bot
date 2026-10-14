# The only module that reads environment variables.
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

_TOKEN_PATTERN = re.compile(r"[0-9]+:[A-Za-z0-9_-]{30,}")
# Not str.isdigit(): it also accepts characters like "²".
_DIGITS = re.compile(r"[0-9]+")
_DIGEST_TIME = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")

DIGEST_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_LOOKAHEAD_DAYS = 365

DEFAULTS = {
    "TIMEZONE": "Africa/Nairobi",
    "LOOKAHEAD_DAYS": "14",
    "DIGEST_DAY": "mon",
    "DIGEST_TIME": "08:00",
    "EVENTS_FILE": "events.toml",
}


class ConfigError(Exception):
    # Messages name the variable but never include its value.
    pass


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    owner_id: int
    timezone: ZoneInfo
    lookahead_days: int
    digest_day: str
    digest_time: time
    events_file: Path


def read_environment() -> Mapping[str, str]:
    # override=False: real environment variables (e.g. from systemd) win over .env.
    load_dotenv(Path.cwd() / ".env", override=False)
    return os.environ


def load_token(env: Mapping[str, str]) -> str:
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and set it.")
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN is malformed (expected <digits>:<secret> as given by @BotFather)."
        )
    return token


def load_config(env: Mapping[str, str]) -> Config:
    return Config(
        token=load_token(env),
        owner_id=_owner_id(env),
        timezone=_timezone(_get(env, "TIMEZONE")),
        lookahead_days=_lookahead_days(_get(env, "LOOKAHEAD_DAYS")),
        digest_day=_digest_day(_get(env, "DIGEST_DAY")),
        digest_time=_digest_time(_get(env, "DIGEST_TIME")),
        events_file=Path(_get(env, "EVENTS_FILE")),
    )


def _get(env: Mapping[str, str], name: str) -> str:
    return env.get(name, "").strip() or DEFAULTS[name]


def _owner_id(env: Mapping[str, str]) -> int:
    raw = env.get("OWNER_ID", "").strip()
    if not raw:
        raise ConfigError("OWNER_ID is not set. Run `python -m ctfbot --whoami` to find it.")
    if not _DIGITS.fullmatch(raw) or int(raw) == 0:
        raise ConfigError("OWNER_ID must be a positive integer (your numeric Telegram user ID).")
    return int(raw)


def _timezone(raw: str) -> ZoneInfo:
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(
            "TIMEZONE is not a valid IANA timezone name (e.g. Africa/Nairobi)."
        ) from None


def _lookahead_days(raw: str) -> int:
    if not _DIGITS.fullmatch(raw) or not 1 <= int(raw) <= MAX_LOOKAHEAD_DAYS:
        raise ConfigError(f"LOOKAHEAD_DAYS must be a whole number from 1 to {MAX_LOOKAHEAD_DAYS}.")
    return int(raw)


def _digest_day(raw: str) -> str:
    day = raw.lower()
    if day not in DIGEST_DAYS:
        raise ConfigError(f"DIGEST_DAY must be one of: {', '.join(DIGEST_DAYS)}.")
    return day


def _digest_time(raw: str) -> time:
    match = _DIGEST_TIME.fullmatch(raw)
    if not match:
        raise ConfigError("DIGEST_TIME must be a 24-hour time in HH:MM format (e.g. 08:00).")
    return time(int(match[1]), int(match[2]))
