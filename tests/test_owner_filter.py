import logging
from types import SimpleNamespace

import pytest
from telegram import Chat, Update
from telegram.ext import ApplicationHandlerStop, CommandHandler

from ctfbot.__main__ import build_application
from ctfbot.config import load_config
from ctfbot.handlers import owner_only, reject_unauthorized
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


def test_every_registered_command_is_owner_locked() -> None:
    app = build_application(load_config(env(OWNER_ID=str(OWNER))), [])
    commands = [h for h in app.handlers[0] if isinstance(h, CommandHandler)]
    assert commands, "no command handlers registered"
    for handler in commands:
        assert handler.filters.check_update(make_update(OWNER))
        assert not handler.filters.check_update(make_update(STRANGER))
        assert not handler.filters.check_update(make_update(OWNER, Chat.GROUP))
    assert min(app.handlers) == -1
