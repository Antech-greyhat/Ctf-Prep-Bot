import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    BaseHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
)

from ctfbot.__main__ import build_application
from ctfbot.categories import Categories
from ctfbot.config import load_config
from ctfbot.handlers import OwnerCallbackQueryHandler, owner_only, reject_unauthorized
from ctfbot.storage import open_database
from tests.helpers import env, make_update

OWNER = 424242
STRANGER = 999


@pytest.mark.parametrize(
    ("update", "allowed"),
    [
        (make_update(OWNER), True),
        (make_update(STRANGER), False),
        (make_update(OWNER, Chat.GROUP), False),
        (make_update(OWNER, Chat.SUPERGROUP), False),
        (make_update(None, Chat.CHANNEL), False),
    ],
)
def test_owner_filter(update: Update, allowed: bool) -> None:
    assert bool(owner_only(OWNER).check_update(update)) is allowed


async def test_gatekeeper_stops_strangers_and_logs_only_their_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    context = SimpleNamespace(bot_data={"owner_id": OWNER})
    update = make_update(STRANGER, text="secret attacker text")
    with caplog.at_level(logging.WARNING), pytest.raises(ApplicationHandlerStop):
        await reject_unauthorized(update, context)
    assert str(STRANGER) in caplog.text
    assert "secret attacker text" not in caplog.text


async def test_gatekeeper_lets_owner_through() -> None:
    context = SimpleNamespace(bot_data={"owner_id": OWNER})
    await reject_unauthorized(make_update(OWNER), context)


def make_button_press(user_id: int, chat_type: str = Chat.PRIVATE) -> Update:
    chat = Chat(id=user_id, type=chat_type)
    message = Message(message_id=1, date=datetime.now(UTC), chat=chat, text="buttons")
    user = User(id=user_id, first_name="x", is_bot=False)
    query = CallbackQuery(
        id="1", from_user=user, chat_instance="x", data="del:yes:1", message=message
    )
    return Update(update_id=1, callback_query=query)


def walk(
    handlers: list[BaseHandler], in_timeout: bool = False
) -> Iterator[tuple[BaseHandler, bool]]:
    # Yields every handler, including those nested in conversations, and whether it sits in
    # a TIMEOUT state.
    for handler in handlers:
        yield handler, in_timeout
        if isinstance(handler, ConversationHandler):
            yield from walk(handler.entry_points)
            for state, state_handlers in handler.states.items():
                yield from walk(state_handlers, state == ConversationHandler.TIMEOUT)
            yield from walk(handler.fallbacks)


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    db = open_database(tmp_path / "bot.db")
    yield build_application(load_config(env(OWNER_ID=str(OWNER))), [], db, Categories())
    db.close()


def test_every_handler_is_owner_locked(app: Application) -> None:
    # A handler type that is not checked here fails this test on purpose.
    gatekeeper, *others = app.handlers[-1]
    assert isinstance(gatekeeper, TypeHandler) and not others
    assert set(app.handlers) == {-1, 0}
    handlers = list(walk(app.handlers[0]))
    assert any(isinstance(h, CommandHandler) for h, _ in handlers)
    assert any(isinstance(h, OwnerCallbackQueryHandler) for h, _ in handlers)
    for handler, in_timeout in handlers:
        if isinstance(handler, ConversationHandler):
            continue
        if isinstance(handler, CommandHandler | MessageHandler):
            message = "/x" if isinstance(handler, CommandHandler) else "hello"
            assert handler.filters.check_update(make_update(OWNER, text=message))
            assert not handler.filters.check_update(make_update(STRANGER, text=message))
            assert not handler.filters.check_update(make_update(OWNER, Chat.GROUP, text=message))
        elif isinstance(handler, OwnerCallbackQueryHandler):
            assert handler.owner_id == OWNER
            assert not handler.check_update(make_button_press(STRANGER))
            assert not handler.check_update(make_button_press(OWNER, Chat.GROUP))
        elif isinstance(handler, TypeHandler) and in_timeout:
            # PTB runs TIMEOUT handlers only with the update that last moved the conversation,
            # which already passed the owner check above.
            assert handler.type is Update
        else:
            pytest.fail(f"unchecked handler type {type(handler).__name__}")


async def test_gatekeeper_stops_stranger_button_presses() -> None:
    context = SimpleNamespace(bot_data={"owner_id": OWNER})
    with pytest.raises(ApplicationHandlerStop):
        await reject_unauthorized(make_button_press(STRANGER), context)
    with pytest.raises(ApplicationHandlerStop):
        await reject_unauthorized(make_button_press(OWNER, Chat.GROUP), context)
    await reject_unauthorized(make_button_press(OWNER), context)
