import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from ctfbot.models import clean_text

MAX_NAME_LENGTH = 100
MAX_NOTES_LENGTH = 1500

# Flag-shaped text: a short prefix glued to braces, e.g. picoCTF{...}, flag{...}, CTF{...}.
# The prefix needs 2+ characters and the body 4+ non-space characters, so code such as
# f"{x}" or "{ }" is left alone.
_FLAG_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{1,24}\{[^\s{}]{4,}\}")
REDACTED_FLAG = "[flag removed]"


class InvalidInput(ValueError):
    # Messages are shown to the owner in Telegram, so they must never echo the input.
    pass


@dataclass(frozen=True)
class WriteupDraft:
    platform: str
    challenge: str
    category: str
    solved: bool
    approach: str
    stuck: str


@dataclass(frozen=True)
class Writeup(WriteupDraft):
    id: int
    created_at: datetime


def clean_name(value: str, what: str) -> str:
    # Single line: platform and challenge names. Keeping one extra character lets us tell
    # "exactly at the limit" from "over it" without silently cutting the name.
    cleaned = clean_text(value, max_length=MAX_NAME_LENGTH + 1)
    if not cleaned:
        raise InvalidInput(f"The {what} can't be empty.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise InvalidInput(f"The {what} is too long (max {MAX_NAME_LENGTH} characters).")
    return cleaned


def clean_notes(value: str, what: str) -> str:
    # Multi-line notes: keep line breaks, drop every other control or invisible character.
    lines = value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept = ["".join(ch for ch in line if not _is_hidden(ch)).rstrip() for line in lines]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    if not cleaned:
        raise InvalidInput(f"The {what} can't be empty.")
    if len(cleaned) > MAX_NOTES_LENGTH:
        raise InvalidInput(
            f"That's too long ({len(cleaned)} characters, max {MAX_NOTES_LENGTH}). "
            "Send a shorter version."
        )
    return cleaned


def _is_hidden(ch: str) -> bool:
    # Category C covers control, format (bidi overrides, zero-width) and unassigned characters.
    return unicodedata.category(ch).startswith("C")


def redact_flags(text: str) -> tuple[str, int]:
    # A writeup database should never become a flag database: if it leaks, or is shared
    # during a live CTF, flags would be published.
    return _FLAG_PATTERN.subn(REDACTED_FLAG, text)
