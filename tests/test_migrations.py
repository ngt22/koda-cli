"""Schema migration framework tests (E3-6)."""

import sqlite3

from koda.db import SCHEMA_VERSION, UID_LENGTH, MemoDatabase, compute_uid


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
    # Existing data survives the migration, and its uid is widened to
    # UID_LENGTH hex chars (recomputed from content + created_at).
    expected_uid = compute_uid("legacy entry", "2026-01-01 00:00:00")
    assert len(expected_uid) == UID_LENGTH
    row = db.get_memo_by_uid(expected_uid)
    assert row is not None and row.content == "legacy entry"
    assert len(row.uid) == UID_LENGTH


def test_source_column_added_and_defaults_local(tmp_path):
    """Migration 0003 adds the source column; pre-existing rows default to
    'local' (the user's own work)."""
    old_path = tmp_path / "v2.db"
    conn = sqlite3.connect(old_path)
    conn.executescript(
        """
        CREATE TABLE memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, idx INTEGER UNIQUE,
            shortcut TEXT, content TEXT, tags TEXT, created_at TIMESTAMP, modified_at TIMESTAMP
        );
        PRAGMA user_version = 2;
        """
    )
    full = compute_uid("body", "2026-01-01 00:00:00")
    conn.execute(
        "INSERT INTO memos (uid, idx, content, tags, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (full, 0, "body", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"),
    )
    conn.commit()
    conn.close()

    db = MemoDatabase(backend="local", path=old_path)
    db.init_db()

    assert "source" in _columns(db)
    assert db.get_memo_by_uid(full).source == "local"


def test_init_db_is_idempotent(tmp_path):
    db = MemoDatabase(backend="local", path=tmp_path / "idem.db")
    db.init_db()
    db.add_memo("uid0001", 0, "sc", "content", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")

    # Re-running init_db must not error, re-run migrations, or drop data.
    db.init_db()

    assert _user_version(db) == SCHEMA_VERSION
    assert db.get_memo_by_uid("uid0001") is not None


def test_widen_uid_migration_preserves_prefix(tmp_path):
    """A genuine pre-widening 7-char uid (the truncated hash) is lengthened to
    UID_LENGTH while keeping the old value as its prefix, so synced peers stay
    aligned after both migrate."""
    content, created = "deploy prod", "2026-02-02 12:00:00"
    full = compute_uid(content, created)
    legacy = full[:7]

    old_path = tmp_path / "v1.db"
    conn = sqlite3.connect(old_path)
    conn.executescript(
        """
        CREATE TABLE memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT UNIQUE, idx INTEGER UNIQUE,
            shortcut TEXT, content TEXT, tags TEXT, created_at TIMESTAMP, modified_at TIMESTAMP
        );
        PRAGMA user_version = 1;
        """
    )
    conn.execute(
        "INSERT INTO memos (uid, idx, content, tags, created_at, modified_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (legacy, 0, content, "", created, created),
    )
    conn.commit()
    conn.close()

    db = MemoDatabase(backend="local", path=old_path)
    db.init_db()

    row = db.get_memo_by_uid(full)
    assert row is not None
    assert row.uid == full and row.uid.startswith(legacy)
    # The legacy short uid still resolves via prefix lookup.
    assert db.get_memo_by_uid_prefix(legacy) is not None
