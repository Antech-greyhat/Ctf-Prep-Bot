# /log (a guided conversation), /recent, /show, /delete and /stats.
import contextlib
import html
import logging
import re
import sqlite3
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    BaseHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from ctfbot import formatting
from ctfbot.categories import Categories
from ctfbot.config import Config
from ctfbot.handlers import OwnerCallbackQueryHandler, owner_only, send_chunks
from ctfbot.progress import RECENT_WINDOW, Report, build_report
from ctfbot.storage import Database
from ctfbot.writeups import InvalidInput, WriteupDraft, clean_name, clean_notes, redact_flags

logger = logging.getLogger(__name__)

PLATFORM, CHALLENGE, CATEGORY, RESULT, APPROACH, STUCK = range(6)
LOG_TIMEOUT = timedelta(minutes=30)
RECENT_LIMIT = 10
DRAFT_KEY = "writeup_draft"

# Callback data comes back from the Telegram client, and a modified client can send anything,
# so every pattern is anchored and every value is checked again in the handler.
# \Z anchors the end too: PTB uses re.match, which only anchors the start.
CATEGORY_DATA = re.compile(r"log:cat:([0-9]{1,2})\Z")
RESULT_DATA = re.compile(r"log:(solved|unsolved)\Z")
DELETE_DATA = re.compile(r"del:(yes|no):([0-9]{1,10})\Z")
_ID_ARG = re.compile(r"#?([0-9]{1,10})")

Callback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[object]]


@dataclass
class Draft:
    # Kept in context.user_data, which lives in memory only: no persistence file, because
    # loading a tampered pickle executes code. A restart loses an unfinished /log.
    platform: str = ""
    challenge: str = ""
    category: str = ""
    solved: bool = False
    approach: str = ""
    stuck: str = ""
    # Per field, so answering a step again (e.g. after a save error) doesn't count twice.
    redactions: dict[str, int] = field(default_factory=dict)

    def finished(self) -> WriteupDraft:
        return WriteupDraft(
            platform=self.platform,
            challenge=self.challenge,
            category=self.category,
            solved=self.solved,
            approach=self.approach,
            stuck=self.stuck,
        )


def parse_writeup_id(args: Sequence[str] | None) -> int | None:
    if not args or len(args) != 1:
        return None
    match = _ID_ARG.fullmatch(args[0])
    return int(match[1]) if match and int(match[1]) > 0 else None


def category_keyboard(categories: Categories) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(name, callback_data=f"log:cat:{index}")
        for index, name in enumerate(categories.order)
    ]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


RESULT_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(formatting.SOLVED_LABEL, callback_data="log:solved"),
            InlineKeyboardButton(formatting.UNSOLVED_LABEL, callback_data="log:unsolved"),
        ]
    ]
)


def delete_keyboard(writeup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Yes, delete", callback_data=f"del:yes:{writeup_id}"),
                InlineKeyboardButton("Keep it", callback_data=f"del:no:{writeup_id}"),
            ]
        ]
    )


def _draft(context: ContextTypes.DEFAULT_TYPE) -> Draft:
    return context.user_data[DRAFT_KEY]


class WriteupHandlers:
    def __init__(
        self,
        config: Config,
        db: Database,
        categories: Categories,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._db = db
        self._categories = categories
        self._clock = clock

    # The /log conversation. Each step returns the next state; returning the same state
    # asks again after an invalid answer. Nothing touches the database until the last step.

    async def log_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        # Also runs on /log mid-conversation (allow_reentry), replacing the old draft.
        context.user_data[DRAFT_KEY] = Draft()
        await update.effective_message.reply_text(formatting.LOG_ASK_PLATFORM)
        return PLATFORM

    async def got_platform(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        value = await self._read_name(update, "platform")
        if value is None:
            return PLATFORM
        _draft(context).platform = value
        await update.effective_message.reply_text(formatting.LOG_ASK_CHALLENGE)
        return CHALLENGE

    async def got_challenge(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        value = await self._read_name(update, "challenge name")
        if value is None:
            return CHALLENGE
        _draft(context).challenge = value
        await update.effective_message.reply_text(
            formatting.LOG_ASK_CATEGORY, reply_markup=category_keyboard(self._categories)
        )
        return CATEGORY

    async def got_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        match = CATEGORY_DATA.fullmatch(query.data or "")
        index = int(match[1]) if match else -1
        if not 0 <= index < len(self._categories.order):
            return CATEGORY
        name = self._categories.order[index]
        _draft(context).category = name
        await query.edit_message_text(f"Category: <b>{html.escape(name)}</b>")
        await query.message.reply_text(formatting.LOG_ASK_RESULT, reply_markup=RESULT_KEYBOARD)
        return RESULT

    async def got_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        match = RESULT_DATA.fullmatch(query.data or "")
        if not match:
            return RESULT
        solved = match[1] == "solved"
        _draft(context).solved = solved
        label = formatting.SOLVED_LABEL if solved else formatting.UNSOLVED_LABEL
        await query.edit_message_text(f"Result: {label}")
        await query.message.reply_text(formatting.log_ask_approach(solved))
        return APPROACH

    async def typed_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.effective_message.reply_text(formatting.LOG_USE_BUTTONS)
        return CATEGORY

    async def typed_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.effective_message.reply_text(formatting.LOG_USE_BUTTONS)
        return RESULT

    async def got_approach(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        draft = _draft(context)
        value = await self._read_notes(update, draft, "approach")
        if value is None:
            return APPROACH
        draft.approach = value
        await update.effective_message.reply_text(formatting.log_ask_stuck(draft.solved))
        return STUCK

    async def got_stuck(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        draft = _draft(context)
        value = await self._read_notes(update, draft, "stuck")
        if value is None:
            return STUCK
        draft.stuck = value
        return await self._save(update, context)

    async def skip_stuck(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        draft = _draft(context)
        if not draft.solved:
            # For an unsolved challenge, where you got stuck is the whole point of the entry.
            await update.effective_message.reply_text(formatting.LOG_STUCK_REQUIRED)
            return STUCK
        draft.stuck = ""
        draft.redactions.pop("stuck", None)
        return await self._save(update, context)

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.pop(DRAFT_KEY, None)
        await update.effective_message.reply_text(formatting.LOG_CANCELLED)
        return ConversationHandler.END

    async def timed_out(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data.pop(DRAFT_KEY, None)
        await context.bot.send_message(self._config.owner_id, formatting.LOG_TIMED_OUT)

    async def nothing_to_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(formatting.NOTHING_TO_CANCEL)

    async def nothing_to_skip(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Reached when /skip arrives outside the STUCK step: the conversation finds no handler
        # for it, so the update falls through to here and the current question still stands.
        await update.effective_message.reply_text(formatting.NOTHING_TO_SKIP)

    async def _read_name(self, update: Update, label: str) -> str | None:
        try:
            return clean_name(update.effective_message.text or "", label)
        except InvalidInput as exc:
            await update.effective_message.reply_text(html.escape(str(exc), quote=False))
            return None

    async def _read_notes(self, update: Update, draft: Draft, field_name: str) -> str | None:
        try:
            value = clean_notes(update.effective_message.text or "", "description")
        except InvalidInput as exc:
            await update.effective_message.reply_text(html.escape(str(exc), quote=False))
            return None
        redacted, removed = redact_flags(value)
        draft.redactions[field_name] = removed
        return redacted

    async def _save(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        draft = _draft(context)
        writeup = draft.finished()
        try:
            writeup_id = self._db.add_writeup(writeup, self._clock())
        except sqlite3.Error:
            # Keep the draft so nothing typed is lost. Only the error type and SQLite's own
            # message are logged: neither contains the writeup text (it is bound with ?).
            logger.exception("Could not save writeup")
            await update.effective_message.reply_text(formatting.LOG_SAVE_FAILED)
            return STUCK
        context.user_data.pop(DRAFT_KEY, None)
        await update.effective_message.reply_text(
            formatting.render_saved(writeup_id, writeup, sum(draft.redactions.values()))
        )
        return ConversationHandler.END

    # Reading and deleting.

    async def recent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        writeups = self._db.recent_writeups(RECENT_LIMIT)
        text = formatting.render_recent(writeups, self._config.timezone)
        await send_chunks(context.bot, update.effective_chat.id, text)

    async def show(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        writeup_id = parse_writeup_id(context.args)
        if writeup_id is None:
            await update.effective_message.reply_text(formatting.usage("show"))
            return
        writeup = self._db.get_writeup(writeup_id)
        if writeup is None:
            await update.effective_message.reply_text(formatting.not_found(writeup_id))
            return
        text = formatting.render_writeup(writeup, self._config.timezone)
        await send_chunks(context.bot, update.effective_chat.id, text)

    async def delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        writeup_id = parse_writeup_id(context.args)
        if writeup_id is None:
            await update.effective_message.reply_text(formatting.usage("delete"))
            return
        writeup = self._db.get_writeup(writeup_id)
        if writeup is None:
            await update.effective_message.reply_text(formatting.not_found(writeup_id))
            return
        await update.effective_message.reply_text(
            formatting.confirm_delete(writeup, self._config.timezone),
            reply_markup=delete_keyboard(writeup_id),
        )

    async def delete_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        match = DELETE_DATA.fullmatch(query.data or "")
        if not match:
            return
        # The ID came from the client: delete_writeup simply finds nothing for a made-up one.
        writeup_id = int(match[2])
        if match[1] == "no":
            await query.edit_message_text(formatting.kept(writeup_id))
        elif self._db.delete_writeup(writeup_id):
            await query.edit_message_text(formatting.deleted(writeup_id))
        else:
            await query.edit_message_text(formatting.not_found(writeup_id))

    # Progress.

    def report(self, now: datetime) -> Report:
        return build_report(
            self._categories,
            solved=self._db.solved_counts(),
            attempts=self._db.attempt_counts(),
            recent=self._db.categories_since(now - RECENT_WINDOW),
            last_log=self._db.last_writeup_at(),
        )

    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        now = self._clock()
        text = formatting.render_report(self.report(now), now)
        await send_chunks(context.bot, update.effective_chat.id, text)

    async def expired_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Buttons from an old or cancelled /log, or data no pattern accepts. Answering stops
        # the client's loading spinner.
        query = update.callback_query
        await query.answer(formatting.BUTTON_EXPIRED)
        # The message may be too old to edit or already button-free; nothing to clean up then.
        with contextlib.suppress(BadRequest):
            await query.edit_message_reply_markup(reply_markup=None)

    def handlers(self, owner_id: int) -> list[BaseHandler]:
        owner = owner_only(owner_id)
        text = owner & filters.TEXT & ~filters.COMMAND

        def button(callback: Callback, pattern: re.Pattern[str]) -> OwnerCallbackQueryHandler:
            return OwnerCallbackQueryHandler(callback, owner_id, pattern)

        with warnings.catch_warnings():
            # PTB warns that with per_message=False, buttons are tracked per chat rather than
            # per message. That is what we want: one /log at a time per chat, a button press
            # is only accepted in the step that expects it, and every other press reaches
            # expired_button. Only this one warning is silenced.
            warnings.filterwarnings(
                "ignore",
                message=r"If 'per_message=False', 'CallbackQueryHandler' will not be tracked",
                category=PTBUserWarning,
            )
            conversation = ConversationHandler(
                entry_points=[CommandHandler("log", self.log_start, filters=owner)],
                states={
                    PLATFORM: [MessageHandler(text, self.got_platform)],
                    CHALLENGE: [MessageHandler(text, self.got_challenge)],
                    CATEGORY: [
                        button(self.got_category, CATEGORY_DATA),
                        MessageHandler(text, self.typed_category),
                    ],
                    RESULT: [
                        button(self.got_result, RESULT_DATA),
                        MessageHandler(text, self.typed_result),
                    ],
                    APPROACH: [MessageHandler(text, self.got_approach)],
                    STUCK: [
                        MessageHandler(text, self.got_stuck),
                        CommandHandler("skip", self.skip_stuck, filters=owner),
                    ],
                    # Runs only with the owner's own last update, when their /log goes idle.
                    ConversationHandler.TIMEOUT: [TypeHandler(Update, self.timed_out)],
                },
                fallbacks=[CommandHandler("cancel", self.cancel, filters=owner)],
                # Sending /log again mid-way starts a fresh entry.
                allow_reentry=True,
                conversation_timeout=LOG_TIMEOUT,
                name="log",
            )
        return [
            conversation,
            CommandHandler("cancel", self.nothing_to_cancel, filters=owner),
            CommandHandler("skip", self.nothing_to_skip, filters=owner),
            CommandHandler("recent", self.recent, filters=owner),
            CommandHandler("show", self.show, filters=owner),
            CommandHandler("delete", self.delete, filters=owner),
            CommandHandler("stats", self.stats, filters=owner),
            button(self.delete_answer, DELETE_DATA),
            # Must stay last: catches any other button.
            OwnerCallbackQueryHandler(self.expired_button, owner_id, pattern=None),
        ]
