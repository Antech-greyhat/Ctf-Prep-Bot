from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationBuilder, Job

from ctfbot.categories import Categories
from ctfbot.config import Config, load_config
from ctfbot.formatting import help_text
from ctfbot.models import Event
from ctfbot.reminders import (
    NUDGED_FOR_KEY,
    TRACKING_SINCE_KEY,
    ReminderJobs,
    events_to_remind,
    nudge_due,
    reminder_key,
    schedule_reminders,
)
from ctfbot.sources import EventService
from ctfbot.storage import Database, open_database, to_db_time
from ctfbot.writeup_handlers import WriteupHandlers
from ctfbot.writeups import WriteupDraft
from tests.helpers import FAKE_TOKEN, env, make_event
from tests.test_sources import FakeCtftime

OWNER = 424242
START = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)


# Pure rules ---------------------------------------------------------------------------------


def test_nudge_off_when_disabled() -> None:
    assert nudge_due(START + 30 * DAY, None, START, None, inactivity_days=0) is None


def test_nudge_waits_for_the_threshold() -> None:
    assert nudge_due(START + 3 * DAY - HOUR, None, START, None, 3) is None
    assert nudge_due(START + 3 * DAY, None, START, None, 3) == START


def test_one_nudge_per_quiet_period() -> None:
    last_log = START + DAY
    nudged = to_db_time(last_log)
    assert nudge_due(START + 10 * DAY, last_log, START, nudged, 3) is None
    # Logging again starts a new quiet period.
    newer = START + 5 * DAY
    assert nudge_due(START + 10 * DAY, newer, START, nudged, 3) == newer


def test_logs_from_before_tracking_started_do_not_trigger_instantly() -> None:
    tracking = START + 20 * DAY
    assert nudge_due(tracking + HOUR, START, tracking, None, 3) is None


def test_reminder_key_matches_dedupe_identity() -> None:
    a = make_event("Kenya  CTF", START)
    b = make_event("kenya ctf", START, source="manual")
    assert reminder_key(a) == reminder_key(b)
    assert reminder_key(a) != reminder_key(make_event("Kenya CTF", START + DAY))


def test_events_to_remind_window_and_memory() -> None:
    now = START
    started = make_event("Started", now - HOUR)
    soon = make_event("Soon", now + 5 * HOUR)
    edge = make_event("Edge", now + 24 * HOUR)
    later = make_event("Later", now + 25 * HOUR)
    sent = {reminder_key(soon)}
    result = events_to_remind((started, soon, edge, later), now, 24, sent.__contains__)
    assert [e.title for e in result] == ["Edge"]


# Jobs against a real database ---------------------------------------------------------------


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class BrokenBot:
    async def send_message(self, chat_id: int, text: str) -> None:
        raise ConnectionError("offline")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "bot.db")
    yield database
    database.close()


def make_jobs(
    db: Database,
    clock: Clock,
    ctftime: list[Event] | None = None,
    manual: list[Event] | None = None,
    **settings: str,
) -> ReminderJobs:
    config = load_config(env(OWNER_ID=str(OWNER), **settings))
    service = EventService(FakeCtftime(ctftime or []), manual or [], config.lookahead_days)
    writeups = WriteupHandlers(config, db, Categories(), clock=clock)
    return ReminderJobs(config, db, service, writeups, clock=clock)


def log_writeup(db: Database, when: datetime) -> None:
    draft = WriteupDraft("picoCTF", "Cookies", "General", True, "approach", "")
    db.add_writeup(draft, when)


async def test_nudge_job_sends_once_per_quiet_period(db: Database) -> None:
    clock = Clock(START)
    jobs, bot = make_jobs(db, clock), FakeBot()
    context = SimpleNamespace(bot=bot)
    assert jobs.tracking_since() == START

    clock.now = START + 2 * DAY
    await jobs.nudge_job(context)
    assert bot.sent == []

    clock.now = START + 3 * DAY
    await jobs.nudge_job(context)
    clock.now = START + 4 * DAY
    await jobs.nudge_job(context)
    [(chat_id, text)] = bot.sent
    assert chat_id == OWNER
    assert "no writeups yet" in text
    assert "Your focus is <b>General</b>." in text

    log_writeup(db, START + 4 * DAY)
    clock.now = START + 7 * DAY
    await jobs.nudge_job(context)
    assert len(bot.sent) == 2
    assert "No writeup logged in 3 days." in bot.sent[-1][1]


async def test_nudge_is_not_repeated_after_restart(db: Database) -> None:
    clock = Clock(START)
    bot = FakeBot()
    make_jobs(db, clock).tracking_since()
    clock.now = START + 5 * DAY
    await make_jobs(db, clock).nudge_job(SimpleNamespace(bot=bot))
    # A restart builds everything again from the same database file.
    await make_jobs(db, clock).nudge_job(SimpleNamespace(bot=bot))
    assert len(bot.sent) == 1
    assert db.get_state(NUDGED_FOR_KEY) == to_db_time(START)


async def test_failed_nudge_is_retried(db: Database) -> None:
    clock = Clock(START)
    jobs = make_jobs(db, clock)
    jobs.tracking_since()
    clock.now = START + 5 * DAY
    with pytest.raises(ConnectionError):
        await jobs.nudge_job(SimpleNamespace(bot=BrokenBot()))
    assert db.get_state(NUDGED_FOR_KEY) is None
    bot = FakeBot()
    await jobs.nudge_job(SimpleNamespace(bot=bot))
    assert len(bot.sent) == 1


async def test_reminders_sent_once_even_across_restarts(db: Database) -> None:
    clock = Clock(START)
    soon = make_event("Soon CTF", START + 5 * HOUR)
    manual = make_event("Local CTF", START + 20 * HOUR, source="manual")
    far = make_event("Far CTF", START + 3 * DAY)
    bot = FakeBot()
    await make_jobs(db, clock, [soon, far], [manual]).reminder_job(SimpleNamespace(bot=bot))
    assert [chat_id for chat_id, _ in bot.sent] == [OWNER, OWNER]
    assert "Soon CTF" in bot.sent[0][1] and "Starts in about 5h" in bot.sent[0][1]
    assert "Local CTF" in bot.sent[1][1]

    clock.now = START + HOUR
    await make_jobs(db, clock, [soon, far], [manual]).reminder_job(SimpleNamespace(bot=bot))
    assert len(bot.sent) == 2


async def test_failed_reminder_is_not_marked_sent(db: Database) -> None:
    soon = make_event("Soon CTF", START + 5 * HOUR)
    jobs = make_jobs(db, Clock(START), [soon])
    with pytest.raises(ConnectionError):
        await jobs.reminder_job(SimpleNamespace(bot=BrokenBot()))
    assert not db.reminder_sent(reminder_key(soon))


async def test_old_reminder_records_are_pruned(db: Database) -> None:
    db.mark_reminder_sent("ancient", START - 31 * DAY)
    db.mark_reminder_sent("recent", START - 29 * DAY)
    await make_jobs(db, Clock(START)).reminder_job(SimpleNamespace(bot=FakeBot()))
    assert not db.reminder_sent("ancient")
    assert db.reminder_sent("recent")


# Scheduling ---------------------------------------------------------------------------------


def schedule(db: Database, **settings: str) -> tuple[list[Job], Config]:
    config = load_config(env(OWNER_ID=str(OWNER), **settings))
    jobs = make_jobs(db, Clock(START), **settings)
    app = ApplicationBuilder().token(FAKE_TOKEN).build()
    return schedule_reminders(app.job_queue, config, jobs), config


def test_zero_disables_both_jobs(db: Database) -> None:
    scheduled, _ = schedule(db, INACTIVITY_DAYS="0", REMINDER_HOURS="0")
    assert scheduled == []
    assert db.get_state(TRACKING_SINCE_KEY) is None


def test_fresh_install_starts_tracking_at_first_run(db: Database) -> None:
    schedule(db)
    assert db.get_state(TRACKING_SINCE_KEY) == to_db_time(START)


def test_nudge_runs_daily_at_local_time(db: Database) -> None:
    scheduled, config = schedule(db, NUDGE_TIME="19:45", TIMEZONE="Africa/Nairobi")
    nudge = next(job for job in scheduled if job.name == "inactivity-nudge")
    next_run = nudge.job.trigger.get_next_fire_time(None, START)
    local = next_run.astimezone(config.timezone)
    assert (local.hour, local.minute) == (19, 45)
    assert {job.name for job in scheduled} == {"inactivity-nudge", "ctf-reminders"}


def test_help_mentions_reminders_and_nudge() -> None:
    text = help_text(load_config(env(REMINDER_HOURS="12", INACTIVITY_DAYS="4")))
    assert "12h before each one starts" in text
    assert "after 4 days" in text
    quiet = help_text(load_config(env(REMINDER_HOURS="0", INACTIVITY_DAYS="0")))
    assert "Nudge" not in quiet and "reminders" not in quiet
