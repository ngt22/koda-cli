"""`raw` prints the stored body byte-for-byte — no comment stripping, no newline normalization."""

import pytest

import koda.runtime as runtime


@pytest.fixture
def wired_db(db, monkeypatch):
    """Point the lazy DB cache at a fresh temp database."""
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, content):
    db.add_memo(
        uid="seed001",
        idx=1,
        shortcut=None,
        content=content,
        tags="",
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def test_empty_content_stays_empty(wired_db, capsys):
    _seed(wired_db, "")
    runtime.emit_raw(None)
    assert capsys.readouterr().out == ""


def test_preserves_missing_newline(wired_db, capsys):
    _seed(wired_db, "no trailing newline")
    runtime.emit_raw(None)
    assert capsys.readouterr().out == "no trailing newline"


def test_preserves_inline_comment(wired_db, capsys):
    _seed(wired_db, "echo hello  # this is a comment")
    runtime.emit_raw(None)
    assert capsys.readouterr().out == "echo hello  # this is a comment"


def test_preserves_existing_newline(wired_db, capsys):
    _seed(wired_db, "already terminated\n")
    runtime.emit_raw(None)
    assert capsys.readouterr().out == "already terminated\n"
