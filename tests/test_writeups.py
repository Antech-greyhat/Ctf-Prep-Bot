import pytest

from ctfbot.writeups import (
    MAX_NAME_LENGTH,
    MAX_NOTES_LENGTH,
    REDACTED_FLAG,
    InvalidInput,
    clean_name,
    clean_notes,
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
