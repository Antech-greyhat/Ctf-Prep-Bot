import calendar
import html
from datetime import datetime
from zoneinfo import ZoneInfo

from ctfbot.config import DIGEST_DAYS, Config
from ctfbot.models import Event
from ctfbot.sources import Upcoming

TELEGRAM_MESSAGE_LIMIT = 4096

DIGEST_HEADING = "Weekly CTF digest"
START_TEXT = "Hi! I track upcoming CTF competitions for you. Send /help to see what I can do."


def help_text(config: Config) -> str:
    day_name = calendar.day_name[DIGEST_DAYS.index(config.digest_day)]
    return "\n".join(
        [
            "<b>CTF prep bot</b>",
            f"/events - CTFs starting in the next {config.lookahead_days} days",
            "/digest - send the weekly digest now",
            "/help - this message",
            "",
            f"Weekly digest: every {day_name} at {config.digest_time:%H:%M}.",
            f"Times are shown in {html.escape(config.timezone.key)}.",
        ]
    )


def render_event_list(upcoming: Upcoming, tz: ZoneInfo, lookahead_days: int, heading: str) -> str:
    header = [
        f"<b>{html.escape(heading)}</b>",
        f"Starting in the next {lookahead_days} days. Times in {html.escape(tz.key)}.",
    ]
    if not upcoming.ctftime_ok:
        header.append("⚠️ CTFtime is unavailable right now; showing manual events only.")
    blocks = ["\n".join(header)]
    if not upcoming.events:
        blocks.append("No matching CTFs found.")
    blocks.extend(render_event(event, tz) for event in upcoming.events)
    return "\n\n".join(blocks)


def render_event(event: Event, tz: ZoneInfo) -> str:
    title = html.escape(event.title)
    if event.url:
        title = f'<a href="{html.escape(event.url, quote=True)}">{title}</a>'
    when = format_time_range(event.start.astimezone(tz), event.finish.astimezone(tz))
    return f"<b>{title}</b>\n{when}\n<i>{html.escape(event.source)}</i>"


def format_time_range(start: datetime, finish: datetime) -> str:
    return f"{start:%a %d %b %H:%M} → {finish:%a %d %b %H:%M %Z} ({format_duration(start, finish)})"


def format_duration(start: datetime, finish: datetime) -> str:
    hours = round((finish - start).total_seconds() / 3600)
    if hours < 48:
        return f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def telegram_length(text: str) -> int:
    # Telegram counts UTF-16 units, so an emoji counts as 2.
    return len(text.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    # Split only between blocks so an HTML tag is never cut in half.
    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        if telegram_length(block) > limit:
            raise ValueError("a single message block exceeds the Telegram limit")
        candidate = f"{current}\n\n{block}" if current else block
        if telegram_length(candidate) <= limit:
            current = candidate
        else:
            chunks.append(current)
            current = block
    if current:
        chunks.append(current)
    return chunks
