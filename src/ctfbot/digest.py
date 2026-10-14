from telegram.ext import Job, JobQueue

from ctfbot.config import Config
from ctfbot.handlers import BotHandlers

# PTB v20+ run_daily uses 0 = Sunday, not Monday.
PTB_WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def schedule_weekly_digest(job_queue: JobQueue, config: Config, handlers: BotHandlers) -> Job:
    at = config.digest_time.replace(tzinfo=config.timezone)
    return job_queue.run_daily(
        handlers.digest_job,
        time=at,
        days=(PTB_WEEKDAYS[config.digest_day],),
        name="weekly-digest",
    )
