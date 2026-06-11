"""Tests for title CLI: add --title, edit footer, show display, query matching (#141)."""

import json

import pytest
import typer

import koda.runtime as runtime
from koda.commands import memo


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
    """Point the lazy DB cache at a fresh temp database."""
    monkeypatch.setattr(runtime, "_db", db)
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
    # Nothing saved.
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


# ── edit footer title line ────────────────────────────────────────────────────


def _seed(db, content="body", title=None, shortcut=None):
    db.add_memo(
        "uid0001",
        0,
        shortcut,
        content,
        "work",
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00",
        title=title,
    )
    return db.get_memo_by_idx(0)


def test_edit_footer_sets_title(wired_db, monkeypatch):
    _seed(wired_db)

    def fake_editor(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "body\n\n---\n# Metadata\ntitle: New Title\ntags: work\n"
                "shortcut: \ncreated_at: 2026-01-01 00:00:00\n---"
            )

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title == "New Title"


def test_edit_footer_clears_title_on_empty_value(wired_db, monkeypatch):
    _seed(wired_db, title="Old Title")

    def fake_editor(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "body\n\n---\n# Metadata\ntitle: \ntags: work\n"
                "shortcut: \ncreated_at: 2026-01-01 00:00:00\n---"
            )

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title is None


def test_edit_footer_changes_title(wired_db, monkeypatch):
    _seed(wired_db, title="Old Title")

    def fake_editor(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "body\n\n---\n# Metadata\ntitle: Updated Title\ntags: work\n"
                "shortcut: \ncreated_at: 2026-01-01 00:00:00\n---"
            )

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title == "Updated Title"


def test_edit_delete_footer_preserves_title(wired_db, monkeypatch):
    """Deleting the whole footer must leave the title unchanged."""
    _seed(wired_db, title="Keep Me")

    def fake_editor(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("body without footer")

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title == "Keep Me"


def test_edit_footer_without_title_key_preserves_title(wired_db, monkeypatch):
    """A hand-trimmed footer that omits the title: line must not wipe the title."""
    _seed(wired_db, title="Keep Me Too")

    def fake_editor(path):
        # Write a footer that has no title: line at all.
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "body\n\n---\n# Metadata\ntags: work\n"
                "shortcut: \ncreated_at: 2026-01-01 00:00:00\n---"
            )

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).title == "Keep Me Too"


# ── looks_like_koda_footer: title: first line ─────────────────────────────────


def test_looks_like_koda_footer_accepts_title_prefix():
    from koda.cmd_helpers.metadata import looks_like_koda_footer

    assert looks_like_koda_footer("title: My Title\ntags: work\n")


def test_looks_like_koda_footer_still_accepts_tags_prefix():
    from koda.cmd_helpers.metadata import looks_like_koda_footer

    assert looks_like_koda_footer("tags: work\nshortcut: foo\n")


# ── query matches title ──────────────────────────────────────────────────────


def _seed_two(db):
    """Insert two entries: one with a title hit, one with a body hit."""
    db.add_memo(
        "uid0001",
        0,
        None,
        "unrelated body",
        "work",
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00",
        title="Deploy prod",
    )
    db.add_memo(
        "uid0002",
        1,
        None,
        "docker compose up",
        "",
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00",
        title=None,
    )


def test_query_matches_title_only_hit(wired_db):
    _seed_two(wired_db)
    # "Deploy" only appears in the title of entry 0, not in its body.
    rows = wired_db.get_memos(query="Deploy")
    assert len(rows) == 1
    assert rows[0].uid == "uid0001"


def test_query_matches_body_hit(wired_db):
    _seed_two(wired_db)
    rows = wired_db.get_memos(query="docker")
    assert len(rows) == 1
    assert rows[0].uid == "uid0002"


def test_list_with_query_shows_title_hit(wired_db, monkeypatch, capsys):
    _seed_two(wired_db)
    memo._list_memos_impl(query="Deploy")
    out = capsys.readouterr().out
    # The body "unrelated body" should appear in the listed row.
    assert "unrelated body" in out


def test_remove_batch_query_matches_title(wired_db, monkeypatch, capsys):
    _seed_two(wired_db)
    # Force delete without prompt.
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


# ── show --json includes title ────────────────────────────────────────────────


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
