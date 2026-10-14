import asyncio
import sys
import time
from collections.abc import Callable

from telegram import Bot, Update, User
from telegram.constants import ChatType
from telegram.error import Conflict

from ctfbot.models import clean_text

TIMEOUT_SECONDS = 120
POLL_SECONDS = 10


async def wait_for_private_sender(
    bot: Bot, timeout: float = TIMEOUT_SECONDS, clock: Callable[[], float] = time.monotonic
) -> User | None:
    deadline = clock() + timeout
    # Skip messages queued before we started so a stranger's old one isn't picked.
    pending = await bot.get_updates(offset=-1, timeout=0, allowed_updates=[Update.MESSAGE])
    offset = pending[-1].update_id + 1 if pending else None
    while (remaining := deadline - clock()) > 0:
        updates = await bot.get_updates(
            offset=offset,
            timeout=int(min(remaining, POLL_SECONDS)),
            allowed_updates=[Update.MESSAGE],
        )
        for update in updates:
            offset = update.update_id + 1
            message = update.message
            if message and message.chat.type == ChatType.PRIVATE and message.from_user:
                return message.from_user
    return None


def describe_user(user: User) -> str:
    username = f"@{clean_text(user.username)}" if user.username else "(no username set)"
    return "\n".join(
        [
            f"User ID:  {user.id}",
            f"Username: {username}",
            "",
            "If the username is yours, set this in .env:",
            f"OWNER_ID={user.id}",
        ]
    )


async def _whoami(token: str) -> int:
    async with Bot(token) as bot:
        print(f"Send any message to @{bot.username} in a private chat ({TIMEOUT_SECONDS}s)...")
        try:
            user = await wait_for_private_sender(bot)
        except Conflict:
            print("Error: the bot is already running elsewhere. Stop it first.", file=sys.stderr)
            return 1
    if user is None:
        print(f"Timed out after {TIMEOUT_SECONDS}s without a private message.", file=sys.stderr)
        return 1
    print(describe_user(user))
    return 0


def run_whoami(token: str) -> int:
    return asyncio.run(_whoami(token))
