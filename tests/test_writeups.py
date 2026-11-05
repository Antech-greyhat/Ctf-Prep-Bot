import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from ctfbot.writeups import (
    MAX_NAME_LENGTH,
    MAX_NOTES_LENGTH,
    REDACTED_FLAG,
    InvalidInput,
    Writeup,
    clean_name,
    clean_notes,
    hold_end,
    is_held,
    parse_hold_end,
    redact_flags,
)

# Written as escapes: these characters are invisible and get lost when code is copied.
ZERO_WIDTH_SPACE = "\u200b"
RIGHT_TO_LEFT_OVERRIDE = "\u202e"


def test_clean_name_collapses_whitespace_and_hidden_characters() -> None:
    raw = f"  pico{ZERO_WIDTH_SPACE}CTF \n 2026{RIGHT_TO_LEFT_OVERRIDE} "
    assert clean_name(raw, "platform") == "picoCTF 2026"


@pytest.mark.parametrize("value", ["", "   ", ZERO_WIDTH_SPACE, "\x1b\x07"])
def test_empty_names_are_rejected(value: str) -> None:
    with pytest.raises(InvalidInput, match="can't be empty"):
        clean_name(value, "platform")


def test_name_at_limit_is_kept_and_over_limit_rejected() -> None:
    assert clean_name("a" * MAX_NAME_LENGTH, "challenge name") == "a" * MAX_NAME_LENGTH
    with pytest.raises(InvalidInput, match="too long") as info:
        clean_name("secret" * 100, "challenge name")
    assert "secret" not in str(info.value)


def test_notes_keep_line_breaks_but_drop_control_characters() -> None:
    raw = "step 1\r\nstep\x1b[31m 2\x07\n\n\n\nstep 3  "
    assert clean_notes(raw, "notes") == "step 1\nstep[31m 2\n\nstep 3"


def test_notes_drop_zero_width_and_bidi_characters() -> None:
    raw = f"admin{ZERO_WIDTH_SPACE}\n{RIGHT_TO_LEFT_OVERRIDE}txt.exe"
    assert clean_notes(raw, "notes") == "admin\ntxt.exe"


def test_notes_length_limit_rejects_without_echo() -> None:
    assert clean_notes("x" * MAX_NOTES_LENGTH, "notes")
    with pytest.raises(InvalidInput, match="too long") as info:
        clean_notes("secret" * MAX_NOTES_LENGTH, "notes")
    assert "secret" not in str(info.value)


@pytest.mark.parametrize(
    "text",
    [
        "the flag was picoCTF{c00k1e_m0nst3r}",
        "flag{abcd}",
        "CTF{h3ll0_w0rld!}",
        "got HTB{s0m3_fl4g_h3r3} at the end",
        "CyberGameKE{n0t_a_r34l_fl4g}",
    ],
)
def test_flags_are_redacted(text: str) -> None:
    redacted, count = redact_flags(text)
    assert count == 1
    assert REDACTED_FLAG in redacted
    assert "{" not in redacted


@pytest.mark.parametrize(
    "text",
    [
        'used f"{x}" in python',
        'json body {"a": 1}',
        '{"user": {"id": 1, "admin": false}}',
        "if (x) { return }",
        "x{ab}",
        "a{abcdef}",
    ],
)
def test_code_is_not_mistaken_for_flags(text: str) -> None:
    assert redact_flags(text) == (text, 0)


def test_multiple_flags_are_counted() -> None:
    redacted, count = redact_flags("flag{aaaa} and flag{bbbb}")
    assert count == 2
    assert redacted == f"{REDACTED_FLAG} and {REDACTED_FLAG}"


# Holds ---------------------------------------------------------------------------------------

HOLD_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def held_writeup(hold_until: str | None) -> Writeup:
    return Writeup(
        platform="picoCTF",
        challenge="Cookies",
        category="Web",
        solved=True,
        approach="secret approach",
        stuck="",
        id=7,
        created_at=HOLD_NOW,
        hold_until=hold_until,
    )


@pytest.mark.parametrize(
    ("hold_until", "held"),
    [
        (None, False),
        ("2026-10-24T12:00:01+00:00", True),
        # The moment the hold ends it no longer applies.
        ("2026-10-24T12:00:00+00:00", False),
        ("2026-10-24T11:59:59+00:00", False),
        # Same instant written with another offset.
        ("2026-10-24T15:00:01+03:00", True),
    ],
)
def test_is_held(hold_until: str | None, held: bool) -> None:
    assert is_held(held_writeup(hold_until), HOLD_NOW) is held


@pytest.mark.parametrize(
    "damaged",
    [
        "garbage",
        "",
        # No offset: the moment in time would depend on the machine's timezone.
        "2020-01-01T00:00:00",
        "9999-99-99T00:00:00+00:00",
        # Converting this to UTC overflows.
        "0001-01-01T00:00:00+05:00",
    ],
)
def test_unreadable_hold_fails_safe_and_logs_only_the_id(
    damaged: str, caplog: pytest.LogCaptureFixture
) -> None:
    writeup = held_writeup(damaged)
    with caplog.at_level(logging.WARNING):
        assert is_held(writeup, HOLD_NOW) is True
    assert "#7" in caplog.text
    assert "secret approach" not in caplog.text
    if damaged:
        assert damaged not in caplog.text


def test_hold_end_is_none_when_unreadable_or_absent() -> None:
    assert hold_end(held_writeup(None)) is None
    assert hold_end(held_writeup("garbage")) is None
    assert hold_end(held_writeup("2026-10-24T12:00:00+00:00")) == HOLD_NOW


NEW_YORK = ZoneInfo("America/New_York")
BEFORE_DST_CHANGES = datetime(2026, 2, 1, tzinfo=UTC)


def test_time_repeated_when_clocks_go_back_uses_the_later_one() -> None:
    end = parse_hold_end("2026-11-01 01:30", datetime(2026, 10, 25, tzinfo=UTC), NEW_YORK)
    assert end == datetime(2026, 11, 1, 6, 30, tzinfo=UTC)


def test_time_skipped_when_clocks_go_forward_is_refused() -> None:
    with pytest.raises(InvalidInput, match="doesn't exist"):
        parse_hold_end("2026-03-08 02:30", datetime(2026, 3, 1, tzinfo=UTC), NEW_YORK)


@pytest.mark.parametrize(
    "text",
    ["2026-2-02 10:00", "2026-02-02T10:00", "2026-02-02 10:00:00", "٢٠٢٦-02-02 10:00", ""],
)
def test_hold_end_needs_the_exact_format(text: str) -> None:
    with pytest.raises(InvalidInput, match="YYYY-MM-DD HH:MM"):
        parse_hold_end(text, BEFORE_DST_CHANGES, NEW_YORK)


def test_hold_end_ignores_surrounding_spaces() -> None:
    end = parse_hold_end("  2026-02-02 10:00\n", BEFORE_DST_CHANGES, NEW_YORK)
    assert end == datetime(2026, 2, 2, 15, 0, tzinfo=UTC)
