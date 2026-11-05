# /export end to end: real Application and handlers, offline fake Telegram API.
import io
import logging
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctfbot import formatting
from ctfbot.categories import Categories
from ctfbot.export import INDEX_NAME
from ctfbot.storage import to_db_time
from ctfbot.writeup_handlers import LAST_EXPORT_KEY
from ctfbot.writeups import WriteupDraft
from tests.fake_telegram import OWNER, STRANGER, Harness, running_bot
from tests.helpers import FrozenClock

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
OPTIONS = ["exp:all", "exp:since", "exp:recent", "exp:category"]


def entry(
    challenge: str = "Cookies",
    category: str = "Web",
    solved: bool = True,
    approach: str = "SECRET-APPROACH",
) -> WriteupDraft:
    return WriteupDraft("picoCTF", challenge, category, solved, approach, "")


def exported_names(bot: Harness) -> list[str]:
    # File names inside the latest ZIP, without the index.
    [(_, data, _)] = bot.telegram.sent("sendDocument")[-1].files.values()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return [name for name in archive.namelist() if name != INDEX_NAME]


def documents(bot: Harness) -> int:
    return len(bot.telegram.sent("sendDocument"))


async def press_export(bot: Harness, *data: str) -> None:
    chat = bot.chat()
    await chat.send("/export")
    for press in data:
        await chat.press(press)


async def test_menu_offers_the_four_options(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        await bot.chat().send("/export")
        assert [data for _, data in bot.telegram.sent()[-1].buttons] == OPTIONS


async def test_export_all_sends_a_zip_to_the_owner(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        await press_export(bot, "exp:all")
        [document] = bot.telegram.sent("sendDocument")
        assert int(document.params["chat_id"]) == OWNER
        [(name, data, _)] = document.files.values()
        assert name == "ctf-writeups-2026-10-24.zip"
        assert data[:2] == b"PK"
        assert exported_names(bot) == ["2026-10-24-picoctf-cookies-1.md"]
        assert bot.telegram.last_text() == "📦 Exported 1 writeup(s)."


async def test_buttons_are_removed_before_the_file_is_sent(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        await press_export(bot, "exp:all")
        methods = [call.method for call in bot.telegram.calls]
        assert methods.index("editMessageText") < methods.index("sendDocument")
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.EXPORT_WORKING
        assert bot.telegram.sent("editMessageText")[-1].buttons == []


async def test_unsolved_attempts_are_included(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(solved=False), NOW)
        await press_export(bot, "exp:all")
        [(_, data, _)] = bot.telegram.sent("sendDocument")[-1].files.values()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            text = archive.read("2026-10-24-picoctf-cookies-1.md").decode("utf-8")
            index = archive.read(INDEX_NAME).decode("utf-8")
        assert "solved: false" in text
        assert "## What I tried" in text
        assert "❌ Unsolved attempt" in text
        assert "❌ Unsolved attempt" in index


@pytest.mark.parametrize(
    "presses", [("exp:all",), ("exp:since",), ("exp:recent",), ("exp:category", "exp:cat:1")]
)
async def test_every_option_holds_back_held_writeups(
    tmp_path: Path, presses: tuple[str, ...]
) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry("Free"), NOW - HOUR)
        bot.db.add_writeup(entry("Held"), NOW - HOUR, NOW + 2 * HOUR, "Hack.lu")
        bot.db.add_writeup(entry("Later"), NOW - HOUR, NOW + 5 * HOUR, "Hack.lu")
        await press_export(bot, *presses)
        assert exported_names(bot) == ["2026-10-24-picoctf-free-1.md"]
        reply = bot.telegram.last_text()
        assert "Exported 1 writeup(s)" in reply
        assert "Held back: 2" in reply
        assert "Next hold ends Sat 24 Oct 2026 17:00 EAT" in reply


@pytest.mark.parametrize(
    "presses", [("exp:all",), ("exp:since",), ("exp:recent",), ("exp:category", "exp:cat:1")]
)
async def test_every_option_excludes_an_unreadable_hold(
    tmp_path: Path, presses: tuple[str, ...]
) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        with bot.db._conn:
            bot.db._conn.execute("UPDATE writeups SET hold_until = 'garbage'")
        await press_export(bot, *presses)
        assert documents(bot) == 0
        assert bot.telegram.last_text() == "Nothing to export\n🔒 Held back: 1."


async def test_expired_hold_is_exported_and_keeps_the_event(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW, NOW + HOUR, "Hack.lu")
        clock.now = NOW + HOUR
        await press_export(bot, "exp:all")
        [(_, data, _)] = bot.telegram.sent("sendDocument")[-1].files.values()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            text = archive.read("2026-10-24-picoctf-cookies-1.md").decode("utf-8")
        assert 'live_ctf: "Hack.lu"' in text


async def test_released_writeup_is_exported_at_once(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        writeup_id = bot.db.add_writeup(entry(), NOW, NOW + DAY, "Hack.lu")
        await press_export(bot, "exp:all")
        assert documents(bot) == 0
        chat = bot.chat()
        await chat.send(f"/release {writeup_id}")
        await chat.press(f"rel:yes:{writeup_id}")
        await press_export(bot, "exp:all")
        assert documents(bot) == 1


# Since last export ----------------------------------------------------------------------------


async def test_since_last_export_only_sends_what_is_new(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry("First"), NOW - HOUR)
        await press_export(bot, "exp:since")
        assert exported_names(bot) == ["2026-10-24-picoctf-first-1.md"]
        assert bot.db.get_state(LAST_EXPORT_KEY) == to_db_time(NOW)

        await press_export(bot, "exp:since")
        assert documents(bot) == 1
        assert bot.telegram.last_text() == "Nothing to export"

        clock.now = NOW + HOUR
        bot.db.add_writeup(entry("Second"), clock.now)
        await press_export(bot, "exp:since")
        assert exported_names(bot) == ["2026-10-24-picoctf-second-2.md"]


async def test_failed_send_does_not_advance_since_last_export(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        bot.telegram.failing.add("sendDocument")
        with caplog.at_level(logging.WARNING):
            await press_export(bot, "exp:since")
        assert bot.db.get_state(LAST_EXPORT_KEY) is None
        assert bot.telegram.last_text() == formatting.EXPORT_FAILED
        assert "SECRET-APPROACH" not in caplog.text

        # The retry sends the same writeup.
        bot.telegram.failing.clear()
        clock.now = NOW + HOUR
        await press_export(bot, "exp:since")
        assert exported_names(bot) == ["2026-10-24-picoctf-cookies-1.md"]
        assert bot.db.get_state(LAST_EXPORT_KEY) == to_db_time(clock.now)


async def test_held_writeup_comes_through_once_after_its_hold_expires(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry("Free"), NOW - HOUR)
        bot.db.add_writeup(entry("Held"), NOW - HOUR, NOW + HOUR, "Hack.lu")
        await press_export(bot, "exp:since")
        assert exported_names(bot) == ["2026-10-24-picoctf-free-1.md"]

        clock.now = NOW + 2 * HOUR
        await press_export(bot, "exp:since")
        assert exported_names(bot) == ["2026-10-24-picoctf-held-2.md"]

        await press_export(bot, "exp:since")
        assert documents(bot) == 2


async def test_writeup_released_after_an_export_comes_through_since(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry("Free"), NOW - HOUR)
        held = bot.db.add_writeup(entry("Held"), NOW - HOUR, NOW + DAY, "Hack.lu")
        await press_export(bot, "exp:since")

        clock.now = NOW + HOUR
        chat = bot.chat()
        await chat.send(f"/release {held}")
        await chat.press(f"rel:yes:{held}")
        await press_export(bot, "exp:since")
        assert exported_names(bot) == ["2026-10-24-picoctf-held-2.md"]


@pytest.mark.parametrize("presses", [("exp:recent",), ("exp:category", "exp:cat:1")])
async def test_partial_exports_do_not_move_the_marker(
    tmp_path: Path, presses: tuple[str, ...]
) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        await press_export(bot, *presses)
        assert documents(bot) == 1
        assert bot.db.get_state(LAST_EXPORT_KEY) is None


async def test_export_all_moves_the_marker(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        await press_export(bot, "exp:all")
        assert bot.db.get_state(LAST_EXPORT_KEY) == to_db_time(NOW)


async def test_empty_export_does_not_move_the_marker(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW, NOW + HOUR, "Hack.lu")
        await press_export(bot, "exp:since")
        assert bot.db.get_state(LAST_EXPORT_KEY) is None


# Other options --------------------------------------------------------------------------------


async def test_last_30_days(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry("Old"), NOW - 31 * DAY)
        bot.db.add_writeup(entry("Recent"), NOW - 29 * DAY)
        await press_export(bot, "exp:recent")
        assert exported_names(bot) == ["2026-09-25-picoctf-recent-2.md"]


async def test_category_buttons_come_from_the_configured_list(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    categories = Categories(order=("Web", "Crypto"))
    async with running_bot(tmp_path, categories=categories, clock=clock) as bot:
        bot.db.add_writeup(entry("Site", "Web"), NOW)
        bot.db.add_writeup(entry("RSA", "crypto"), NOW)
        await press_export(bot, "exp:category")
        assert bot.telegram.sent("editMessageText")[-1].buttons == [
            ("Web", "exp:cat:0"),
            ("Crypto", "exp:cat:1"),
        ]
        await bot.chat().press("exp:cat:1")
        assert exported_names(bot) == ["2026-10-24-picoctf-rsa-2.md"]


@pytest.mark.parametrize("presses", [("exp:all",), ("exp:category", "exp:cat:1")])
async def test_empty_export_says_so_and_sends_no_file(
    tmp_path: Path, presses: tuple[str, ...]
) -> None:
    async with running_bot(tmp_path) as bot:
        await press_export(bot, *presses)
        assert documents(bot) == 0
        assert bot.telegram.last_text() == "Nothing to export"


async def test_everything_held_reports_when_the_next_hold_ends(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW, NOW + HOUR, "Hack.lu")
        await press_export(bot, "exp:all")
        assert documents(bot) == 0
        assert bot.telegram.last_text() == (
            "Nothing to export\n🔒 Held back: 1. Next hold ends Sat 24 Oct 2026 16:00 EAT."
        )


async def test_redactions_during_export_are_reported(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry("flag{challenge_name}", approach="uses CTF{in_notes}"), NOW)
        await press_export(bot, "exp:all")
        assert "Removed 2 flag-like string(s)" in bot.telegram.last_text()
        [name] = exported_names(bot)
        assert "challenge" not in name


async def test_export_does_not_disturb_a_log_in_progress(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        chat = bot.chat()
        await chat.send("/log")
        await chat.send("picoCTF")
        await press_export(bot, "exp:all")
        assert documents(bot) == 1
        await chat.press("log:live:no")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE


# Tampering and access -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    ["exp:", "exp:everything", "exp:all\n", "exp:all;x", "exp:cat:", "exp:cat:x", "exp:cat:123"],
)
async def test_tampered_data_exports_nothing(tmp_path: Path, data: str) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        await bot.chat().press(data)
        assert documents(bot) == 0
        assert bot.telegram.sent("answerCallbackQuery")[-1].params["text"] == (
            formatting.BUTTON_EXPIRED
        )


@pytest.mark.parametrize("data", ["exp:cat:7", "exp:cat:99"])
async def test_category_index_out_of_range_exports_nothing(tmp_path: Path, data: str) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        await bot.chat().press(data)
        assert documents(bot) == 0
        assert not bot.telegram.sent("sendMessage")


async def test_stranger_and_group_get_nothing(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)
        calls_before = len(bot.telegram.calls)
        for chat in (bot.chat(STRANGER), bot.chat(OWNER, "group")):
            await chat.send("/export")
            for data in ["exp:all", "exp:since", "exp:recent", "exp:category", "exp:cat:1"]:
                await chat.press(data)
        assert len(bot.telegram.calls) == calls_before
        assert bot.db.get_state(LAST_EXPORT_KEY) is None


async def test_database_error_in_export_is_not_swallowed_into_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        bot.db.add_writeup(entry(), NOW)

        def broken() -> list[object]:
            raise sqlite3.OperationalError("disk I/O error")

        monkeypatch.setattr(bot.db, "all_writeups", broken)
        await press_export(bot, "exp:all")
        assert documents(bot) == 0
        assert bot.db.get_state(LAST_EXPORT_KEY) is None
