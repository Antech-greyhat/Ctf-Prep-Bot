# Inactivity nudge and "CTF starts soon" reminders.
#
# Both remember what they already sent in the database, so a restart never repeats a message.
# The decisions are pure functions that take `now` as an argument; the job methods only fetch
# data, send and record.
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

from telegram.ext import ContextTypes, Job, JobQueue

from ctfbot import formatting
from ctfbot.config import Config
from ctfbot.models import Event
from ctfbot.sources import EventService, dedupe_key
from ctfbot.storage import Database, from_db_time, to_db_time
from ctfbot.writeup_handlers import WriteupHandlers

logger = logging.getLogger(__name__)

TRACKING_SINCE_KEY = "tracking_since"
NUDGED_FOR_KEY = "nudged_for"
REMINDER_CHECK_EVERY = timedelta(hours=1)
FIRST_REMINDER_CHECK = timedelta(minutes=1)
# Long after an event started its key is useless; keep the table small.
REMINDER_RETENTION = timedelta(days=30)


def nudge_due(
    now: datetime,
    last_log: datetime | None,
    tracking_since: datetime,
    nudged_for: str | None,
    inactivity_days: int,
) -> datetime | None:
    # Returns the start of the quiet period to nudge about, or None.
    # One nudge per quiet period: the period starts at your last log (or when the bot first
    # ran, so a fresh install doesn't count from 1970), and logging again starts a new one.
    if inactivity_days == 0:
        return None
    quiet_since = max(last_log or tracking_since, tracking_since)
    if now - quiet_since < timedelta(days=inactivity_days):
        return None
    if nudged_for == to_db_time(quiet_since):
        return None
    return quiet_since


def reminder_key(event: Event) -> str:
    # Same identity as de-duplication: normalised title plus start time. If an organiser
    # moves the start time, that is a new key and you are reminded again.
    title, start = dedupe_key(event)
    return f"{to_db_time(start)}|{title}"


def events_to_remind(
    events: Iterable[Event], now: datetime, hours: int, already_sent: Callable[[str], bool]
) -> list[Event]:
    until = now + timedelta(hours=hours)
    return [
        event
        for event in events
        if now < event.start <= until and not already_sent(reminder_key(event))
    ]


class ReminderJobs:
    def __init__(
        self,
        config: Config,
        db: Database,
        service: EventService,
        writeups: WriteupHandlers,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._db = db
        self._service = service
        self._writeups = writeups
        self._clock = clock

    def tracking_since(self) -> datetime:
        stored = self._db.get_state(TRACKING_SINCE_KEY)
        if stored is not None:
            return from_db_time(stored)
        now = self._clock()
        self._db.set_state(TRACKING_SINCE_KEY, to_db_time(now))
        return now

    async def nudge_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = self._clock()
        last_log = self._db.last_writeup_at()
        quiet_since = nudge_due(
            now,
            last_log,
            self.tracking_since(),
            self._db.get_state(NUDGED_FOR_KEY),
            self._config.inactivity_days,
        )
        if quiet_since is None:
            return
        focus = self._writeups.report(now).focus
        text = formatting.render_nudge((now - quiet_since).days, focus, last_log is not None)
        await context.bot.send_message(self._config.owner_id, text)
        # Recorded only after a successful send, so a failed send is retried next time.
        self._db.set_state(NUDGED_FOR_KEY, to_db_time(quiet_since))

    async def reminder_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = self._clock()
        hours = self._config.reminder_hours
        upcoming = await self._service.upcoming(now, days=hours / 24)
        for event in events_to_remind(upcoming.events, now, hours, self._db.reminder_sent):
            text = formatting.render_reminder(event, self._config.timezone, now)
            await context.bot.send_message(self._config.owner_id, text)
            # Recorded only after a successful send; a failure is retried next hour.
            self._db.mark_reminder_sent(reminder_key(event), now)
        self._db.prune_reminders(now - REMINDER_RETENTION)


def schedule_reminders(job_queue: JobQueue, config: Config, jobs: ReminderJobs) -> list[Job]:
    scheduled: list[Job] = []
    if config.inactivity_days:
        # Start the clock now, so the first quiet period counts from the first run.
        jobs.tracking_since()
        scheduled.append(
            job_queue.run_daily(
                jobs.nudge_job,
                time=config.nudge_time.replace(tzinfo=config.timezone),
                name="inactivity-nudge",
            )
        )
    if config.reminder_hours:
        scheduled.append(
            job_queue.run_repeating(
                jobs.reminder_job,
                interval=REMINDER_CHECK_EVERY,
                first=FIRST_REMINDER_CHECK,
                name="ctf-reminders",
            )
        )
    return scheduled
