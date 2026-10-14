import argparse
import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar

import httpx

from ctfbot import REPO_URL, __version__
from ctfbot.logging_setup import configure_logging
from ctfbot.models import Event, clean_text, is_http_url, parse_utc, starting_within

logger = logging.getLogger(__name__)

API_URL = "https://ctftime.org/api/v1/events/"
USER_AGENT = f"ctf-prep-bot/{__version__} (+{REPO_URL})"
REQUEST_TIMEOUT_SECONDS = 10.0
CACHE_TTL = timedelta(hours=1)
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = 2.0
# No `finish` param: CTFtime applies it to event end times, which drops long events.
FETCH_LIMIT = 100

# Event filters. Other values seen: Attack-Defense; Prequalified, Academic, Casual.
ONLINE_ONLY = True
ALLOWED_FORMATS = frozenset({"Jeopardy"})
ALLOWED_RESTRICTIONS = frozenset({"Open"})

SOURCE_NAME = "CTFtime"

T = TypeVar("T")


class CtftimeUnavailable(Exception):
    pass


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})


def passes_filters(onsite: bool, event_format: str, restrictions: str) -> bool:
    if ONLINE_ONLY and onsite:
        return False
    return event_format in ALLOWED_FORMATS and restrictions in ALLOWED_RESTRICTIONS


def parse_events(payload: object) -> list[Event]:
    if not isinstance(payload, list):
        raise CtftimeUnavailable("CTFtime response was not a JSON list")
    parsed = (parse_entry(entry) for entry in payload)
    return [event for event in parsed if event is not None]


def parse_entry(entry: object) -> Event | None:
    if not isinstance(entry, dict):
        logger.warning("Skipping malformed CTFtime entry: not a JSON object")
        return None
    try:
        title = clean_text(_field(entry, "title", str))
        start = parse_utc(_field(entry, "start", str))
        finish = parse_utc(_field(entry, "finish", str))
        onsite = _field(entry, "onsite", bool)
        event_format = _field(entry, "format", str)
        restrictions = _field(entry, "restrictions", str)
        if not title:
            raise ValueError("empty title")
        if finish < start:
            raise ValueError("finish is before start")
    except ValueError as exc:
        # Never log field values: they are untrusted.
        logger.warning("Skipping malformed CTFtime entry (id=%s): %s", _safe_id(entry), exc)
        return None
    if not passes_filters(onsite, event_format, restrictions):
        return None
    return Event(title=title, start=start, finish=finish, url=_pick_url(entry), source=SOURCE_NAME)


def _field(entry: dict[str, object], key: str, expected: type[T]) -> T:
    value = entry.get(key)
    # bool is a subclass of int, so reject it explicitly.
    if not isinstance(value, expected) or (expected is not bool and isinstance(value, bool)):
        raise ValueError(f"field {key!r} is missing or not a {expected.__name__}")
    return value


def _safe_id(entry: dict[str, object]) -> str:
    event_id = entry.get("id")
    return str(event_id) if type(event_id) is int else "?"


def _pick_url(entry: dict[str, object]) -> str | None:
    for key in ("url", "ctftime_url"):
        value = entry.get(key)
        if isinstance(value, str) and is_http_url(value.strip()):
            return value.strip()
    return None


class CtftimeClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._http = http
        self._sleep = sleep
        self._clock = clock
        self._cached_at: datetime | None = None
        self._cached_events: list[Event] = []
        self._lock = asyncio.Lock()

    async def upcoming(self) -> list[Event]:
        async with self._lock:
            now = self._clock()
            if self._cached_at is not None and now - self._cached_at < CACHE_TTL:
                return self._cached_events
            events = parse_events(await self._fetch(now))
            self._cached_at, self._cached_events = now, events
            return events

    async def _fetch(self, now: datetime) -> object:
        params = {"start": int(now.timestamp()), "limit": FETCH_LIMIT}
        problem = ""
        for attempt in range(1 + MAX_RETRIES):
            if attempt:
                await self._sleep(RETRY_BACKOFF_SECONDS * attempt)
            try:
                response = await self._http.get(API_URL, params=params)
            except httpx.TransportError as exc:
                problem = type(exc).__name__
                continue
            if response.status_code == 429 or response.status_code >= 500:
                problem = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                raise CtftimeUnavailable(f"CTFtime returned HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError:
                raise CtftimeUnavailable("CTFtime returned invalid JSON") from None
        raise CtftimeUnavailable(f"CTFtime request failed after retry ({problem})")


async def _print_upcoming(days: int) -> None:
    async with make_http_client() as http:
        events = await CtftimeClient(http).upcoming()
    for event in starting_within(events, datetime.now(UTC), days):
        print(f"{event.start:%Y-%m-%d %H:%M} UTC  {event.title}  {event.url or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Print upcoming CTFtime events (UTC).")
    parser.add_argument("--days", type=int, default=14, help="lookahead in days (default 14)")
    args = parser.parse_args()
    configure_logging()
    # Windows consoles cannot encode every title.
    sys.stdout.reconfigure(errors="replace")
    try:
        asyncio.run(_print_upcoming(args.days))
    except CtftimeUnavailable as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
