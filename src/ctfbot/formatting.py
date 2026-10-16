import calendar
import html
from datetime import datetime
from zoneinfo import ZoneInfo

from ctfbot.config import DIGEST_DAYS, Config
from ctfbot.models import Event
from ctfbot.progress import RECENT_WINDOW, CategoryProgress, Report
from ctfbot.sources import Upcoming
from ctfbot.writeups import Writeup, WriteupDraft

TELEGRAM_MESSAGE_LIMIT = 4096

DIGEST_HEADING = "Weekly CTF digest"
START_TEXT = (
    "Hi! I track upcoming CTF competitions and keep your writeup log. "
    "Send /help to see what I can do."
)


def help_text(config: Config) -> str:
    day_name = calendar.day_name[DIGEST_DAYS.index(config.digest_day)]
    return "\n".join(
        [
            "<b>CTF prep bot</b>",
            f"/events - CTFs starting in the next {config.lookahead_days} days",
            "/digest - send the weekly digest now",
            "",
            "/log - record a challenge you solved or attempted",
            "/recent - your latest writeups",
            "/show &lt;id&gt; - read one writeup",
            "/delete &lt;id&gt; - delete one writeup",
            "/stats - progress by category and what to focus on",
            "/cancel - stop the current /log",
            "",
            "/help - this message",
            "",
            f"Weekly digest: every {day_name} at {config.digest_time:%H:%M}.",
            *_reminder_help(config),
            f"Times are shown in {html.escape(config.timezone.key)}.",
        ]
    )


def _reminder_help(config: Config) -> list[str]:
    lines = []
    if config.reminder_hours:
        lines.append(f"CTF reminders: {config.reminder_hours}h before each one starts.")
    if config.inactivity_days:
        lines.append(
            f"Nudge: at {config.nudge_time:%H:%M} after {config.inactivity_days} days "
            "with no writeup."
        )
    return lines


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


# Writeups ----------------------------------------------------------------------------------

SOLVED_LABEL = "✅ Solved"
UNSOLVED_LABEL = "❌ Not solved"

LOG_ASK_PLATFORM = (
    "📝 New writeup. Which platform or CTF? (e.g. picoCTF, TryHackMe, CyberGame Kenya)\n"
    "Send /cancel at any time to stop."
)
LOG_ASK_CHALLENGE = "Challenge name?"
LOG_ASK_CATEGORY = "Category?"
LOG_ASK_RESULT = "Did you solve it?"
LOG_USE_BUTTONS = "Tap one of the buttons above, or send /cancel."
LOG_STUCK_REQUIRED = (
    "For an unsolved challenge, where you got stuck is the most useful part. Write a line or two."
)
LOG_CANCELLED = "Cancelled. Nothing was saved."
LOG_TIMED_OUT = "Your /log was idle too long and timed out. Nothing was saved."
LOG_SAVE_FAILED = (
    "⚠️ Couldn't save the writeup (database error; see the bot's log). Your answers are "
    "kept: send your last answer again, or /cancel."
)
NOTHING_TO_CANCEL = "Nothing to cancel."
BUTTON_EXPIRED = "This button has expired."

# split_message only splits between blank-line-separated blocks. Escaping can make text up to
# 6x longer (" becomes &quot;), so long paragraphs are cut into pieces that always fit:
# 600 characters x 6 = 3600, which leaves room for a heading in the same block.
_SAFE_BLOCK = 3800
_SAFE_PIECE = 600


def log_ask_approach(solved: bool) -> str:
    if solved:
        return (
            "How did you solve it? Key steps and tools.\n"
            "Don't paste the flag: flag-shaped text like flag{...} is removed before saving."
        )
    return "What did you try? Steps, tools and ideas."


def log_ask_stuck(solved: bool) -> str:
    if solved:
        return "Where did you get stuck along the way? Send /skip if you didn't."
    return "Where exactly did you get stuck?"


def status(writeup: WriteupDraft) -> str:
    return "✅ solved" if writeup.solved else "❌ unsolved"


def render_saved(writeup_id: int, writeup: WriteupDraft, redactions: int) -> str:
    lines = [
        f"Saved writeup #{writeup_id}: <b>{html.escape(writeup.challenge)}</b> "
        f"({html.escape(writeup.category)}, {status(writeup)})."
    ]
    if redactions:
        lines.append(f"🔒 Removed {redactions} flag-like string(s) before saving.")
    lines.append(f"Read it back with /show {writeup_id}.")
    return "\n".join(lines)


def summary_block(writeup: Writeup, tz: ZoneInfo) -> str:
    created = writeup.created_at.astimezone(tz)
    return (
        f"<b>#{writeup.id}</b> {html.escape(writeup.challenge)} · "
        f"{html.escape(writeup.platform)}\n"
        f"{html.escape(writeup.category)} · {status(writeup)} · {created:%a %d %b %Y}"
    )


def render_recent(writeups: list[Writeup], tz: ZoneInfo) -> str:
    if not writeups:
        return "No writeups yet. Send /log after your next challenge."
    blocks = ["<b>Recent writeups</b>"]
    blocks.extend(summary_block(writeup, tz) for writeup in writeups)
    blocks.append("Read one with /show &lt;id&gt;.")
    return "\n\n".join(blocks)


def render_writeup(writeup: Writeup, tz: ZoneInfo) -> str:
    created = writeup.created_at.astimezone(tz)
    blocks = [
        f"<b>#{writeup.id} · {html.escape(writeup.challenge)}</b>\n"
        f"{html.escape(writeup.platform)} · {html.escape(writeup.category)} · "
        f"{status(writeup)}\n{created:%a %d %b %Y %H:%M %Z}",
        f"<b>{'How I solved it' if writeup.solved else 'What I tried'}</b>\n"
        f"{escape_notes(writeup.approach)}",
    ]
    if writeup.stuck:
        blocks.append(f"<b>Where I got stuck</b>\n{escape_notes(writeup.stuck)}")
    return "\n\n".join(blocks)


def escape_notes(text: str) -> str:
    pieces: list[str] = []
    for paragraph in text.split("\n\n"):
        escaped = html.escape(paragraph)
        if telegram_length(escaped) <= _SAFE_BLOCK:
            pieces.append(escaped)
        else:
            # Cut before escaping, so an entity like &quot; is never split in half.
            pieces.extend(
                html.escape(paragraph[i : i + _SAFE_PIECE])
                for i in range(0, len(paragraph), _SAFE_PIECE)
            )
    return "\n\n".join(pieces)


def confirm_delete(writeup: Writeup, tz: ZoneInfo) -> str:
    return f"Delete this writeup? This can't be undone.\n\n{summary_block(writeup, tz)}"


def usage(command: str) -> str:
    return f"Usage: /{command} &lt;id&gt;, for example /{command} 12. Find IDs with /recent."


def not_found(writeup_id: int) -> str:
    return f"There is no writeup #{writeup_id}."


def deleted(writeup_id: int) -> str:
    return f"🗑 Deleted writeup #{writeup_id}."


def kept(writeup_id: int) -> str:
    return f"Kept writeup #{writeup_id}."


# Progress report ---------------------------------------------------------------------------

_BAR_CELLS = 10
# Bounds the "not in your order" block so it can never exceed one Telegram message.
_MAX_OTHER_SHOWN = 20


def progress_bar(solved: int, target: int) -> str:
    filled = min(solved, target) * _BAR_CELLS // target
    return "█" * filled + "░" * (_BAR_CELLS - filled)


def _progress_line(row: CategoryProgress, width: int, target: int, focus: str | None) -> str:
    tried = row.attempts - row.solved
    marker = " ✓" if row.solved >= target else " ← focus" if row.name == focus else ""
    extra = f" +{tried} tried" if tried else ""
    count = f"{row.solved:>3}/{target}"
    return f"{row.name:<{width}} {progress_bar(row.solved, target)} {count}{extra}{marker}"


def render_report(report: Report, now: datetime) -> str:
    width = max(len(row.name) for row in report.rows)
    table = "\n".join(
        _progress_line(row, width, report.target, report.focus) for row in report.rows
    )
    blocks = [
        f"<b>Your progress</b>\nTarget: {report.target} solved per category, fundamentals first.",
        # <pre> keeps the bars aligned in Telegram's monospace font.
        f"<pre>{html.escape(table)}</pre>",
    ]
    if report.focus is None:
        blocks.append(
            "🏁 Every category has reached the target. Raise target_solves in "
            "categories.toml to keep going."
        )
    else:
        focus_row = next(row for row in report.rows if row.name == report.focus)
        to_go = report.target - focus_row.solved
        blocks.append(f"🎯 Focus: <b>{html.escape(report.focus)}</b>, {to_go} more to solve.")
    if report.jumping:
        blocks.append(
            f"⚠️ You're jumping around: {report.jumped_logs} of your {report.recent_logs} "
            f"logs in the last {RECENT_WINDOW.days} days were in "
            f"{html.escape(', '.join(report.jumped_to))}, well past your focus. "
            "Fundamentals first."
        )
    blocks.append(_activity_line(report, now))
    if report.other:
        shown = report.other[:_MAX_OTHER_SHOWN]
        others = ", ".join(f"{html.escape(row.name)} {row.solved}/{row.attempts}" for row in shown)
        hidden = len(report.other) - len(shown)
        more = f" and {hidden} more" if hidden else ""
        blocks.append(f"Not in your order (renamed or removed): {others}{more}")
    return "\n\n".join(blocks)


def _activity_line(report: Report, now: datetime) -> str:
    if report.last_log is None:
        return "No writeups yet. Send /log after your next challenge."
    days = (now - report.last_log).days
    ago = "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"
    return f"Last writeup: {ago}. Logs in the last {RECENT_WINDOW.days} days: {report.recent_logs}."


# Nudges and reminders ----------------------------------------------------------------------


def render_nudge(days: int, focus: str | None, ever_logged: bool) -> str:
    if ever_logged:
        lines = [f"⏳ No writeup logged in {days} days."]
    else:
        lines = [f"⏳ It's been {days} days and there are no writeups yet."]
    lines.append("Solve one challenge, any size, and /log it.")
    if focus:
        lines.append(f"Your focus is <b>{html.escape(focus)}</b>.")
    return "\n".join(lines)


def render_reminder(event: Event, tz: ZoneInfo, now: datetime) -> str:
    hours = max(1, round((event.start - now).total_seconds() / 3600))
    return f"⏰ Starts in about {hours}h\n\n{render_event(event, tz)}"


# Message size ------------------------------------------------------------------------------


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
