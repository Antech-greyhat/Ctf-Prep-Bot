import logging
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from ctfbot.models import Event, clean_text, is_http_url

logger = logging.getLogger(__name__)

SOURCE_NAME = "manual"
ALLOWED_KEYS = frozenset({"title", "start", "finish", "url"})
DATETIME_HINT = "a date-time with a UTC offset and no quotes, e.g. 2026-11-07T09:00:00+03:00"


class ManualEventsError(Exception):
    pass


def load_manual_events(path: Path) -> list[Event]:
    if not path.exists():
        logger.info("No manual events file at %s; showing CTFtime events only", path)
        return []
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ManualEventsError(f"{path}: invalid TOML: {exc}") from None
    events = parse_manual_events(data, str(path))
    logger.info("Loaded %d manual event(s) from %s", len(events), path)
    return events


def parse_manual_events(data: dict[str, object], origin: str) -> list[Event]:
    unknown = set(data) - {"event"}
    if unknown:
        raise ManualEventsError(
            f"{origin}: unexpected top-level key(s) {sorted(unknown)}; use [[event]] tables"
        )
    entries = data.get("event", [])
    if not isinstance(entries, list):
        raise ManualEventsError(f"{origin}: 'event' must be a list of [[event]] tables")
    return [_parse_entry(entry, f"{origin}: [[event]] #{n}") for n, entry in enumerate(entries, 1)]


def _parse_entry(entry: object, where: str) -> Event:
    if not isinstance(entry, dict):
        raise ManualEventsError(f"{where}: must be a table")
    unknown = set(entry) - ALLOWED_KEYS
    if unknown:
        raise ManualEventsError(f"{where}: unknown key(s) {sorted(unknown)}")
    raw_title = entry.get("title")
    title = clean_text(raw_title) if isinstance(raw_title, str) else ""
    if not title:
        raise ManualEventsError(f"{where}: 'title' is required and must be non-empty text")
    where = f"{where} ({title!r})"
    start = _datetime(entry, "start", where)
    finish = _datetime(entry, "finish", where)
    if finish < start:
        raise ManualEventsError(f"{where}: 'finish' is before 'start'")
    url = entry.get("url")
    if url is not None and not (isinstance(url, str) and is_http_url(url)):
        raise ManualEventsError(f"{where}: 'url' must be an http:// or https:// link")
    return Event(title=title, start=start, finish=finish, url=url, source=SOURCE_NAME)


def _datetime(entry: dict[str, object], key: str, where: str) -> datetime:
    value = entry.get(key)
    # Without an offset the moment in time would be ambiguous.
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ManualEventsError(f"{where}: '{key}' must be {DATETIME_HINT}")
    return value.astimezone(UTC)
