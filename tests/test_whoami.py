import pytest
from telegram import Chat, Update, User

from ctfbot import __main__ as entry
from ctfbot.whoami import describe_user, wait_for_private_sender
from tests.helpers import FAKE_TOKEN, make_update

OWNER = 424242
STRANGER = 999


class FakeBot:
    # No send_message on purpose: any reply to the chat would crash the test.
    def __init__(self, batches: list[list[Update]]) -> None:
        self.batches = batches
        self.offsets: list[int | None] = []

    async def get_updates(
        self, offset: int | None, timeout: int, allowed_updates: list[str]
    ) -> list[Update]:
        self.offsets.append(offset)
        return self.batches.pop(0) if self.batches else []


class StepClock:
    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


async def test_stale_messages_are_skipped_and_first_private_sender_is_returned() -> None:
    stale = make_update(STRANGER, update_id=5)
    group = make_update(STRANGER, Chat.GROUP, update_id=6)
    mine = make_update(OWNER, update_id=7, username="me")
    bot = FakeBot([[stale], [group, mine]])
    user = await wait_for_private_sender(bot, clock=StepClock(1))
    assert user is not None
    assert user.id == OWNER
    assert bot.offsets == [-1, 6]


async def test_times_out_without_a_message() -> None:
    bot = FakeBot([[]])
    assert await wait_for_private_sender(bot, timeout=120, clock=StepClock(50)) is None


def test_describe_user_shows_id_and_username() -> None:
    text = describe_user(User(id=OWNER, first_name="x", is_bot=False, username="me"))
    assert "User ID:  424242" in text
    assert "@me" in text
    assert "OWNER_ID=424242" in text


def test_describe_user_without_username() -> None:
    text = describe_user(User(id=OWNER, first_name="x", is_bot=False))
    assert "(no username set)" in text


def test_whoami_needs_only_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[str] = []
    monkeypatch.setattr(entry, "read_environment", lambda: {"TELEGRAM_BOT_TOKEN": FAKE_TOKEN})
    monkeypatch.setattr(entry, "run_whoami", lambda token: received.append(token) or 0)
    assert entry.main(["--whoami"]) == 0
    assert received == [FAKE_TOKEN]


def test_whoami_fails_closed_without_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(entry, "read_environment", lambda: {})
    assert entry.main(["--whoami"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err
