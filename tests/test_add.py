"""Tests for `add` content sourcing: argument vs piped stdin vs editor."""

from pathlib import Path

import pytest

import koda.main as main


class FakeStdin:
    def __init__(self, data="", tty=True):
        self._data = data
        self._tty = tty

    def isatty(self):
        return self._tty

    def read(self):
        return self._data


@pytest.fixture
def wired_db(db, monkeypatch):
    """Point koda.main's module-level db at a fresh temp database."""
    monkeypatch.setattr(main, "db", db)
    return db


def _latest_content(db):
    row = db.get_latest_entry()
    return row.content if row else None


def test_arg_only_uses_arg(wired_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    main._add_impl(text=["hello", "world"])
    assert _latest_content(wired_db) == "hello world"


def test_stdin_only_uses_stdin(wired_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(data="piped body\n", tty=False))
    main._add_impl(text=None)
    assert _latest_content(wired_db) == "piped body"


def test_arg_wins_over_stdin_and_warns(wired_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", FakeStdin(data="ignored stdin", tty=False))
    main._add_impl(text=["arg body"])
    assert _latest_content(wired_db) == "arg body"
    assert "ignoring piped stdin" in capsys.readouterr().err


def test_arg_with_empty_noninteractive_stdin_no_warning(wired_db, monkeypatch, capsys):
    """The original bug: non-interactive shell with empty stdin (e.g. /dev/null)
    must still save the argument and not abort or warn."""
    monkeypatch.setattr("sys.stdin", FakeStdin(data="", tty=False))
    main._add_impl(text=["hello"])
    assert _latest_content(wired_db) == "hello"
    assert "ignoring piped stdin" not in capsys.readouterr().err


def test_no_arg_no_stdin_uses_editor(wired_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))

    def fake_call(cmd):
        Path(cmd[1]).write_text("from editor")
        return 0

    monkeypatch.setattr(main.subprocess, "call", fake_call)
    main._add_impl(text=None)
    assert _latest_content(wired_db) == "from editor"
