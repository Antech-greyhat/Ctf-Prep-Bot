# An offline stand-in for the Telegram Bot API, so tests can drive the real Application,
# handlers and ConversationHandler end to end without any network access.
import itertools
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import Application, ConversationHandler
from telegram.request import BaseRequest, RequestData

from ctfbot.__main__ import build_application
from ctfbot.categories import Categories
from ctfbot.config import load_config
from ctfbot.storage import Database, open_database
from tests.helpers import env

OWNER = 424242
STRANGER = 999
BOT_ID = 777


@dataclass
class Call:
    method: str
    params: dict[str, Any]

    @property
    def text(self) -> str:
        return str(self.params.get("text", ""))

    @property
    def buttons(self) -> list[tuple[str, str]]:
        markup = self.params.get("reply_markup") or {}
        if isinstance(markup, str):
            markup = json.loads(markup)
        rows = markup.get("inline_keyboard", [])
        return [(b["text"], b["callback_data"]) for row in rows for b in row]


@dataclass
class FakeTelegram(BaseRequest):
    calls: list[Call] = field(default_factory=list)
    _message_ids: Iterator[int] = field(default_factory=lambda: itertools.count(1000))

    @property
    def read_timeout(self) -> float | None:
        return None

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: object = None,
        write_timeout: object = None,
        connect_timeout: object = None,
        pool_timeout: object = None,
    ) -> tuple[int, bytes]:
        name = url.rsplit("/", 1)[-1]
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append(Call(name, params))
        return 200, json.dumps({"ok": True, "result": self._result(name, params)}).encode()

    def _result(self, name: str, params: dict[str, Any]) -> object:
        if name == "getMe":
            return {"id": BOT_ID, "is_bot": True, "first_name": "Test", "username": "test_bot"}
        if name in {"sendMessage", "editMessageText", "editMessageReplyMarkup"}:
            chat_id = params.get("chat_id", OWNER)
            return bot_message(next(self._message_ids), int(chat_id), params.get("text", ""))
        return True

    def sent(self, method: str = "sendMessage") -> list[Call]:
        return [call for call in self.calls if call.method == method]

    def last_text(self) -> str:
        return self.sent()[-1].text


def user(user_id: int) -> dict[str, object]:
    return {"id": user_id, "is_bot": False, "first_name": "x"}


def bot_message(
    message_id: int, chat_id: int, text: str, chat_type: str = "private"
) -> dict[str, object]:
    return {
        "message_id": message_id,
        "date": int(time.time()),
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": BOT_ID, "is_bot": True, "first_name": "Test", "username": "test_bot"},
        "text": text,
    }


class Chat:
    # Builds updates as a given user would send them.
    def __init__(self, app: Application, user_id: int = OWNER, chat_type: str = "private") -> None:
        self._app = app
        self._user_id = user_id
        self._chat_type = chat_type
        self._ids = itertools.count(1)

    async def send(self, text: str) -> None:
        number = next(self._ids)
        message: dict[str, object] = {
            "message_id": number,
            "date": int(time.time()),
            "chat": {"id": self._user_id, "type": self._chat_type},
            "from": user(self._user_id),
            "text": text,
        }
        if text.startswith("/"):
            command = text.split()[0]
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(command)}]
        await self._process({"update_id": number, "message": message})

    async def press(self, data: str) -> None:
        number = next(self._ids)
        query = {
            "id": str(number),
            "from": user(self._user_id),
            "chat_instance": "test",
            "data": data,
            "message": bot_message(number, self._user_id, "buttons", self._chat_type),
        }
        await self._process({"update_id": number, "callback_query": query})

    async def _process(self, data: dict[str, object]) -> None:
        await self._app.process_update(Update.de_json(data, self._app.bot))


@dataclass
class Harness:
    app: Application
    telegram: FakeTelegram
    db: Database

    def chat(self, user_id: int = OWNER, chat_type: str = "private") -> Chat:
        return Chat(self.app, user_id, chat_type)

    def log_conversation(self) -> ConversationHandler:
        return next(
            handler
            for handler in self.app.handlers[0]
            if isinstance(handler, ConversationHandler) and handler.name == "log"
        )


@asynccontextmanager
async def running_bot(
    tmp_path: Path, categories: Categories | None = None, **settings: str
) -> AsyncIterator[Harness]:
    # Reminders and nudges are off unless a test turns them on: their scheduled jobs would
    # otherwise be live, and the reminder job talks to CTFtime.
    settings = {"INACTIVITY_DAYS": "0", "REMINDER_HOURS": "0", **settings}
    config = load_config(env(OWNER_ID=str(OWNER), **settings))
    telegram = FakeTelegram()
    db = open_database(tmp_path / "bot.db")
    app = build_application(config, [], db, categories or Categories(), request=telegram)
    await app.initialize()
    # Starts the job queue (needed for /log's timeout) but not polling.
    await app.start()
    try:
        yield Harness(app, telegram, db)
    finally:
        await app.stop()
        await app.shutdown()
        # post_shutdown only runs inside run_polling, so close the database here.
        db.close()
