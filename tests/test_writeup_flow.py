# End-to-end tests: real Application and handlers, offline fake Telegram API.
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctfbot import formatting
from ctfbot.categories import Categories
from ctfbot.writeup_handlers import DRAFT_KEY, parse_writeup_id
from ctfbot.writeups import MAX_NOTES_LENGTH, REDACTED_FLAG
from tests.fake_telegram import OWNER, STRANGER, Chat, Harness, running_bot
from tests.test_writeups import ZERO_WIDTH_SPACE


async def log_until_notes(chat: Chat, solved: bool = True, category: int = 1) -> None:
    await chat.send("/log")
    await chat.send("picoCTF")
    await chat.send("Cookies")
    await chat.press(f"log:cat:{category}")
    await chat.press("log:solved" if solved else "log:unsolved")


async def save_one(bot: Harness, challenge: str = "Cookies") -> int:
    chat = bot.chat()
    await chat.send("/log")
    await chat.send("picoCTF")
    await chat.send(challenge)
    await chat.press("log:cat:1")
    await chat.press("log:solved")
    await chat.send("approach")
    await chat.send("/skip")
    return bot.db.recent_writeups(1)[0].id


# The /log conversation ----------------------------------------------------------------------


async def test_full_solved_writeup_is_saved(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat)
        assert bot.db.recent_writeups(5) == []
        await chat.send("Edited the cookie in devtools.\n\nThen refreshed.")
        assert bot.db.recent_writeups(5) == []
        await chat.send("/skip")
        [saved] = bot.db.recent_writeups(5)
        assert (saved.platform, saved.challenge, saved.category) == ("picoCTF", "Cookies", "Web")
        assert saved.solved is True
        assert saved.approach == "Edited the cookie in devtools.\n\nThen refreshed."
        assert saved.stuck == ""
        assert f"Saved writeup #{saved.id}" in bot.telegram.last_text()
        assert DRAFT_KEY not in bot.app.user_data[OWNER]


async def test_unsolved_writeup_requires_where_you_got_stuck(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat, solved=False)
        assert bot.telegram.last_text() == formatting.log_ask_approach(False)
        await chat.send("Tried SQL injection on the login form.")
        assert bot.telegram.last_text() == formatting.log_ask_stuck(False)
        await chat.send("/skip")
        assert bot.telegram.last_text() == formatting.LOG_STUCK_REQUIRED
        assert bot.db.recent_writeups(5) == []
        await chat.send("Could not find the injectable parameter.")
        [saved] = bot.db.recent_writeups(5)
        assert saved.solved is False
        assert saved.stuck == "Could not find the injectable parameter."


async def test_category_buttons_follow_configured_order(tmp_path: Path) -> None:
    categories = Categories(order=("Web", "Crypto"))
    async with running_bot(tmp_path, categories=categories) as bot:
        chat = bot.chat()
        await chat.send("/log")
        await chat.send("picoCTF")
        await chat.send("Cookies")
        assert bot.telegram.sent()[-1].buttons == [("Web", "log:cat:0"), ("Crypto", "log:cat:1")]


async def test_flags_are_removed_before_saving(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat)
        await chat.send("The answer was picoCTF{s3cr3t_c00k1e}")
        await chat.send("Almost pasted flag{another_one} here")
        [saved] = bot.db.recent_writeups(1)
        assert "s3cr3t" not in saved.approach + saved.stuck
        assert REDACTED_FLAG in saved.approach
        assert "Removed 2 flag-like string(s)" in bot.telegram.last_text()


async def test_invalid_answer_asks_again_instead_of_moving_on(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await chat.send("/log")
        await chat.send(ZERO_WIDTH_SPACE)
        assert "can't be empty" in bot.telegram.last_text()
        await chat.send("picoCTF")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE


async def test_overlong_notes_are_rejected_without_echo(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat)
        await chat.send("z" * (MAX_NOTES_LENGTH + 5))
        assert "too long" in bot.telegram.last_text()
        assert "zzzz" not in bot.telegram.last_text()
        await chat.send("Short version.")
        assert bot.telegram.last_text() == formatting.log_ask_stuck(True)


async def test_skip_at_another_step_is_answered_and_the_question_stands(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await chat.send("/skip")
        assert bot.telegram.last_text() == formatting.NOTHING_TO_SKIP
        await chat.send("/log")
        await chat.send("/skip")
        assert bot.telegram.last_text() == formatting.NOTHING_TO_SKIP
        await chat.send("picoCTF")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE
        assert bot.app.user_data[OWNER][DRAFT_KEY].platform == "picoCTF"


async def test_typing_instead_of_tapping_a_button(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await chat.send("/log")
        await chat.send("picoCTF")
        await chat.send("Cookies")
        await chat.send("Web")
        assert bot.telegram.last_text() == formatting.LOG_USE_BUTTONS
        await chat.press("log:cat:1")
        await chat.send("yes")
        assert bot.telegram.last_text() == formatting.LOG_USE_BUTTONS


@pytest.mark.parametrize(
    "data", ["log:cat:99", "log:cat:1x", "log:cat:-1", "log:cat:", "log:cat:1\n", "log:solved"]
)
async def test_tampered_category_data_is_ignored(tmp_path: Path, data: str) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await chat.send("/log")
        await chat.send("picoCTF")
        await chat.send("Cookies")
        await chat.press(data)
        # Every press is answered, so the client's spinner always stops.
        assert len(bot.telegram.sent("answerCallbackQuery")) == 1
        await chat.press("log:cat:2")
        edits = [call.text for call in bot.telegram.sent("editMessageText")]
        assert edits == ["Category: <b>Forensics</b>"]


async def test_cancel_discards_the_draft(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat)
        await chat.send("/cancel")
        assert bot.telegram.last_text() == formatting.LOG_CANCELLED
        assert DRAFT_KEY not in bot.app.user_data[OWNER]
        await chat.send("this text is not an answer any more")
        assert bot.db.recent_writeups(5) == []
        await chat.send("/cancel")
        assert bot.telegram.last_text() == formatting.NOTHING_TO_CANCEL


async def test_log_again_starts_fresh(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat)
        await chat.send("/log")
        assert bot.telegram.last_text() == formatting.LOG_ASK_PLATFORM
        await chat.send("TryHackMe")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE
        assert bot.app.user_data[OWNER][DRAFT_KEY].platform == "TryHackMe"
        assert bot.app.user_data[OWNER][DRAFT_KEY].category == ""


async def test_idle_log_times_out_and_tells_the_owner(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await chat.send("/log")
        await chat.send("picoCTF")
        [job] = bot.log_conversation().timeout_jobs.values()
        delay = job.next_t - datetime.now(UTC)
        assert timedelta(minutes=29) < delay <= timedelta(minutes=30)
        # Run the scheduled timeout now instead of waiting 30 minutes.
        await job.run(bot.app)
        assert bot.telegram.sent()[-1].params["chat_id"] == OWNER
        assert bot.telegram.last_text() == formatting.LOG_TIMED_OUT
        assert DRAFT_KEY not in bot.app.user_data[OWNER]
        await chat.send("Cookies")
        assert bot.telegram.last_text() == formatting.LOG_TIMED_OUT
        assert bot.db.recent_writeups(5) == []


async def test_other_commands_still_work_mid_log(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await chat.send("/log")
        await chat.send("/help")
        assert "/log" in bot.telegram.last_text()
        await chat.send("picoCTF")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE


async def test_save_failure_keeps_the_draft_and_logs_no_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat)
        await chat.send("approach with flag{aaaa}")

        def broken(*_: object) -> int:
            raise sqlite3.OperationalError("disk I/O error")

        with monkeypatch.context() as patch, caplog.at_level(logging.ERROR):
            patch.setattr(bot.db, "add_writeup", broken)
            await chat.send("private stuck notes")
        assert bot.telegram.last_text() == formatting.LOG_SAVE_FAILED
        assert "Could not save writeup" in caplog.text
        assert "private stuck notes" not in caplog.text
        assert "approach with" not in caplog.text

        await chat.send("private stuck notes")
        [saved] = bot.db.recent_writeups(5)
        assert saved.stuck == "private stuck notes"
        # The flag in the approach is counted once, not once per attempt.
        assert "Removed 1 flag-like string(s)" in bot.telegram.last_text()


# Strangers, groups and stale buttons --------------------------------------------------------


async def test_stranger_gets_no_reply_at_all(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        writeup_id = await save_one(bot)
        calls_before = len(bot.telegram.calls)
        stranger = bot.chat(STRANGER)
        for text in ("/log", "picoCTF", "/recent", f"/show {writeup_id}", "/stats", "/cancel"):
            await stranger.send(text)
        await stranger.press("log:cat:1")
        await stranger.press(f"del:yes:{writeup_id}")
        assert len(bot.telegram.calls) == calls_before
        assert bot.db.get_writeup(writeup_id) is not None


async def test_owner_in_a_group_gets_no_reply_at_all(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        writeup_id = await save_one(bot)
        calls_before = len(bot.telegram.calls)
        group = bot.chat(OWNER, "group")
        await group.send("/log")
        await group.send("/recent")
        await group.press(f"del:yes:{writeup_id}")
        assert len(bot.telegram.calls) == calls_before
        assert bot.db.get_writeup(writeup_id) is not None


async def test_stale_button_is_answered_and_removed(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        await bot.chat().press("log:cat:1")
        [answer] = bot.telegram.sent("answerCallbackQuery")
        assert answer.params["text"] == formatting.BUTTON_EXPIRED
        assert bot.telegram.sent("editMessageReplyMarkup")


async def test_button_from_an_earlier_step_is_stale(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await log_until_notes(chat)
        await chat.press("log:cat:3")
        assert bot.telegram.sent("answerCallbackQuery")[-1].params["text"] == (
            formatting.BUTTON_EXPIRED
        )
        assert bot.app.user_data[OWNER][DRAFT_KEY].category == "Web"


# /recent, /show, /delete --------------------------------------------------------------------


async def test_recent_lists_newest_first(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await chat.send("/recent")
        assert "No writeups yet" in bot.telegram.last_text()
        first = await save_one(bot, "First")
        second = await save_one(bot, "Second")
        await chat.send("/recent")
        text = bot.telegram.last_text()
        assert text.index(f"#{second}") < text.index(f"#{first}")


async def test_show_escapes_user_text(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        writeup_id = await save_one(bot, "<b>x</b> & <script>")
        await bot.chat().send(f"/show {writeup_id}")
        text = bot.telegram.last_text()
        assert "&lt;b&gt;x&lt;/b&gt; &amp; &lt;script&gt;" in text
        assert "<script>" not in text
        assert bot.telegram.sent()[-1].params["parse_mode"] == "HTML"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/show", formatting.usage("show")),
        ("/show abc", formatting.usage("show")),
        ("/show 1 2", formatting.usage("show")),
        ("/show 0", formatting.usage("show")),
        ("/show 404", formatting.not_found(404)),
        ("/delete", formatting.usage("delete")),
        ("/delete 404", formatting.not_found(404)),
    ],
)
async def test_bad_ids(tmp_path: Path, command: str, expected: str) -> None:
    async with running_bot(tmp_path) as bot:
        await bot.chat().send(command)
        assert bot.telegram.last_text() == expected


async def test_delete_asks_first_and_can_be_declined(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        writeup_id = await save_one(bot)
        chat = bot.chat()
        await chat.send(f"/delete {writeup_id}")
        assert bot.db.get_writeup(writeup_id) is not None
        assert bot.telegram.sent()[-1].buttons == [
            ("🗑 Yes, delete", f"del:yes:{writeup_id}"),
            ("Keep it", f"del:no:{writeup_id}"),
        ]
        await chat.press(f"del:no:{writeup_id}")
        assert bot.db.get_writeup(writeup_id) is not None
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.kept(writeup_id)


async def test_delete_confirmed_and_stale_confirm(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        writeup_id = await save_one(bot)
        chat = bot.chat()
        await chat.send(f"/delete {writeup_id}")
        await chat.press(f"del:yes:{writeup_id}")
        assert bot.db.get_writeup(writeup_id) is None
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.deleted(writeup_id)
        await chat.press(f"del:yes:{writeup_id}")
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.not_found(writeup_id)
        # The next writeup gets a new ID.
        assert await save_one(bot) > writeup_id


@pytest.mark.parametrize(
    "data", ["del:yes:1;DROP", "del:maybe:1", "del:yes:", "del:yes:12345678901", "del:yes:-1"]
)
async def test_tampered_delete_data_deletes_nothing(tmp_path: Path, data: str) -> None:
    async with running_bot(tmp_path) as bot:
        writeup_id = await save_one(bot)
        await bot.chat().press(data)
        assert bot.db.get_writeup(writeup_id) is not None
        assert bot.telegram.sent("answerCallbackQuery")[-1].params["text"] == (
            formatting.BUTTON_EXPIRED
        )


@pytest.mark.parametrize(
    ("args", "expected"),
    [(["12"], 12), (["#12"], 12), ([], None), (["0"], None), (["1", "2"], None), (["١٢"], None)],
)
def test_parse_writeup_id(args: list[str], expected: int | None) -> None:
    assert parse_writeup_id(args) == expected
