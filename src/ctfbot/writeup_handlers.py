# /log (a guided conversation), /recent, /show, /delete, /release, /export and /stats.
import contextlib
import html
import logging
import re
import sqlite3
import warnings
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
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
from ctfbot.export import (
    Keep,
    archive_name,
    build_archive,
    created_since,
    everything,
    in_category,
    new_since,
    plan_export,
)
from ctfbot.handlers import OwnerCallbackQueryHandler, owner_only, send_chunks
from ctfbot.models import Event
from ctfbot.progress import RECENT_WINDOW, Report, build_report
from ctfbot.sources import EventService
from ctfbot.storage import Database, from_db_time, to_db_time
from ctfbot.writeups import (
    InvalidInput,
    WriteupDraft,
    clean_name,
    clean_notes,
    is_held,
    parse_hold_end,
    redact_flags,
)

logger = logging.getLogger(__name__)

PLATFORM, LIVE, LIVE_EVENT, HOLD_END, CHALLENGE, CATEGORY, RESULT, APPROACH, STUCK = range(9)
LOG_TIMEOUT = timedelta(minutes=30)
RECENT_LIMIT = 10
MAX_EVENT_BUTTONS = 8
# Telegram shows long button text cut off anyway; this keeps the end predictable.
MAX_BUTTON_TITLE = 40
DRAFT_KEY = "writeup_draft"
LAST_EXPORT_KEY = "last_export"
EXPORT_RECENT_WINDOW = timedelta(days=30)

# Callback data comes back from the Telegram client, and a modified client can send anything,
# so every pattern is anchored and every value is checked again in the handler.
# \Z anchors the end too: PTB uses re.match, which only anchors the start.
LIVE_DATA = re.compile(r"log:live:(yes|no)\Z")
EVENT_DATA = re.compile(r"log:event:([0-9]{1,2}|none)\Z")
CATEGORY_DATA = re.compile(r"log:cat:([0-9]{1,2})\Z")
RESULT_DATA = re.compile(r"log:(solved|unsolved)\Z")
DELETE_DATA = re.compile(r"del:(yes|no):([0-9]{1,10})\Z")
RELEASE_DATA = re.compile(r"rel:(yes|no):([0-9]{1,10})\Z")
EXPORT_DATA = re.compile(r"exp:(all|since|recent|category)\Z")
EXPORT_CATEGORY_DATA = re.compile(r"exp:cat:([0-9]{1,2})\Z")
_ID_ARG = re.compile(r"#?([0-9]{1,10})")

Callback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[object]]


@dataclass
class Draft:
    # Kept in context.user_data, which lives in memory only: no persistence file, because
    # loading a tampered pickle executes code. A restart loses an unfinished /log.
    platform: str = ""
    # The events behind the buttons on screen. The buttons only carry an index into this,
    # so a modified client can pick from what was offered but can't supply a title or time.
    running: tuple[Event, ...] = ()
    hold_until: datetime | None = None
    hold_event: str | None = None
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


def category_keyboard(categories: Categories, prefix: str = "log:cat") -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(name, callback_data=f"{prefix}:{index}")
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


LIVE_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(formatting.LIVE_YES_LABEL, callback_data="log:live:yes"),
            InlineKeyboardButton(formatting.LIVE_NO_LABEL, callback_data="log:live:no"),
        ]
    ]
)


def event_keyboard(events: Sequence[Event]) -> InlineKeyboardMarkup:
    # Plain text, not HTML: Telegram doesn't parse button labels, so titles aren't escaped.
    rows = [
        [InlineKeyboardButton(event.title[:MAX_BUTTON_TITLE], callback_data=f"log:event:{index}")]
        for index, event in enumerate(events)
    ]
    rows.append([InlineKeyboardButton(formatting.NOT_LISTED_LABEL, callback_data="log:event:none")])
    return InlineKeyboardMarkup(rows)


EXPORT_KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton(formatting.EXPORT_ALL_LABEL, callback_data="exp:all"),
            InlineKeyboardButton(formatting.EXPORT_SINCE_LABEL, callback_data="exp:since"),
        ],
        [
            InlineKeyboardButton(formatting.EXPORT_RECENT_LABEL, callback_data="exp:recent"),
            InlineKeyboardButton(formatting.EXPORT_CATEGORY_LABEL, callback_data="exp:category"),
        ],
    ]
)


def release_keyboard(writeup_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔓 Yes, release", callback_data=f"rel:yes:{writeup_id}"),
                InlineKeyboardButton("Keep private", callback_data=f"rel:no:{writeup_id}"),
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
        service: EventService,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config
        self._db = db
        self._categories = categories
        self._service = service
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
        await update.effective_message.reply_text(
            formatting.LOG_ASK_LIVE, reply_markup=LIVE_KEYBOARD
        )
        return LIVE

    async def got_live(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        # Answered first: asking CTFtime can take seconds and this stops the client's spinner.
        await query.answer()
        match = LIVE_DATA.fullmatch(query.data or "")
        if not match:
            return LIVE
        draft = _draft(context)
        live = match[1] == "yes"
        await query.edit_message_text(formatting.log_live_answer(live))
        if not live:
            draft.hold_until, draft.hold_event = None, None
            await query.message.reply_text(formatting.LOG_ASK_CHALLENGE)
            return CHALLENGE
        running = await self._service.running(self._clock())
        draft.running = running.events[:MAX_EVENT_BUTTONS]
        await query.message.reply_text(
            formatting.log_ask_event(running), reply_markup=event_keyboard(draft.running)
        )
        return LIVE_EVENT

    async def got_event(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        query = update.callback_query
        await query.answer()
        match = EVENT_DATA.fullmatch(query.data or "")
        if not match:
            return LIVE_EVENT
        draft = _draft(context)
        if match[1] == "none":
            draft.running = ()
            await query.edit_message_text(f"Live CTF: {formatting.NOT_LISTED_LABEL}")
            await query.message.reply_text(formatting.log_ask_hold_end(self._config.timezone))
            return HOLD_END
        index = int(match[1])
        if not 0 <= index < len(draft.running):
            return LIVE_EVENT
        event = draft.running[index]
        draft.hold_until, draft.hold_event, draft.running = event.finish, event.title, ()
        await query.edit_message_text(formatting.log_event_chosen(event.title))
        await query.message.reply_text(formatting.LOG_ASK_CHALLENGE)
        return CHALLENGE

    async def got_hold_end(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        draft = _draft(context)
        try:
            end = parse_hold_end(
                update.effective_message.text or "", self._clock(), self._config.timezone
            )
        except InvalidInput as exc:
            await update.effective_message.reply_text(html.escape(str(exc), quote=False))
            return HOLD_END
        # No event title was picked, and the platform step is where the CTF was just named.
        draft.hold_until, draft.hold_event = end, draft.platform
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

    async def typed_live(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.effective_message.reply_text(formatting.LOG_USE_BUTTONS)
        return LIVE

    async def typed_event(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await update.effective_message.reply_text(formatting.LOG_USE_BUTTONS)
        return LIVE_EVENT

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
        now = self._clock()
        try:
            writeup_id = self._db.add_writeup(writeup, now, draft.hold_until, draft.hold_event)
        except sqlite3.Error:
            # Keep the draft so nothing typed is lost. Only the error type and SQLite's own
            # message are logged: neither contains the writeup text (it is bound with ?).
            logger.exception("Could not save writeup")
            await update.effective_message.reply_text(formatting.LOG_SAVE_FAILED)
            return STUCK
        context.user_data.pop(DRAFT_KEY, None)
        # A picked event can finish while the rest of /log is being answered.
        private = draft.hold_until is not None and now < draft.hold_until
        await update.effective_message.reply_text(
            formatting.render_saved(
                writeup_id,
                writeup,
                sum(draft.redactions.values()),
                draft.hold_until if private else None,
                self._config.timezone,
            )
        )
        return ConversationHandler.END

    # Reading and deleting.

    async def recent(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        writeups = self._db.recent_writeups(RECENT_LIMIT)
        text = formatting.render_recent(writeups, self._config.timezone, self._clock())
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
        text = formatting.render_writeup(writeup, self._config.timezone, self._clock())
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
            formatting.confirm_delete(writeup, self._config.timezone, self._clock()),
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

    # Ending a hold early. Holds also end on their own, silently, so nothing here is on a timer.

    async def release(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        writeup_id = parse_writeup_id(context.args)
        if writeup_id is None:
            await update.effective_message.reply_text(formatting.usage("release"))
            return
        writeup = self._db.get_writeup(writeup_id)
        if writeup is None:
            await update.effective_message.reply_text(formatting.not_found(writeup_id))
            return
        now = self._clock()
        if not is_held(writeup, now):
            await update.effective_message.reply_text(formatting.not_held(writeup_id))
            return
        await update.effective_message.reply_text(
            formatting.confirm_release(writeup, self._config.timezone, now),
            reply_markup=release_keyboard(writeup_id),
        )

    async def release_answer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        match = RELEASE_DATA.fullmatch(query.data or "")
        if not match:
            return
        # The ID came from the client, and the hold may have ended since the button was shown,
        # so the writeup is checked again rather than trusting the earlier check.
        writeup_id = int(match[2])
        writeup = self._db.get_writeup(writeup_id)
        now = self._clock()
        if writeup is None:
            await query.edit_message_text(formatting.not_found(writeup_id))
        elif not is_held(writeup, now):
            await query.edit_message_text(formatting.not_held(writeup_id))
        elif match[1] == "no":
            await query.edit_message_text(formatting.still_held(writeup_id))
        else:
            self._db.release_hold(writeup_id, now)
            await query.edit_message_text(formatting.released(writeup_id))

    # Export. Plain buttons like /delete, not a conversation: there is nothing to type, so
    # there is no state to track and nothing to clash with a /log in progress.

    async def export(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(
            formatting.EXPORT_ASK, reply_markup=EXPORT_KEYBOARD
        )

    async def export_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        match = EXPORT_DATA.fullmatch(query.data or "")
        if not match:
            return
        choice = match[1]
        if choice == "category":
            await query.edit_message_text(
                formatting.EXPORT_ASK_CATEGORY,
                reply_markup=category_keyboard(self._categories, "exp:cat"),
            )
            return
        now = self._clock()
        if choice == "all":
            await self._run_export(query, context, everything, now, moves_marker=True)
        elif choice == "since":
            await self._run_export(query, context, new_since(self._last_export()), now, True)
        else:
            keep = created_since(now - EXPORT_RECENT_WINDOW)
            await self._run_export(query, context, keep, now, moves_marker=False)

    async def export_category(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        match = EXPORT_CATEGORY_DATA.fullmatch(query.data or "")
        index = int(match[1]) if match else -1
        if not 0 <= index < len(self._categories.order):
            return
        keep = in_category(self._categories.order[index])
        await self._run_export(query, context, keep, self._clock(), moves_marker=False)

    def _last_export(self) -> datetime | None:
        stored = self._db.get_state(LAST_EXPORT_KEY)
        return from_db_time(stored) if stored else None

    async def _run_export(
        self,
        query: CallbackQuery,
        context: ContextTypes.DEFAULT_TYPE,
        keep: Keep,
        now: datetime,
        moves_marker: bool,
    ) -> None:
        # moves_marker: only exports that cover everything new may move "since last export".
        # After a one-category export the other categories' new writeups would be skipped.
        tz = self._config.timezone
        owner = self._config.owner_id
        # Removing the buttons first means a double tap can't send the file twice.
        await query.edit_message_text(formatting.EXPORT_WORKING)
        plan = plan_export(self._db.all_writeups(), keep, now)
        if not plan.ready:
            text = formatting.render_nothing_to_export(plan.held_back, plan.next_release, tz)
            await context.bot.send_message(owner, text)
            return
        archive = build_archive(plan.ready, tz)
        try:
            # To the configured owner by ID, never to whichever chat the update claims to be from.
            await context.bot.send_document(
                owner, document=archive.data, filename=archive_name(now, tz)
            )
        except TelegramError as exc:
            # Only the error type: the marker stays put, so a retry exports the same writeups.
            logger.warning("Could not send the export (%s)", type(exc).__name__)
            await context.bot.send_message(owner, formatting.EXPORT_FAILED)
            return
        if moves_marker:
            # `now` from before the file was built, so a writeup logged meanwhile counts as new.
            self._db.set_state(LAST_EXPORT_KEY, to_db_time(now))
        await context.bot.send_message(
            owner,
            formatting.render_export_done(
                len(plan.ready), archive.redactions, plan.held_back, plan.next_release, tz
            ),
        )

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
                    LIVE: [
                        button(self.got_live, LIVE_DATA),
                        MessageHandler(text, self.typed_live),
                    ],
                    LIVE_EVENT: [
                        button(self.got_event, EVENT_DATA),
                        MessageHandler(text, self.typed_event),
                    ],
                    HOLD_END: [MessageHandler(text, self.got_hold_end)],
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
            CommandHandler("release", self.release, filters=owner),
            CommandHandler("export", self.export, filters=owner),
            CommandHandler("stats", self.stats, filters=owner),
            button(self.delete_answer, DELETE_DATA),
            button(self.release_answer, RELEASE_DATA),
            button(self.export_choice, EXPORT_DATA),
            button(self.export_category, EXPORT_CATEGORY_DATA),
            # Must stay last: catches any other button.
            OwnerCallbackQueryHandler(self.expired_button, owner_id, pattern=None),
        ]
