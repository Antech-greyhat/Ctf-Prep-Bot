import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ctfbot import storage
from ctfbot.storage import (
    MIGRATIONS,
    Database,
    StorageError,
    from_db_time,
    open_database,
    to_db_time,
)
from ctfbot.writeups import WriteupDraft

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")


def draft(category: str = "Web", solved: bool = True, **overrides: str) -> WriteupDraft:
    fields = {
        "platform": "picoCTF",
        "challenge": "Cookies",
        "approach": "Changed the cookie value.",
        "stuck": "",
        **overrides,
    }
    return WriteupDraft(category=category, solved=solved, **fields)


def user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    database = open_database(tmp_path / "data" / "bot.db")
    yield database
    database.close()


def test_add_and_get_round_trip(db: Database) -> None:
    writeup_id = db.add_writeup(draft(stuck="Took a while"), NOW)
    stored = db.get_writeup(writeup_id)
    assert stored is not None
    assert (stored.id, stored.created_at, stored.challenge) == (writeup_id, NOW, "Cookies")
    assert stored.solved is True
    assert stored.stuck == "Took a while"


def test_missing_writeup_is_none(db: Database) -> None:
    assert db.get_writeup(12345) is None
    assert db.delete_writeup(12345) is False


def test_sql_metacharacters_are_stored_literally(db: Database) -> None:
    nasty = "x'); DROP TABLE writeups; --"
    writeup_id = db.add_writeup(draft(challenge=nasty), NOW)
    stored = db.get_writeup(writeup_id)
    assert stored is not None and stored.challenge == nasty
    assert db.recent_writeups(10)


def test_recent_is_newest_first_and_limited(db: Database) -> None:
    ids = [db.add_writeup(draft(challenge=f"c{n}"), NOW + timedelta(minutes=n)) for n in range(5)]
    assert [w.id for w in db.recent_writeups(3)] == ids[::-1][:3]


def test_deleted_ids_are_never_reused(db: Database) -> None:
    first = db.add_writeup(draft(), NOW)
    assert db.delete_writeup(first) is True
    assert db.add_writeup(draft(), NOW) != first


def test_counts_by_category(db: Database) -> None:
    db.add_writeup(draft("Web", solved=True), NOW)
    db.add_writeup(draft("Web", solved=False), NOW)
    db.add_writeup(draft("Crypto", solved=True), NOW)
    assert db.solved_counts() == {"Web": 1, "Crypto": 1}
    assert db.attempt_counts() == {"Web": 2, "Crypto": 1}


def test_categories_since_and_last_writeup(db: Database) -> None:
    assert db.last_writeup_at() is None
    db.add_writeup(draft("Web"), NOW - timedelta(days=10))
    db.add_writeup(draft("Pwn"), NOW - timedelta(days=1))
    assert db.categories_since(NOW - timedelta(days=7)) == ["Pwn"]
    assert db.last_writeup_at() == NOW - timedelta(days=1)


def test_state_upsert(db: Database) -> None:
    assert db.get_state("k") is None
    db.set_state("k", "1")
    db.set_state("k", "2")
    assert db.get_state("k") == "2"


def test_reminders_are_remembered_and_pruned(db: Database) -> None:
    db.mark_reminder_sent("old", NOW - timedelta(days=40))
    db.mark_reminder_sent("new", NOW)
    db.mark_reminder_sent("new", NOW)
    assert db.reminder_sent("new") and db.reminder_sent("old")
    assert db.prune_reminders(NOW - timedelta(days=30)) == 1
    assert not db.reminder_sent("old")


def test_data_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    first = open_database(path)
    writeup_id = first.add_writeup(draft(), NOW)
    first.close()
    second = open_database(path)
    assert second.get_writeup(writeup_id) is not None
    second.close()


def test_schema_version_is_recorded(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    open_database(path).close()
    assert user_version(path) == len(MIGRATIONS)


def test_each_migration_is_one_transaction_that_sets_its_own_version() -> None:
    for number, script in enumerate(MIGRATIONS, start=1):
        lines = [line.strip() for line in script.strip().splitlines()]
        assert lines[0] == "BEGIN;"
        assert lines[-2] == f"PRAGMA user_version = {number};"
        assert lines[-1] == "COMMIT;"


def test_failed_migration_keeps_the_previous_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "bot.db"
    open_database(path).close()
    broken = "BEGIN;\nCREATE TABLE extra (x);\nNOT VALID SQL;\nPRAGMA user_version = 2;\nCOMMIT;"
    monkeypatch.setattr(storage, "MIGRATIONS", (*MIGRATIONS, broken))
    with pytest.raises(StorageError, match="could not be prepared"):
        open_database(path)
    assert user_version(path) == 1
    with closing(sqlite3.connect(path)) as conn:
        tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master")}
    assert "extra" not in tables


def test_database_from_newer_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    assert len(MIGRATIONS) < 99
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version = 99")
    with pytest.raises(StorageError, match="newer"):
        open_database(path)


def test_non_database_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    path.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(StorageError, match="could not be prepared"):
        open_database(path)


def test_secure_delete_is_on(db: Database) -> None:
    assert db._conn.execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_deleted_text_is_not_left_in_the_file(tmp_path: Path) -> None:
    path = tmp_path / "bot.db"
    database = open_database(path)
    marker = "UNIQUE-MARKER-7f3a9c"
    writeup_id = database.add_writeup(draft(approach=marker), NOW)
    database.close()
    assert marker.encode() in path.read_bytes()

    database = open_database(path)
    database.delete_writeup(writeup_id)
    database.close()
    assert marker.encode() not in path.read_bytes()


@posix_only
def test_new_database_file_and_folder_are_private(tmp_path: Path) -> None:
    path = tmp_path / "data" / "bot.db"
    open_database(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0


@posix_only
def test_world_readable_database_is_tightened(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "bot.db"
    open_database(path).close()
    path.chmod(0o644)
    open_database(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "reset to 600" in caplog.text


def test_times_round_trip_in_utc() -> None:
    local = datetime(2026, 10, 16, 15, 0, tzinfo=UTC).astimezone()
    assert from_db_time(to_db_time(local)) == local
    assert to_db_time(NOW) == "2026-10-16T12:00:00+00:00"


def test_naive_times_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        to_db_time(datetime(2026, 1, 1))
