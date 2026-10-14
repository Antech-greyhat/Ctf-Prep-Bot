from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationBuilder

from ctfbot.config import Config, load_config
from ctfbot.digest import schedule_weekly_digest
from ctfbot.formatting import help_text
from ctfbot.handlers import BotHandlers
from ctfbot.sources import EventService
from tests.helpers import FAKE_TOKEN, env, make_event
from tests.test_sources import FakeCtftime

OWNER = 424242


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def make_handlers(config: Config) -> BotHandlers:
    event = make_event("Soon CTF", datetime.now(UTC) + timedelta(days=1))
    return BotHandlers(config, EventService(FakeCtftime([event]), [], config.lookahead_days))


@pytest.mark.parametrize(
    ("day", "python_weekday"),
    [("mon", 0), ("tue", 1), ("wed", 2), ("thu", 3), ("fri", 4), ("sat", 5), ("sun", 6)],
)
def test_digest_fires_on_configured_weekday_and_local_time(day: str, python_weekday: int) -> None:
    # Ask the real scheduler, so a wrong weekday mapping cannot slip through.
    config = load_config(env(DIGEST_DAY=day, DIGEST_TIME="08:30", TIMEZONE="Africa/Nairobi"))
    app = ApplicationBuilder().token(FAKE_TOKEN).build()
    job = schedule_weekly_digest(app.job_queue, config, make_handlers(config))
    next_run = job.job.trigger.get_next_fire_time(None, datetime(2026, 10, 14, tzinfo=UTC))
    local = next_run.astimezone(config.timezone)
    assert local.weekday() == python_weekday
    assert (local.hour, local.minute) == (8, 30)


WINTER = datetime(2026, 1, 5, tzinfo=UTC)
SUMMER = datetime(2026, 7, 6, tzinfo=UTC)


@pytest.mark.parametrize("now", [WINTER, SUMMER])
def test_digest_time_follows_daylight_saving(now: datetime) -> None:
    config = load_config(env(TIMEZONE="Europe/Berlin", DIGEST_TIME="08:00"))
    app = ApplicationBuilder().token(FAKE_TOKEN).build()
    job = schedule_weekly_digest(app.job_queue, config, make_handlers(config))
    next_run = job.job.trigger.get_next_fire_time(None, now)
    assert next_run.astimezone(config.timezone).hour == 8


async def test_send_digest_goes_to_owner() -> None:
    config = load_config(env(OWNER_ID=str(OWNER)))
    bot = FakeBot()
    await make_handlers(config).send_digest(bot)
    [(chat_id, text)] = bot.sent
    assert chat_id == OWNER
    assert "Weekly CTF digest" in text
    assert "Soon CTF" in text


async def test_digest_command_and_job_both_send_digest() -> None:
    config = load_config(env(OWNER_ID=str(OWNER)))
    handlers = make_handlers(config)
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=OWNER))
    await handlers.digest(update, context)
    await handlers.digest_job(context)
    assert [chat_id for chat_id, _ in bot.sent] == [OWNER, OWNER]


async def test_events_command_replies_with_list() -> None:
    config = load_config(env(OWNER_ID=str(OWNER)))
    bot = FakeBot()
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=OWNER))
    await make_handlers(config).events(update, SimpleNamespace(bot=bot))
    [(chat_id, text)] = bot.sent
    assert chat_id == OWNER
    assert "Upcoming CTFs" in text


def test_help_mentions_schedule() -> None:
    text = help_text(load_config(env(DIGEST_DAY="fri", DIGEST_TIME="18:15")))
    assert "every Friday at 18:15" in text
    assert "/digest" in text
