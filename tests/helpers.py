from datetime import UTC, datetime, timedelta

from telegram import Chat, Message, Update, User

from ctfbot.models import Event

# Date the CTFtime fixture was recorded.
FIXTURE_NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def make_event(
    title: str = "Example CTF",
    start: datetime = FIXTURE_NOW,
    hours: float = 24,
    url: str | None = None,
    source: str = "CTFtime",
) -> Event:
    return Event(
        title=title, start=start, finish=start + timedelta(hours=hours), url=url, source=source
    )


# Fake token. Never put a real one in tests.
FAKE_TOKEN = "123456789:AAFakeFakeFakeFakeFakeFakeFakeFake12"  # noqa: S105


def env(**overrides: str) -> dict[str, str]:
    return {"TELEGRAM_BOT_TOKEN": FAKE_TOKEN, "OWNER_ID": "424242", **overrides}


def make_update(
    user_id: int | None,
    chat_type: str = Chat.PRIVATE,
    text: str = "/start",
    update_id: int = 1,
    username: str | None = None,
) -> Update:
    user = (
        User(id=user_id, first_name="x", is_bot=False, username=username)
        if user_id is not None
        else None
    )
    chat = Chat(id=user_id or -1001, type=chat_type)
    message = Message(message_id=1, date=datetime.now(UTC), chat=chat, from_user=user, text=text)
    return Update(update_id=update_id, message=message)
