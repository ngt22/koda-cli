"""Schema migration framework tests (E3-6)."""

import sqlite3

from koda.db import SCHEMA_VERSION, MemoDatabase


def _user_version(db: MemoDatabase) -> int:
    with db.connection() as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _columns(db: MemoDatabase) -> set[str]:
    with db.connection() as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(memos)").fetchall()}


def test_fresh_init_applies_all_migrations(tmp_path):
    db = MemoDatabase(backend="local", path=tmp_path / "fresh.db")
    db.init_db()

    assert _user_version(db) == SCHEMA_VERSION
    assert {"id", "uid", "idx", "shortcut", "content", "tags"} <= _columns(db)


def test_up_migration_from_pre_versioning_schema(tmp_path):
    """A pre-versioning DB (no shortcut column, user_version 0) upgrades cleanly."""
    old_path = tmp_path / "old.db"
    conn = sqlite3.connect(old_path)
    conn.execute(
        """
        CREATE TABLE memos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            uid         TEXT UNIQUE,
            idx         INTEGER UNIQUE,
            content     TEXT,
            tags        TEXT,
            created_at  TIMESTAMP,
            modified_at TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO memos (uid, idx, content, tags, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("abc1234", 0, "legacy entry", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
    )
    conn.commit()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "shortcut" not in {r[1] for r in conn.execute("PRAGMA table_info(memos)").fetchall()}
    conn.close()

    db = MemoDatabase(backend="local", path=old_path)
    db.init_db()

    assert _user_version(db) == SCHEMA_VERSION
    assert "shortcut" in _columns(db)
    # Existing data survives the migration.
    row = db.get_memo_by_uid("abc1234")
    assert row is not None and row.content == "legacy entry"


def test_init_db_is_idempotent(tmp_path):
    db = MemoDatabase(backend="local", path=tmp_path / "idem.db")
    db.init_db()
    db.add_memo("uid0001", 0, "sc", "content", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")

    # Re-running init_db must not error, re-run migrations, or drop data.
    db.init_db()

    assert _user_version(db) == SCHEMA_VERSION
    assert db.get_memo_by_uid("uid0001") is not None
