"""`raw` prints the stored body byte-for-byte — no comment stripping, no newline normalization."""

import pytest
from _helpers import put_entry

import koda.runtime as runtime


@pytest.fixture
def wired_db(db):
    return db


def _seed(db, content):
    put_entry(content, idx=1, uid="seed001000000000")


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
