import logging
from datetime import UTC, datetime

from telegram import Bot, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    TypeHandler,
    filters,
)

from ctfbot import formatting
from ctfbot.config import Config
from ctfbot.sources import EventService

logger = logging.getLogger(__name__)


def owner_only(owner_id: int) -> filters.BaseFilter:
    return filters.User(user_id=owner_id) & filters.ChatType.PRIVATE


def is_authorized(update: Update, owner_id: int) -> bool:
    user, chat = update.effective_user, update.effective_chat
    return (
        user is not None
        and user.id == owner_id
        and chat is not None
        and chat.type == ChatType.PRIVATE
    )


async def reject_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Second layer behind the per-handler filter. Never log the message text.
    owner_id: int = context.bot_data["owner_id"]
    if is_authorized(update, owner_id):
        return
    user = update.effective_user
    logger.warning("Ignored update from unauthorized user id=%s", user.id if user else "unknown")
    raise ApplicationHandlerStop


async def send_chunks(bot: Bot, chat_id: int, text: str) -> None:
    for chunk in formatting.split_message(text):
        await bot.send_message(chat_id, chunk)


class BotHandlers:
    def __init__(self, config: Config, service: EventService) -> None:
        self._config = config
        self._service = service

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(formatting.START_TEXT)

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(formatting.help_text(self._config))

    async def events(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._send_event_list(context.bot, update.effective_chat.id, "Upcoming CTFs")

    async def digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send_digest(context.bot)

    async def digest_job(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.send_digest(context.bot)

    async def send_digest(self, bot: Bot) -> None:
        await self._send_event_list(bot, self._config.owner_id, formatting.DIGEST_HEADING)

    async def _send_event_list(self, bot: Bot, chat_id: int, heading: str) -> None:
        upcoming = await self._service.upcoming(datetime.now(UTC))
        text = formatting.render_event_list(
            upcoming, self._config.timezone, self._config.lookahead_days, heading
        )
        await send_chunks(bot, chat_id, text)


def register_handlers(app: Application, handlers: BotHandlers, owner_id: int) -> None:
    app.bot_data["owner_id"] = owner_id
    app.add_handler(TypeHandler(Update, reject_unauthorized), group=-1)
    owner = owner_only(owner_id)
    app.add_handlers(
        [
            CommandHandler("start", handlers.start, filters=owner),
            CommandHandler("help", handlers.help, filters=owner),
            CommandHandler("events", handlers.events, filters=owner),
            CommandHandler("digest", handlers.digest, filters=owner),
        ]
    )
