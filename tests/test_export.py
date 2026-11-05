# export.py is pure, so these need no Telegram and no database.
import io
import json
import logging
import re
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ctfbot.export import (
    INDEX_NAME,
    MAX_FILE_NAME,
    Plan,
    archive_name,
    build_archive,
    created_since,
    everything,
    file_name,
    in_category,
    new_since,
    plan_export,
    render_index,
    render_markdown,
    slugify,
)
from ctfbot.writeups import REDACTED_FLAG, Writeup

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
NAIROBI = ZoneInfo("Africa/Nairobi")
DATE = "2026-10-24"
SAFE_NAME = re.compile(r"[a-z0-9-]+\.md")


def writeup(writeup_id: int = 7, **overrides: object) -> Writeup:
    fields: dict[str, object] = {
        "platform": "picoCTF",
        "challenge": "Cookies",
        "category": "Web",
        "solved": True,
        "approach": "Changed the cookie.",
        "stuck": "",
        "created_at": NOW - timedelta(days=1),
        **overrides,
    }
    return Writeup(id=writeup_id, **fields)


def unpack(data: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def parse_front_matter(text: str) -> dict[str, object]:
    # A strict reader of the only form we write: `key: <JSON value>` between two --- lines.
    lines = text.split("\n")
    assert lines[0] == "---"
    end = lines.index("---", 1)
    parsed: dict[str, object] = {}
    for line in lines[1:end]:
        key, _, value = line.partition(": ")
        assert key not in parsed
        parsed[key] = json.loads(value)
    return parsed


# Selection ------------------------------------------------------------------------------------


def hold(hours: float, base: datetime = NOW) -> str:
    return (base + timedelta(hours=hours)).isoformat()


def test_plan_holds_back_held_writeups_and_finds_the_next_release() -> None:
    free = writeup(1)
    soon = writeup(2, hold_until=hold(2))
    late = writeup(3, hold_until=hold(9))
    expired = writeup(4, hold_until=hold(-1))
    plan = plan_export([free, soon, late, expired], everything, NOW)
    assert plan == Plan((free, expired), 2, NOW + timedelta(hours=2))


def test_a_hold_ending_this_second_no_longer_applies() -> None:
    plan = plan_export([writeup(hold_until=hold(0))], everything, NOW)
    assert (len(plan.ready), plan.held_back) == (1, 0)


def test_unreadable_hold_is_held_back_with_no_known_end(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        plan = plan_export([writeup(9, hold_until="garbage")], everything, NOW)
    assert plan == Plan((), 1, None)
    assert "#9" in caplog.text
    assert "garbage" not in caplog.text


def test_held_count_only_covers_the_selection() -> None:
    web = writeup(1, category="Web", hold_until=hold(2))
    crypto = writeup(2, category="Crypto", hold_until=hold(1))
    plan = plan_export([web, crypto], in_category("Web"), NOW)
    assert plan == Plan((), 1, NOW + timedelta(hours=2))


def test_category_ignores_case() -> None:
    plan = plan_export([writeup(category="crypto")], in_category("Crypto"), NOW)
    assert len(plan.ready) == 1


def test_created_since_includes_the_cutoff() -> None:
    cutoff = NOW - timedelta(days=30)
    old = writeup(1, created_at=cutoff - timedelta(seconds=1))
    edge = writeup(2, created_at=cutoff)
    assert plan_export([old, edge], created_since(cutoff), NOW).ready == (edge,)


def test_first_export_counts_everything_as_new() -> None:
    assert plan_export([writeup()], new_since(None), NOW).ready == (writeup(),)


def test_new_since_skips_what_was_already_available() -> None:
    last = NOW - timedelta(hours=1)
    old = writeup(1, created_at=last - timedelta(hours=1))
    fresh = writeup(2, created_at=last + timedelta(minutes=1))
    assert plan_export([old, fresh], new_since(last), NOW).ready == (fresh,)


def test_writeup_logged_in_the_same_second_as_the_export_is_not_lost() -> None:
    edge = writeup(created_at=NOW)
    assert plan_export([edge], new_since(NOW), NOW).ready == (edge,)


def test_held_writeup_is_not_lost_when_its_hold_expires_after_an_export() -> None:
    # Logged before the last export, held through it, and free now.
    last = NOW - timedelta(hours=1)
    held_through = writeup(created_at=last - timedelta(days=1), hold_until=hold(-0.5))
    assert plan_export([held_through], new_since(last), NOW).ready == (held_through,)


def test_writeup_whose_hold_ended_before_the_last_export_is_not_repeated() -> None:
    last = NOW - timedelta(hours=1)
    done = writeup(created_at=last - timedelta(days=1), hold_until=hold(-2))
    assert plan_export([done], new_since(last), NOW).ready == ()


def test_since_reports_writeups_that_are_still_held() -> None:
    last = NOW - timedelta(hours=1)
    still = writeup(1, created_at=last - timedelta(days=1), hold_until=hold(3))
    damaged = writeup(2, created_at=last - timedelta(days=1), hold_until="garbage")
    assert plan_export([still, damaged], new_since(last), NOW) == Plan(
        (), 2, NOW + timedelta(hours=3)
    )


# Names ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "challenge",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "<script>alert(1)</script>",
        "/absolute",
        "a/../b",
        "nul\x00byte",
        "CON",
        "...",
        "тест",
        "🚩",
        "x" * 500,
    ],
)
def test_names_are_always_safe(challenge: str) -> None:
    name = file_name(writeup(challenge=challenge, platform=challenge), DATE)
    assert SAFE_NAME.fullmatch(name)
    assert len(name) <= MAX_FILE_NAME
    assert ".." not in name
    assert name.startswith(f"{DATE}-")
    assert name.endswith("-7.md")


def test_name_format() -> None:
    name = file_name(writeup(platform="Hack The Box", challenge="Baby's First RSA!"), DATE)
    assert name == "2026-10-24-hack-the-box-baby-s-first-rsa-7.md"


def test_long_names_keep_the_id_and_stay_within_the_limit() -> None:
    name = file_name(writeup(1234567890, platform="p" * 100, challenge="c" * 100), DATE)
    assert len(name) == MAX_FILE_NAME
    assert name.endswith("-1234567890.md")
    assert "ccc" in name


def test_identical_titles_get_different_names() -> None:
    assert file_name(writeup(1), DATE) != file_name(writeup(2), DATE)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Café Crème", "cafe-creme"), ("--a--b--", "a-b"), ("!!!", "untitled"), ("", "untitled")],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text, 40) == expected


def test_slug_cut_never_ends_in_a_hyphen() -> None:
    assert slugify("abc def", 4) == "abc"


def test_archive_name_uses_the_local_date() -> None:
    late_evening = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    assert archive_name(late_evening, NAIROBI) == "ctf-writeups-2026-10-25.zip"


# Markdown -------------------------------------------------------------------------------------


def test_solved_file_layout() -> None:
    created = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
    text = render_markdown(writeup(created_at=created, stuck="The path."), NAIROBI)
    assert text == (
        "---\n"
        "id: 7\n"
        'date: "2026-10-23"\n'
        'platform: "picoCTF"\n'
        'challenge: "Cookies"\n'
        'category: "Web"\n'
        "solved: true\n"
        "live_ctf: false\n"
        "---\n"
        "\n"
        "# Cookies\n"
        "\n"
        "## How I solved it\n"
        "\n"
        "Changed the cookie.\n"
        "\n"
        "## Where I got stuck\n"
        "\n"
        "The path.\n"
        "\n"
        "---\n"
        "\n"
        "Exported by [ctf-prep-bot](https://github.com/Antech-greyhat/ctf-prep-bot).\n"
    )


def test_empty_stuck_heading_is_omitted() -> None:
    assert "Where I got stuck" not in render_markdown(writeup(), NAIROBI)


def test_unsolved_attempt_is_clearly_marked() -> None:
    text = render_markdown(writeup(solved=False, stuck="Stuck here."), NAIROBI)
    assert "❌ Unsolved attempt" in text
    assert "## What I tried" in text
    assert "## How I solved it" not in text
    assert parse_front_matter(text)["solved"] is False


def test_date_uses_the_owner_timezone() -> None:
    created = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    assert parse_front_matter(render_markdown(writeup(created_at=created), NAIROBI))["date"] == (
        "2026-10-25"
    )


def test_live_ctf_is_the_event_title() -> None:
    text = render_markdown(writeup(hold_until=hold(-1), hold_event="Hack.lu CTF"), NAIROBI)
    assert parse_front_matter(text)["live_ctf"] == "Hack.lu CTF"


@pytest.mark.parametrize(
    "nasty",
    [
        "a: b",
        "---",
        "x\n---\ninjected: true",
        "x\ninjected: true",
        'quote " inside',
        "back\\slash",
        "# comment",
        "{json: yes}",
        "- list",
        "tab\there",
        "next line\u0085injected: true",
        "line sep\u2028injected: true",
        "🚩 emoji",
    ],
)
def test_nothing_in_a_value_can_break_the_front_matter(nasty: str) -> None:
    entry = writeup(platform=nasty, challenge=nasty, category=nasty, hold_event=nasty)
    text = render_markdown(entry, NAIROBI)
    parsed = parse_front_matter(text)
    assert list(parsed) == [
        "id",
        "date",
        "platform",
        "challenge",
        "category",
        "solved",
        "live_ctf",
    ]
    assert parsed["challenge"] == parsed["live_ctf"] == nasty
    assert "injected" not in parsed
    assert text.split("\n---\n")[0].isascii()


def test_heading_stays_on_one_line_and_is_inert() -> None:
    text = render_markdown(writeup(challenge="<img src=x>\n## fake [a](b)"), NAIROBI)
    assert "# \\<img src=x\\> ## fake \\[a\\](b)\n" in text


def test_index_table_escapes_cells_and_links_each_file() -> None:
    entry = writeup(challenge="a|b [c](javascript:x)", solved=False)
    text = render_index([(entry, "file-7.md")], NAIROBI)
    row = next(line for line in text.split("\n") if "file-7.md" in line)
    assert row == (
        "| 2026-10-23 | [a\\|b \\[c\\](javascript:x)](file-7.md) | picoCTF | Web | "
        "❌ Unsolved attempt |"
    )


# The archive ----------------------------------------------------------------------------------


def test_archive_holds_an_index_and_one_file_per_writeup() -> None:
    files = unpack(build_archive([writeup(1), writeup(2, solved=False)], NAIROBI).data)
    assert list(files) == [
        INDEX_NAME,
        "2026-10-23-picoctf-cookies-2.md",
        "2026-10-23-picoctf-cookies-1.md",
    ]
    index = files[INDEX_NAME]
    assert "(2026-10-23-picoctf-cookies-1.md)" in index
    assert "(2026-10-23-picoctf-cookies-2.md)" in index
    assert "❌ Unsolved attempt" in index


def test_archive_member_names_are_all_safe() -> None:
    entries = [
        writeup(1, challenge="../../etc/passwd"),
        writeup(2, challenge="<script>", platform="..\\.."),
    ]
    with zipfile.ZipFile(io.BytesIO(build_archive(entries, NAIROBI).data)) as archive:
        names = archive.namelist()
        assert archive.testzip() is None
    assert len(names) == len(set(names)) == 3
    for name in names:
        assert name == INDEX_NAME or SAFE_NAME.fullmatch(name)


def test_same_writeups_give_the_same_bytes() -> None:
    assert build_archive([writeup()], NAIROBI) == build_archive([writeup()], NAIROBI)


def test_flags_are_removed_from_all_text_and_counted() -> None:
    entry = writeup(
        platform="pico{plat_flag}",
        challenge="CTF{chal_flag}",
        category="cat{cate_flag}",
        approach="flag{note_flag}",
        stuck="flag{stuck_flag}",
        hold_event="evt{event_flag}",
    )
    archive = build_archive([entry], NAIROBI)
    assert archive.redactions == 6
    files = unpack(archive.data)
    joined = "\n".join(files.values()) + "\n".join(files)
    for secret in ("plat_flag", "chal_flag", "cate_flag", "note_flag", "stuck_flag", "event_flag"):
        assert secret not in joined
    assert REDACTED_FLAG in files[next(name for name in files if name != INDEX_NAME)]


def test_flag_in_a_challenge_name_never_reaches_the_file_name() -> None:
    names = unpack(build_archive([writeup(challenge="flag{abc123}")], NAIROBI).data)
    assert not any("abc123" in name for name in names)
    assert "2026-10-23-picoctf-flag-removed-7.md" in names


def test_clean_writeups_report_no_redactions() -> None:
    assert build_archive([writeup()], NAIROBI).redactions == 0


def test_archive_leaves_the_writeups_it_was_given_untouched() -> None:
    entry = writeup(approach="flag{note_flag}")
    copy = replace(entry)
    build_archive([entry], NAIROBI)
    assert entry == copy
