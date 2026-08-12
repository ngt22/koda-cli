"""Tests for `koda migrate`: legacy SQLite koda.db → Markdown entry files."""

import sqlite3

import pytest
import typer

import koda.runtime as runtime
from koda import md_store
from koda.commands import manage, memo


def _make_legacy_db(path, rows, *, with_title=True):
    """Build a legacy koda.db ``memos`` table and insert ``rows``.

    Each row is a dict with keys uid/idx/content/tags/shortcut/created_at/
    modified_at (+ title when ``with_title``).
    """
    conn = sqlite3.connect(path)
    title_col = ", title TEXT" if with_title else ""
    conn.execute(
        f"""CREATE TABLE memos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT, idx INTEGER, content TEXT, tags TEXT, shortcut TEXT,
            created_at TIMESTAMP, modified_at TIMESTAMP{title_col}
        )"""
    )
    cols = ["uid", "idx", "content", "tags", "shortcut", "created_at", "modified_at"]
    if with_title:
        cols.append("title")
    placeholders = ", ".join("?" for _ in cols)
    for r in rows:
        conn.execute(
            f"INSERT INTO memos ({', '.join(cols)}) VALUES ({placeholders})",
            tuple(r.get(c) for c in cols),
        )
    conn.commit()
    conn.close()


LEGACY_ROWS = [
    {
        "uid": "legacyuid0000001",
        "idx": 0,
        "content": "echo one",
        "tags": "work,ops",
        "shortcut": "one",
        "created_at": "2026-01-01 00:00:00",
        "modified_at": "2026-01-01 00:00:00",
        "title": "First entry",
    },
    {
        "uid": "legacyuid0000002",
        "idx": 1,
        "content": "echo two",
        "tags": "",
        "shortcut": None,
        "created_at": "2026-01-02 00:00:00",
        "modified_at": "2026-01-02 00:00:00",
        "title": None,
    },
]


def test_migrate_writes_one_md_per_row(tmp_path, db):
    legacy = tmp_path / "koda.db"
    _make_legacy_db(legacy, LEGACY_ROWS)

    manage.migrate(db_path=legacy, force=False)

    md_files = sorted(runtime.get_entries_dir().glob("*.md"))
    assert len(md_files) == 2


def test_migrate_preserves_fields(tmp_path, db):
    legacy = tmp_path / "koda.db"
    _make_legacy_db(legacy, LEGACY_ROWS)

    manage.migrate(db_path=legacy, force=False)

    row1 = db.get_memo_by_uid("legacyuid0000001")
    assert row1 is not None
    assert row1.idx == 0
    assert row1.content == "echo one"
    assert row1.tags == "work,ops"
    assert row1.shortcut == "one"
    assert row1.title == "First entry"
    # uid is preserved verbatim from the legacy row (stable sync identity).
    assert row1.uid == "legacyuid0000001"

    row2 = db.get_memo_by_uid("legacyuid0000002")
    assert row2.idx == 1
    assert row2.content == "echo two"


def test_migrate_files_have_preserved_uid_in_frontmatter(tmp_path, db):
    legacy = tmp_path / "koda.db"
    _make_legacy_db(legacy, LEGACY_ROWS)
    manage.migrate(db_path=legacy, force=False)

    uids = {md_store.read_entry(p).uid for p in runtime.get_entries_dir().glob("*.md")}
    assert uids == {"legacyuid0000001", "legacyuid0000002"}


def test_migrate_entries_visible_in_list(tmp_path, db, capsys):
    legacy = tmp_path / "koda.db"
    _make_legacy_db(legacy, LEGACY_ROWS)
    manage.migrate(db_path=legacy, force=False)
    capsys.readouterr()  # discard migrate's own summary

    memo._list_memos_impl(display="body")
    out = capsys.readouterr().out
    assert "echo one" in out
    assert "echo two" in out


def test_migrate_missing_legacy_db_errors(tmp_path, db):
    with pytest.raises(typer.Exit):
        manage.migrate(db_path=tmp_path / "does-not-exist.db", force=False)


def test_migrate_refuses_nonempty_vault_without_force(tmp_path, db, seed):
    seed("existing entry")  # vault now has one .md
    legacy = tmp_path / "koda.db"
    _make_legacy_db(legacy, LEGACY_ROWS)
    with pytest.raises(typer.Exit):
        manage.migrate(db_path=legacy, force=False)


def test_migrate_without_title_column(tmp_path, db):
    """A very old koda.db lacking the title column still migrates."""
    legacy = tmp_path / "koda.db"
    rows = [
        {
            "uid": "notitle000000001",
            "idx": 0,
            "content": "no title body",
            "tags": "",
            "shortcut": None,
            "created_at": "2026-01-01 00:00:00",
            "modified_at": "2026-01-01 00:00:00",
        }
    ]
    _make_legacy_db(legacy, rows, with_title=False)
    manage.migrate(db_path=legacy, force=False)
    row = db.get_memo_by_uid("notitle000000001")
    assert row.content == "no title body"
    assert row.title is None


def test_migrate_skips_rows_with_existing_uid(tmp_path, db, write_md):
    """migrate -f must not write a duplicate .md for a uid already in the vault,
    and must not overwrite the existing file (no irreversible writes)."""
    write_md("vault edited version", idx=0, uid="legacyuid0000001", title="First entry")

    legacy = tmp_path / "koda.db"
    _make_legacy_db(legacy, [LEGACY_ROWS[0]])

    manage.migrate(db_path=legacy, force=True)

    entries = runtime.get_entries_dir()
    uids = [md_store.read_entry(p).uid for p in entries.glob("*.md")]
    assert uids.count("legacyuid0000001") == 1
    assert db.get_memo_by_uid("legacyuid0000001").content == "vault edited version"


def test_migrate_records_skipped_rows_as_koda_entry_and_prints_them(tmp_path, db, write_md, capsys):
    write_md("vault edited version", idx=0, uid="legacyuid0000001", title="First entry")
    legacy = tmp_path / "koda.db"
    _make_legacy_db(legacy, LEGACY_ROWS)

    manage.migrate(db_path=legacy, force=True)

    skip_rows = [r for r in db.get_memos(limit=None) if r.tags == "migrate"]
    assert len(skip_rows) == 1
    assert "legacyuid0000001" in skip_rows[0].content
    assert "First entry" in skip_rows[0].content

    out = capsys.readouterr().out
    assert "Skipped" in out
    assert "legacyuid0000001" in out
