import argparse
import sys

from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import InvalidToken
from telegram.ext import Application, ApplicationBuilder, Defaults

from ctfbot.config import Config, ConfigError, load_config, load_token, read_environment
from ctfbot.ctftime import CtftimeClient, make_http_client
from ctfbot.digest import schedule_weekly_digest
from ctfbot.handlers import BotHandlers, register_handlers
from ctfbot.logging_setup import configure_logging
from ctfbot.manual_events import ManualEventsError, load_manual_events
from ctfbot.models import Event
from ctfbot.sources import EventService
from ctfbot.whoami import run_whoami


def build_application(config: Config, manual_events: list[Event]) -> Application:
    # No link previews: Telegram must never fetch an untrusted URL for us.
    defaults = Defaults(
        parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
    )
    http = make_http_client()

    async def close_http(_: Application) -> None:
        await http.aclose()

    app = (
        ApplicationBuilder()
        .token(config.token)
        .defaults(defaults)
        .post_shutdown(close_http)
        .build()
    )
    service = EventService(CtftimeClient(http), manual_events, config.lookahead_days)
    handlers = BotHandlers(config, service)
    register_handlers(app, handlers, config.owner_id)
    schedule_weekly_digest(app.job_queue, config, handlers)
    return app


def run_bot() -> int:
    config = load_config(read_environment())
    configure_logging(secret=config.token)
    app = build_application(config, load_manual_events(config.events_file))
    app.run_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)
    return 0


def run_whoami_mode() -> int:
    token = load_token(read_environment())
    configure_logging(secret=token)
    return run_whoami(token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ctfbot", description="CTF alerts bot for Telegram."
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
