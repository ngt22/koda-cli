"""Tests for title CLI: add --title, edit via .md, show/list display, query (#141)."""

import json
from pathlib import Path

import pytest
import typer
from _helpers import put_entry

from koda import md_store
from koda.commands import memo
from koda.runtime import get_entries_dir


class FakeStdin:
    def __init__(self, data="", tty=True):
        self._data = data
        self._tty = tty

    def isatty(self):
        return self._tty

    def read(self):
        return self._data


@pytest.fixture
def wired_db(db):
    return db


# ── _validate_title ──────────────────────────────────────────────────────────


def test_validate_title_none_returns_none():
    assert memo._validate_title(None) is None


def test_validate_title_strips_whitespace():
    assert memo._validate_title("  My Title  ") == "My Title"


def test_validate_title_empty_string_errors(capsys):
    with pytest.raises(typer.Exit):
        memo._validate_title("")
    assert "Title cannot be empty" in capsys.readouterr().err


def test_validate_title_whitespace_only_errors(capsys):
    with pytest.raises(typer.Exit):
        memo._validate_title("   ")
    assert "Title cannot be empty" in capsys.readouterr().err


def test_validate_title_newline_errors(capsys):
    with pytest.raises(typer.Exit):
        memo._validate_title("Line one\nLine two")
    assert "Title must be a single line" in capsys.readouterr().err


# ── add --title ──────────────────────────────────────────────────────────────


def test_add_with_title_persists(wired_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    memo._add_impl(text=["deploy body"], title="Deploy prod")
    row = wired_db.get_latest_entry()
    assert row.title == "Deploy prod"


def test_add_with_title_visible_in_show_json(wired_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    memo._add_impl(text=["deploy body"], title="Deploy prod")
    row = wired_db.get_latest_entry()
    capsys.readouterr()  # discard the "Saved" success message
    memo.show(ref=str(row.idx), json_output=True)
    obj = json.loads(capsys.readouterr().out)
    assert obj["title"] == "Deploy prod"


def test_add_empty_title_rejected(wired_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    with pytest.raises(typer.Exit):
        memo._add_impl(text=["body"], title="")
    assert "Title cannot be empty" in capsys.readouterr().err
    assert wired_db.get_latest_entry() is None


def test_add_multiline_title_rejected(wired_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    with pytest.raises(typer.Exit):
        memo._add_impl(text=["body"], title="Line one\nLine two")
    assert "Title must be a single line" in capsys.readouterr().err
    assert wired_db.get_latest_entry() is None


def test_add_success_message_includes_title(wired_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    memo._add_impl(text=["body"], title="My Label")
    out = capsys.readouterr().out
    assert "title: My Label" in out


def test_add_with_description_persists(wired_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    memo._add_impl(text=["deploy body"], description="one-line summary")
    row = wired_db.get_latest_entry()
    assert row.description == "one-line summary"


# ── edit the .md frontmatter title ────────────────────────────────────────────


def _seed(db, content="body", title=None, shortcut=None):
    return put_entry(content, idx=0, shortcut=shortcut, tags="work", title=title, uid="uid00010000")


def test_edit_sets_title(wired_db, monkeypatch):
    _seed(wired_db)

    def fake_editor(path):
        entry = md_store.read_entry(Path(path))
        entry.title = "New Title"
        md_store.write_entry(get_entries_dir(), entry, path=Path(path))

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title == "New Title"


def test_edit_clears_title(wired_db, monkeypatch):
    _seed(wired_db, title="Old Title")

    def fake_editor(path):
        entry = md_store.read_entry(Path(path))
        entry.title = None
        md_store.write_entry(get_entries_dir(), entry, path=Path(path))

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title is None


def test_edit_changes_title(wired_db, monkeypatch):
    _seed(wired_db, title="Old Title")

    def fake_editor(path):
        entry = md_store.read_entry(Path(path))
        entry.title = "Updated Title"
        md_store.write_entry(get_entries_dir(), entry, path=Path(path))

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title == "Updated Title"


def test_edit_marks_source_local(wired_db, monkeypatch):
    """Reviewing a remote entry via edit trusts it (source -> local)."""
    put_entry("remote body", idx=0, uid="rem00010000", source="remote")
    assert wired_db.get_memo_by_idx(0).source == "remote"

    def fake_editor(path):
        entry = md_store.read_entry(Path(path))
        entry.content = "reviewed body"
        md_store.write_entry(get_entries_dir(), entry, path=Path(path))

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    row = wired_db.get_memo_by_idx(0)
    assert row.content == "reviewed body"
    assert row.source == "local"


# ── query matches title ──────────────────────────────────────────────────────


def _seed_two(db):
    """Insert two entries: one with a title hit, one with a body hit."""
    put_entry("unrelated body", idx=0, tags="work", title="Deploy prod", uid="uid00010000")
    put_entry("docker compose up", idx=1, uid="uid00020000")


def test_query_matches_title_only_hit(wired_db):
    _seed_two(wired_db)
    # "Deploy" only appears in the title of entry 0, not in its body.
    rows = wired_db.get_memos(query="Deploy")
    assert len(rows) == 1
    assert rows[0].uid == "uid00010000"


def test_query_matches_body_hit(wired_db):
    _seed_two(wired_db)
    rows = wired_db.get_memos(query="docker")
    assert len(rows) == 1
    assert rows[0].uid == "uid00020000"


def test_list_with_query_shows_title_hit(wired_db, capsys):
    _seed_two(wired_db)
    memo._list_memos_impl(query="Deploy")
    out = capsys.readouterr().out
    assert "Deploy prod" in out


def test_remove_batch_query_matches_title(wired_db, capsys):
    _seed_two(wired_db)
    memo.rm(indices=None, tag=None, query="Deploy", all_entries=False, force=True)
    assert wired_db.get_memo_by_idx(0) is None
    # Body-match entry survives.
    assert wired_db.get_memo_by_idx(1) is not None


# ── copy preserves title ──────────────────────────────────────────────────────


def test_copy_preserves_title(wired_db):
    _seed(wired_db, title="Original Title")
    memo.copy("0")
    copied = wired_db.get_memo_by_idx(1)
    assert copied is not None
    assert copied.title == "Original Title"


# ── show/list --json includes title ───────────────────────────────────────────


def test_show_json_includes_title(wired_db, capsys):
    _seed(wired_db, title="JSON Title")
    memo.show(ref="0", json_output=True)
    obj = json.loads(capsys.readouterr().out)
    assert obj["title"] == "JSON Title"


def test_list_json_includes_title(wired_db, capsys):
    _seed(wired_db, title="List JSON Title")
    memo._emit_list_json(None, None, None, False, None, None)
    data = json.loads(capsys.readouterr().out)
    assert data[0]["title"] == "List JSON Title"
