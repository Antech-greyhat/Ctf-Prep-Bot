import argparse
import sys

from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import InvalidToken
from telegram.ext import Application, ApplicationBuilder, Defaults
from telegram.request import BaseRequest

from ctfbot.categories import Categories, CategoriesError, load_categories
from ctfbot.config import Config, ConfigError, load_config, load_token, read_environment
from ctfbot.ctftime import CtftimeClient, make_http_client
from ctfbot.digest import schedule_weekly_digest
from ctfbot.handlers import BotHandlers, register_handlers
from ctfbot.logging_setup import configure_logging
from ctfbot.manual_events import ManualEventsError, load_manual_events
from ctfbot.models import Event
from ctfbot.reminders import ReminderJobs, schedule_reminders
from ctfbot.sources import EventService
from ctfbot.storage import Database, StorageError, open_database
from ctfbot.whoami import run_whoami
from ctfbot.writeup_handlers import WriteupHandlers


def build_application(
    config: Config,
    manual_events: list[Event],
    db: Database,
    categories: Categories,
    *,
    request: BaseRequest | None = None,
) -> Application:
    # No link previews: Telegram must never fetch an untrusted URL for us.
    defaults = Defaults(
        parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
    )
    http = make_http_client()

    async def close_resources(_: Application) -> None:
        await http.aclose()
        db.close()

    builder = ApplicationBuilder().token(config.token).defaults(defaults)
    if request is not None:
        # Tests pass an offline fake so no request ever reaches Telegram.
        builder = builder.request(request)
    app = builder.post_shutdown(close_resources).build()
    service = EventService(CtftimeClient(http), manual_events, config.lookahead_days)
    handlers = BotHandlers(config, service)
    writeups = WriteupHandlers(config, db, categories)
    register_handlers(app, handlers, config.owner_id)
    app.add_handlers(writeups.handlers(config.owner_id))
    schedule_weekly_digest(app.job_queue, config, handlers)
    schedule_reminders(app.job_queue, config, ReminderJobs(config, db, service, writeups))
    return app


def run_bot() -> int:
    config = load_config(read_environment())
    configure_logging(secret=config.token)
    manual_events = load_manual_events(config.events_file)
    categories = load_categories(config.categories_file)
    # Opened last, so a typo in a config file never leaves a fresh database behind.
    db = open_database(config.database_path)
    app = build_application(config, manual_events, db, categories)
    # Messages and button presses only; nothing else Telegram could send is requested.
    app.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY], drop_pending_updates=True
    )
    return 0


def run_whoami_mode() -> int:
    token = load_token(read_environment())
    configure_logging(secret=token)
    return run_whoami(token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ctfbot", description="CTF prep bot for Telegram."
    )
    parser.add_argument(
        "--whoami",
        action="store_true",
        help="print the user ID of the next person to message the bot, then exit",
    )
    args = parser.parse_args(argv)
    try:
        return run_whoami_mode() if args.whoami else run_bot()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
    except ManualEventsError as exc:
        print(f"Events file error: {exc}", file=sys.stderr)
    except CategoriesError as exc:
        print(f"Categories file error: {exc}", file=sys.stderr)
    except StorageError as exc:
        print(f"Database error: {exc}", file=sys.stderr)
    except InvalidToken:
        # The library's own message for this error contains the raw token.
        print(
            "Error: Telegram rejected TELEGRAM_BOT_TOKEN. Check the value in .env, or create "
            "a new one with /revoke in @BotFather.",
            file=sys.stderr,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
