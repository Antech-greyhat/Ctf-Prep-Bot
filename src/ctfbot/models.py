import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

MAX_TITLE_LENGTH = 200
# Keeps one escaped event well under Telegram's message limit.
MAX_URL_LENGTH = 400


@dataclass(frozen=True)
class Event:
    title: str
    start: datetime
    finish: datetime
    url: str | None
    source: str

    def __post_init__(self) -> None:
        for name in ("start", "finish"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"Event.{name} must be timezone-aware UTC")
        if self.finish < self.start:
            raise ValueError("Event.finish is before Event.start")
        if self.url is not None and not is_http_url(self.url):
            raise ValueError("Event.url must be an http(s) URL")


def is_http_url(value: str) -> bool:
    if len(value) > MAX_URL_LENGTH or any(ch.isspace() for ch in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def clean_text(value: str, max_length: int = MAX_TITLE_LENGTH) -> str:
    # Strips terminal escapes, bidi overrides and zero-width characters.
    collapsed = " ".join(value.split())
    visible = "".join(ch for ch in collapsed if not unicodedata.category(ch).startswith("C"))
    return visible.strip()[:max_length]


def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        # Drop the untrusted input from the message; it ends up in logs.
        raise ValueError("not an ISO 8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed.astimezone(UTC)


def starting_within(events: list[Event], now: datetime, days: float) -> list[Event]:
    end = now + timedelta(days=days)
    return [event for event in events if now <= event.start <= end]
