# The live-CTF step in /log, the 🔒 markers and /release. Real Application, offline fakes.
import html
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctfbot import formatting
from ctfbot.storage import to_db_time
from ctfbot.writeup_handlers import DRAFT_KEY, MAX_BUTTON_TITLE, MAX_EVENT_BUTTONS
from ctfbot.writeups import WriteupDraft, is_held
from tests.fake_telegram import OWNER, STRANGER, Chat, Harness, offline_ctftime, running_bot
from tests.helpers import FrozenClock, make_event

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=9)
RUNNING = make_event("Hack.lu <CTF> 2026", NOW - timedelta(hours=9), hours=18, source="manual")
UPCOMING = make_event("Next week CTF", NOW + timedelta(days=7), source="manual")


def ctftime_entry(title: str, start: datetime, hours: int) -> dict[str, object]:
    return {
        "id": 1,
        "title": title,
        "url": "https://ctf.example.org/",
        "start": start.isoformat(),
        "finish": (start + timedelta(hours=hours)).isoformat(),
        "onsite": False,
        "format": "Jeopardy",
        "restrictions": "Open",
    }


async def to_live_question(chat: Chat) -> None:
    await chat.send("/log")
    await chat.send("picoCTF")


async def finish_log(chat: Chat) -> None:
    await chat.send("Cookies")
    await chat.press("log:cat:1")
    await chat.press("log:solved")
    await chat.send("approach")
    await chat.send("/skip")


def draft(bot: Harness) -> object:
    return bot.app.user_data[OWNER][DRAFT_KEY]


async def log_held(bot: Harness) -> int:
    chat = bot.chat()
    await to_live_question(chat)
    await chat.press("log:live:yes")
    await chat.press("log:event:0")
    await finish_log(chat)
    return bot.db.recent_writeups(1)[0].id


# The new step ---------------------------------------------------------------------------------


async def test_live_question_comes_right_after_platform(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        await to_live_question(bot.chat())
        assert bot.telegram.last_text() == formatting.LOG_ASK_LIVE
        assert bot.telegram.sent()[-1].buttons == [
            ("Yes", "log:live:yes"),
            ("No", "log:live:no"),
        ]


async def test_answering_no_saves_without_a_hold(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:no")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE
        await finish_log(chat)
        [saved] = bot.db.recent_writeups(1)
        assert (saved.hold_until, saved.hold_event) == (None, None)
        assert "Private" not in bot.telegram.last_text()


async def test_yes_lists_only_running_events_from_both_sources(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    entries = [
        ctftime_entry("From CTFtime", NOW - timedelta(hours=1), 3),
        ctftime_entry("Finished", NOW - timedelta(days=2), 3),
    ]
    http = offline_ctftime(payload=entries)
    async with running_bot(
        tmp_path, manual_events=[RUNNING, UPCOMING], http=http, clock=clock
    ) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        assert bot.telegram.sent()[-1].buttons == [
            ("From CTFtime", "log:event:0"),
            ("Hack.lu <CTF> 2026", "log:event:1"),
            (formatting.NOT_LISTED_LABEL, "log:event:none"),
        ]


async def test_picking_an_event_holds_until_it_finishes(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        await chat.press("log:event:0")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE
        chosen = bot.telegram.sent("editMessageText")[-1].text
        assert chosen == f"Live CTF: <b>{html.escape(RUNNING.title)}</b>"
        await finish_log(chat)
        [saved] = bot.db.recent_writeups(1)
        assert saved.hold_until == to_db_time(RUNNING.finish)
        assert saved.hold_event == RUNNING.title
        assert is_held(saved, NOW)
        assert not is_held(saved, RUNNING.finish)
        assert "🔒 Private until Sun 25 Oct 2026 00:00 EAT" in bot.telegram.last_text()


async def test_buttons_are_capped_at_eight_soonest_finish_first(tmp_path: Path) -> None:
    events = [
        make_event(f"Event {n}", NOW - timedelta(hours=1), hours=2 + n, source="manual")
        for n in range(MAX_EVENT_BUTTONS + 3)
    ]
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=events, clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        buttons = bot.telegram.sent()[-1].buttons
        assert len(buttons) == MAX_EVENT_BUTTONS + 1
        assert buttons[0][0] == "Event 0"
        assert buttons[-1][1] == "log:event:none"
        assert len(draft(bot).running) == MAX_EVENT_BUTTONS


async def test_long_title_is_cut_on_the_button_but_kept_in_full(tmp_path: Path) -> None:
    title = "x" * 150
    event = make_event(title, NOW - timedelta(hours=1), source="manual")
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[event], clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        assert len(bot.telegram.sent()[-1].buttons[0][0]) == MAX_BUTTON_TITLE
        await chat.press("log:event:0")
        await finish_log(chat)
        assert bot.db.recent_writeups(1)[0].hold_event == title


@pytest.mark.parametrize(
    "data", ["log:event:1", "log:event:7", "log:event:99", "log:event:", "log:event:0\n"]
)
async def test_tampered_event_index_is_ignored(tmp_path: Path, data: str) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        await chat.press(data)
        assert draft(bot).hold_until is None
        assert "Which CTF?" in bot.telegram.last_text()
        await chat.press("log:event:0")
        assert draft(bot).hold_until == RUNNING.finish


@pytest.mark.parametrize("data", ["log:live:maybe", "log:live:", "log:live:yes\n"])
async def test_tampered_live_answer_is_ignored(tmp_path: Path, data: str) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press(data)
        assert bot.telegram.last_text() == formatting.LOG_ASK_LIVE
        await chat.press("log:live:no")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE


async def test_event_button_from_an_old_list_is_stale(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        await chat.press("log:event:0")
        await chat.press("log:event:0")
        assert bot.telegram.sent("answerCallbackQuery")[-1].params["text"] == (
            formatting.BUTTON_EXPIRED
        )


async def test_typing_at_the_button_steps(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.send("yes")
        assert bot.telegram.last_text() == formatting.LOG_USE_BUTTONS
        await chat.press("log:live:yes")
        await chat.send("Hack.lu")
        assert bot.telegram.last_text() == formatting.LOG_USE_BUTTONS
        assert draft(bot).hold_until is None


async def test_ctftime_down_still_offers_manual_events_and_not_listed(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    http = offline_ctftime(status=404)
    async with running_bot(tmp_path, manual_events=[RUNNING], http=http, clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        message = bot.telegram.sent()[-1]
        assert "CTFtime is unavailable" in message.text
        assert [label for label, _ in message.buttons] == [
            RUNNING.title,
            formatting.NOT_LISTED_LABEL,
        ]
        await chat.press("log:event:0")
        assert draft(bot).hold_until == RUNNING.finish


async def test_ctftime_down_with_no_manual_events_offers_not_listed(tmp_path: Path) -> None:
    http = offline_ctftime(status=404)
    async with running_bot(tmp_path, http=http) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        message = bot.telegram.sent()[-1]
        assert "CTFtime is unavailable" in message.text
        assert message.buttons == [(formatting.NOT_LISTED_LABEL, "log:event:none")]


async def test_nothing_running_says_so(tmp_path: Path) -> None:
    async with running_bot(tmp_path) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        message = bot.telegram.sent()[-1]
        assert "can't see any CTF" in message.text
        assert "unavailable" not in message.text


# "Not listed" ---------------------------------------------------------------------------------


async def to_hold_end(chat: Chat) -> None:
    await to_live_question(chat)
    await chat.press("log:live:yes")
    await chat.press("log:event:none")


async def test_not_listed_with_a_valid_time(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        chat = bot.chat()
        await to_hold_end(chat)
        assert "Africa/Nairobi" in bot.telegram.last_text()
        await chat.send("2026-10-25 18:00")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE
        await finish_log(chat)
        [saved] = bot.db.recent_writeups(1)
        # 18:00 in Nairobi is 15:00 UTC.
        assert saved.hold_until == "2026-10-25T15:00:00+00:00"
        assert saved.hold_event == "picoCTF"
        assert "🔒 Private until Sun 25 Oct 2026 18:00 EAT" in bot.telegram.last_text()


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("2026-10-24 15:00", "has passed"),
        ("2026-10-01 10:00", "has passed"),
        ("2026-11-24 15:01", "too far ahead"),
        ("2027-10-24 12:00", "too far ahead"),
        ("not a date <b>", "YYYY-MM-DD HH:MM"),
        ("2026-13-45 99:99", "YYYY-MM-DD HH:MM"),
        ("0001-01-01 00:00", "YYYY-MM-DD HH:MM"),
        ("9999-12-31 23:59", "too far ahead"),
    ],
)
async def test_invalid_times_ask_again_without_echoing(
    tmp_path: Path, answer: str, expected: str
) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        chat = bot.chat()
        await to_hold_end(chat)
        await chat.send(answer)
        reply = bot.telegram.last_text()
        assert expected in reply
        assert answer not in reply
        assert draft(bot).hold_until is None
        await chat.send("2026-10-25 18:00")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE


async def test_the_limit_itself_is_allowed(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        chat = bot.chat()
        await to_hold_end(chat)
        # Exactly 30 days from noon UTC, written in Nairobi time.
        await chat.send("2026-11-23 15:00")
        assert bot.telegram.last_text() == formatting.LOG_ASK_CHALLENGE


async def test_event_that_finishes_during_log_shows_no_lock(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        await chat.press("log:event:0")
        clock.now = RUNNING.finish
        await finish_log(chat)
        assert "Private" not in bot.telegram.last_text()
        [saved] = bot.db.recent_writeups(1)
        assert saved.hold_event == RUNNING.title
        assert not is_held(saved, clock.now)


async def test_log_again_drops_the_earlier_hold(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        chat = bot.chat()
        await to_live_question(chat)
        await chat.press("log:live:yes")
        await chat.press("log:event:0")
        await to_live_question(chat)
        await chat.press("log:live:no")
        await finish_log(chat)
        assert bot.db.recent_writeups(1)[0].hold_until is None


# Markers --------------------------------------------------------------------------------------


async def test_recent_and_show_mark_held_writeups(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        writeup_id = await log_held(bot)
        chat = bot.chat()
        for command in ("/recent", f"/show {writeup_id}"):
            await chat.send(command)
            assert "🔒 Private until Sun 25 Oct 2026 00:00 EAT" in bot.telegram.last_text()


async def test_marker_disappears_when_the_hold_expires_and_nothing_is_sent(
    tmp_path: Path,
) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, manual_events=[RUNNING], clock=clock) as bot:
        writeup_id = await log_held(bot)
        # No job is scheduled for the release, so there is nothing that could send a message.
        assert {job.name for job in bot.app.job_queue.jobs()} == {"weekly-digest"}
        clock.now = RUNNING.finish
        chat = bot.chat()
        await chat.send("/recent")
        assert "🔒" not in bot.telegram.last_text()
        await chat.send(f"/show {writeup_id}")
        assert "🔒" not in bot.telegram.last_text()


async def test_unreadable_hold_is_marked_and_can_be_released(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        writeup_id = bot.db.add_writeup(plain_draft(), NOW)
        with bot.db._conn:
            bot.db._conn.execute("UPDATE writeups SET hold_until = 'garbage'")
        chat = bot.chat()
        await chat.send("/recent")
        assert "unreadable" in bot.telegram.last_text()
        await chat.send(f"/release {writeup_id}")
        await chat.press(f"rel:yes:{writeup_id}")
        assert not is_held(bot.db.get_writeup(writeup_id), NOW)


# /release -------------------------------------------------------------------------------------


def plain_draft() -> WriteupDraft:
    return WriteupDraft("picoCTF", "Cookies", "Web", True, "approach", "")


async def test_release_asks_first_and_can_be_declined(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        writeup_id = bot.db.add_writeup(plain_draft(), NOW, LATER, "Hack.lu")
        chat = bot.chat()
        await chat.send(f"/release {writeup_id}")
        assert bot.telegram.sent()[-1].buttons == [
            ("🔓 Yes, release", f"rel:yes:{writeup_id}"),
            ("Keep private", f"rel:no:{writeup_id}"),
        ]
        assert is_held(bot.db.get_writeup(writeup_id), NOW)
        await chat.press(f"rel:no:{writeup_id}")
        assert is_held(bot.db.get_writeup(writeup_id), NOW)
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.still_held(writeup_id)


async def test_release_confirmed_ends_the_hold_and_keeps_the_event(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        writeup_id = bot.db.add_writeup(plain_draft(), NOW, LATER, "Hack.lu")
        chat = bot.chat()
        await chat.send(f"/release {writeup_id}")
        clock.now = NOW + timedelta(minutes=1)
        await chat.press(f"rel:yes:{writeup_id}")
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.released(writeup_id)
        released = bot.db.get_writeup(writeup_id)
        assert not is_held(released, clock.now)
        assert released.hold_event == "Hack.lu"
        # Stored as the release moment: "since last export" depends on it.
        assert released.hold_until == to_db_time(clock.now)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/release", formatting.usage("release")),
        ("/release abc", formatting.usage("release")),
        ("/release 0", formatting.usage("release")),
        ("/release 404", formatting.not_found(404)),
    ],
)
async def test_release_bad_ids(tmp_path: Path, command: str, expected: str) -> None:
    async with running_bot(tmp_path) as bot:
        await bot.chat().send(command)
        assert bot.telegram.last_text() == expected


async def test_release_of_an_unheld_writeup_says_so(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        writeup_id = bot.db.add_writeup(plain_draft(), NOW)
        await bot.chat().send(f"/release {writeup_id}")
        assert bot.telegram.last_text() == formatting.not_held(writeup_id)
        assert bot.telegram.sent()[-1].buttons == []


async def test_confirm_button_rechecks_the_writeup(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        expired = bot.db.add_writeup(plain_draft(), NOW, LATER, "Hack.lu")
        chat = bot.chat()
        await chat.send(f"/release {expired}")
        clock.now = LATER
        await chat.press(f"rel:yes:{expired}")
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.not_held(expired)
        # The natural end stays on record instead of being overwritten.
        assert bot.db.get_writeup(expired).hold_until == to_db_time(LATER)
        await chat.press("rel:yes:404")
        assert bot.telegram.sent("editMessageText")[-1].text == formatting.not_found(404)


@pytest.mark.parametrize(
    "data", ["rel:yes:1;DROP", "rel:maybe:1", "rel:yes:", "rel:yes:12345678901", "rel:yes:-1"]
)
async def test_tampered_release_data_changes_nothing(tmp_path: Path, data: str) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        writeup_id = bot.db.add_writeup(plain_draft(), NOW, LATER, "Hack.lu")
        await bot.chat().press(data)
        assert is_held(bot.db.get_writeup(writeup_id), NOW)
        assert bot.telegram.sent("answerCallbackQuery")[-1].params["text"] == (
            formatting.BUTTON_EXPIRED
        )


async def test_stranger_and_group_cannot_release(tmp_path: Path) -> None:
    clock = FrozenClock(NOW)
    async with running_bot(tmp_path, clock=clock) as bot:
        writeup_id = bot.db.add_writeup(plain_draft(), NOW, LATER, "Hack.lu")
        calls_before = len(bot.telegram.calls)
        for chat in (bot.chat(STRANGER), bot.chat(OWNER, "group")):
            await chat.send(f"/release {writeup_id}")
            await chat.press(f"rel:yes:{writeup_id}")
            await chat.press("log:live:yes")
            await chat.press("log:event:0")
        assert len(bot.telegram.calls) == calls_before
        assert is_held(bot.db.get_writeup(writeup_id), NOW)
