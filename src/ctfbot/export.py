# Turns writeups into a ZIP of Markdown files. Pure functions: no Telegram, no database, no clock.
#
# The order of work matters. Everything is redacted first, and names and text are built from
# the redacted copy, so a flag in a challenge name can't survive inside a filename.
import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

from ctfbot import REPO_URL
from ctfbot.writeups import Writeup, hold_end, is_held, redact_flags

Keep = Callable[[Writeup], bool]

MAX_FILE_NAME = 80
_MAX_PLATFORM_SLUG = 20
_DATE_LENGTH = len("YYYY-MM-DD")
_SUFFIX = ".md"
_FALLBACK_SLUG = "untitled"
INDEX_NAME = "README.md"
# Fixed, so the same writeups give the same bytes. 1980 is the earliest date a ZIP can store.
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
# Characters that would otherwise start a link, an HTML tag, emphasis or a new table cell.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|&~])")

SOLVED_RESULT = "✅ Solved"
UNSOLVED_RESULT = "❌ Unsolved attempt"


@dataclass(frozen=True)
class Plan:
    ready: tuple[Writeup, ...]
    held_back: int
    # When the soonest hold ends. None if nothing is held, or no held writeup has a readable time.
    next_release: datetime | None


@dataclass(frozen=True)
class Archive:
    data: bytes
    redactions: int


# Selection ------------------------------------------------------------------------------------


def everything(_: Writeup) -> bool:
    return True


def created_since(cutoff: datetime) -> Keep:
    return lambda writeup: writeup.created_at >= cutoff


def in_category(name: str) -> Keep:
    # Case-insensitive, like the rest of the bot, so editing "crypto" to "Crypto" keeps old logs.
    wanted = name.casefold()
    return lambda writeup: writeup.category.casefold() == wanted


def new_since(last_export: datetime | None) -> Keep:
    # New means "became exportable since the last export", not "written since". A writeup held
    # past an export has an old created_at, and comparing that alone would skip it forever.
    # >= so one logged in the same second as the export repeats rather than goes missing.
    def keep(writeup: Writeup) -> bool:
        if last_export is None:
            return True
        if writeup.hold_until is None:
            return writeup.created_at >= last_export
        end = hold_end(writeup)
        # No readable end: keep it so plan_export counts it as held back.
        return end is None or max(writeup.created_at, end) >= last_export

    return keep


def plan_export(writeups: Iterable[Writeup], keep: Keep, now: datetime) -> Plan:
    chosen = [writeup for writeup in writeups if keep(writeup)]
    held = [writeup for writeup in chosen if is_held(writeup, now)]
    ends = [end for end in map(hold_end, held) if end is not None]
    return Plan(
        ready=tuple(writeup for writeup in chosen if not is_held(writeup, now)),
        held_back=len(held),
        next_release=min(ends, default=None),
    )


# Names ----------------------------------------------------------------------------------------


def slugify(text: str, max_length: int) -> str:
    # Lowercase a-z, 0-9 and single hyphens. That leaves no "/", "\\" or "." for a path to use.
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_length].strip("-") or _FALLBACK_SLUG


def file_name(writeup: Writeup, date: str) -> str:
    # The ID goes last and is never cut: it is what keeps every name unique.
    writeup_id = str(writeup.id)
    hyphens = 3
    room = MAX_FILE_NAME - _DATE_LENGTH - hyphens - len(writeup_id) - len(_SUFFIX)
    platform = slugify(writeup.platform, min(_MAX_PLATFORM_SLUG, room // 3))
    challenge = slugify(writeup.challenge, room - len(platform))
    return f"{date}-{platform}-{challenge}-{writeup_id}{_SUFFIX}"


def archive_name(now: datetime, tz: ZoneInfo) -> str:
    return f"ctf-writeups-{now.astimezone(tz):%Y-%m-%d}.zip"


# Markdown -------------------------------------------------------------------------------------


def _local_date(writeup: Writeup, tz: ZoneInfo) -> str:
    return f"{writeup.created_at.astimezone(tz):%Y-%m-%d}"


def _inline(text: str) -> str:
    # For headings and table cells. Line breaks would end the cell or heading early.
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def _front_matter(writeup: Writeup, date: str) -> list[str]:
    fields: dict[str, object] = {
        "id": writeup.id,
        "date": date,
        "platform": writeup.platform,
        "challenge": writeup.challenge,
        "category": writeup.category,
        "solved": writeup.solved,
        "live_ctf": writeup.hold_event or False,
    }
    # JSON is valid YAML. Quoting and escaping make a ":" or "---" plain text inside a string,
    # and ASCII-only output means no character in a value can start a new line or a new key.
    lines = [f"{key}: {json.dumps(value)}" for key, value in fields.items()]
    return ["---", *lines, "---"]


def render_markdown(writeup: Writeup, tz: ZoneInfo) -> str:
    lines = [
        *_front_matter(writeup, _local_date(writeup, tz)),
        "",
        f"# {_inline(writeup.challenge)}",
        "",
    ]
    if not writeup.solved:
        lines += [f"> {UNSOLVED_RESULT}", ""]
    lines += [f"## {'How I solved it' if writeup.solved else 'What I tried'}", "", writeup.approach]
    if writeup.stuck:
        lines += ["", "## Where I got stuck", "", writeup.stuck]
    lines += ["", "---", "", f"Exported by [ctf-prep-bot]({REPO_URL}).", ""]
    return "\n".join(lines)


def render_index(rows: Iterable[tuple[Writeup, str]], tz: ZoneInfo) -> str:
    lines = [
        "# CTF writeups",
        "",
        "| Date | Challenge | Platform | Category | Result |",
        "|---|---|---|---|---|",
    ]
    for writeup, name in rows:
        result = SOLVED_RESULT if writeup.solved else UNSOLVED_RESULT
        lines.append(
            f"| {_local_date(writeup, tz)} | [{_inline(writeup.challenge)}]({name}) | "
            f"{_inline(writeup.platform)} | {_inline(writeup.category)} | {result} |"
        )
    lines += ["", f"Exported by [ctf-prep-bot]({REPO_URL}).", ""]
    return "\n".join(lines)


# The archive ----------------------------------------------------------------------------------


def _redacted(writeup: Writeup) -> tuple[Writeup, int]:
    # Defense in depth. Notes were redacted at /log, but names never were, and a row may predate
    # a stricter pattern. Every piece of text that reaches the archive passes through here.
    total = 0

    def scrub(text: str) -> str:
        nonlocal total
        clean, removed = redact_flags(text)
        total += removed
        return clean

    return (
        replace(
            writeup,
            platform=scrub(writeup.platform),
            challenge=scrub(writeup.challenge),
            category=scrub(writeup.category),
            approach=scrub(writeup.approach),
            stuck=scrub(writeup.stuck),
            hold_event=scrub(writeup.hold_event) if writeup.hold_event else None,
        ),
        total,
    )


def build_archive(writeups: Iterable[Writeup], tz: ZoneInfo) -> Archive:
    # In memory only: an export must never leave a copy of the writeups on disk.
    newest_first = sorted(writeups, key=lambda writeup: writeup.id, reverse=True)
    redacted = [_redacted(writeup) for writeup in newest_first]
    rows = [(writeup, file_name(writeup, _local_date(writeup, tz))) for writeup, _ in redacted]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        _add(archive, INDEX_NAME, render_index(rows, tz))
        for writeup, name in rows:
            _add(archive, name, render_markdown(writeup, tz))
    return Archive(buffer.getvalue(), sum(removed for _, removed in redacted))


def _add(archive: zipfile.ZipFile, name: str, text: str) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, text.encode("utf-8"))
