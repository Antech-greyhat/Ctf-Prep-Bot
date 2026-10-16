# SQLite storage for writeups and bot state. Standard library only.
#
# Calls are synchronous. For one user and a small local file each query takes well under a
# millisecond, so running them on the event loop is simpler than adding threads.
import logging
import os
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ctfbot.writeups import Writeup, WriteupDraft

logger = logging.getLogger(__name__)

# Entry N upgrades the schema from version N-1 to N. Never edit a released migration: append
# a new one so existing databases upgrade cleanly. Each script is a complete literal
# transaction that also sets its own version number, so no SQL is ever assembled at runtime
# and a failed step leaves the previous version intact.
MIGRATIONS: tuple[str, ...] = (
    """
    BEGIN;
    -- AUTOINCREMENT: a deleted writeup's ID is never reused, so "#12" always means one entry.
    CREATE TABLE writeups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        platform TEXT NOT NULL,
        challenge TEXT NOT NULL,
        category TEXT NOT NULL,
        solved INTEGER NOT NULL CHECK (solved IN (0, 1)),
        approach TEXT NOT NULL,
        stuck TEXT NOT NULL
    );
    CREATE INDEX writeups_by_time ON writeups (created_at);
    CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE sent_reminders (event_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
    PRAGMA user_version = 1;
    COMMIT;
    """,
)


class StorageError(Exception):
    pass


def to_db_time(moment: datetime) -> str:
    # Fixed format (UTC, whole seconds) so text comparison and sorting match time order.
    if moment.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def from_db_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class Database:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    def close(self) -> None:
        self._conn.close()

    # Writeups. Every query uses ? placeholders; user text is never put into SQL strings.

    def add_writeup(self, draft: WriteupDraft, now: datetime) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO writeups (created_at, platform, challenge, category, solved, "
                "approach, stuck) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    to_db_time(now),
                    draft.platform,
                    draft.challenge,
                    draft.category,
                    int(draft.solved),
                    draft.approach,
                    draft.stuck,
                ),
            )
        return int(cursor.lastrowid)

    def get_writeup(self, writeup_id: int) -> Writeup | None:
        row = self._conn.execute(
            "SELECT id, created_at, platform, challenge, category, solved, approach, stuck "
            "FROM writeups WHERE id = ?",
            (writeup_id,),
        ).fetchone()
        return _to_writeup(row) if row else None

    def recent_writeups(self, limit: int) -> list[Writeup]:
        rows = self._conn.execute(
            "SELECT id, created_at, platform, challenge, category, solved, approach, stuck "
            "FROM writeups ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_to_writeup(row) for row in rows]

    def delete_writeup(self, writeup_id: int) -> bool:
        with self._conn:
            cursor = self._conn.execute("DELETE FROM writeups WHERE id = ?", (writeup_id,))
        return cursor.rowcount == 1

    def solved_counts(self) -> Counter[str]:
        rows = self._conn.execute(
            "SELECT category, COUNT(*) FROM writeups WHERE solved = 1 GROUP BY category"
        ).fetchall()
        return Counter({category: count for category, count in rows})

    def attempt_counts(self) -> Counter[str]:
        rows = self._conn.execute(
            "SELECT category, COUNT(*) FROM writeups GROUP BY category"
        ).fetchall()
        return Counter({category: count for category, count in rows})

    def categories_since(self, since: datetime) -> list[str]:
        rows = self._conn.execute(
            "SELECT category FROM writeups WHERE created_at >= ? ORDER BY created_at",
            (to_db_time(since),),
        ).fetchall()
        return [category for (category,) in rows]

    def last_writeup_at(self) -> datetime | None:
        (value,) = self._conn.execute("SELECT MAX(created_at) FROM writeups").fetchone()
        return from_db_time(value) if value else None

    # Small key-value store for scheduler bookkeeping.

    def get_state(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # CTF reminders already sent, so a restart never repeats one.

    def reminder_sent(self, event_key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sent_reminders WHERE event_key = ?", (event_key,)
        ).fetchone()
        return row is not None

    def mark_reminder_sent(self, event_key: str, now: datetime) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO sent_reminders (event_key, sent_at) VALUES (?, ?)",
                (event_key, to_db_time(now)),
            )

    def prune_reminders(self, before: datetime) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sent_reminders WHERE sent_at < ?", (to_db_time(before),)
            )
        return cursor.rowcount


def open_database(path: Path) -> Database:
    try:
        _prepare_file(path)
        connection = sqlite3.connect(path)
    except (OSError, sqlite3.Error) as exc:
        raise StorageError(f"cannot open database {path}: {exc}") from None
    try:
        # Overwrite deleted content instead of leaving it readable in free pages,
        # so /delete really removes a writeup from the file.
        connection.execute("PRAGMA secure_delete = ON")
        _migrate(connection)
    except sqlite3.Error as exc:
        connection.close()
        raise StorageError(f"database {path} could not be prepared: {exc}") from None
    except StorageError:
        connection.close()
        raise
    return Database(connection)


def _prepare_file(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        # Create the file ourselves, before SQLite writes a byte, so it is private (600)
        # from the start. O_EXCL fails if anything (even a symlink) already exists there.
        # Windows ignores the mode; there the folder's ACL decides who can read the file.
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        _tighten_permissions(path)


def _tighten_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    if path.stat().st_mode & 0o077:
        path.chmod(0o600)
        logger.warning("Database file %s was readable by other users; reset to 600", path)


def _migrate(connection: sqlite3.Connection) -> None:
    (version,) = connection.execute("PRAGMA user_version").fetchone()
    if version > len(MIGRATIONS):
        raise StorageError(
            f"the database is schema version {version}, but this bot only knows up to "
            f"{len(MIGRATIONS)}. It was created by a newer ctf-prep-bot; upgrade the bot."
        )
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        try:
            connection.executescript(script)
        except sqlite3.Error:
            # The script stopped inside its own BEGIN; undo the partial step.
            connection.rollback()
            raise
        logger.info("Database upgraded to schema version %d", number)


def _to_writeup(row: tuple[object, ...]) -> Writeup:
    writeup_id, created_at, platform, challenge, category, solved, approach, stuck = row
    return Writeup(
        id=int(writeup_id),
        created_at=from_db_time(str(created_at)),
        platform=str(platform),
        challenge=str(challenge),
        category=str(category),
        solved=bool(solved),
        approach=str(approach),
        stuck=str(stuck),
    )
