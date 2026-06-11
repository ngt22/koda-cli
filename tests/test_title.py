"""Title column threading: edit/copy must never wipe an existing title."""

import pytest

import koda.runtime as runtime
from koda.commands import memo


@pytest.fixture
def wired_db(db, monkeypatch):
    """Point the lazy DB cache at a fresh temp database."""
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed_titled(db, title="Deploy prod"):
    """Insert one entry that carries a title and return its row."""
    db.add_memo(
        "uid0001",
        0,
        "dp",
        "deploy body",
        "work",
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00",
        title=title,
    )
    return db.get_memo_by_idx(0)


def test_copy_preserves_source_title(wired_db):
    _seed_titled(wired_db)
    memo.copy("0")
    copied = wired_db.get_memo_by_idx(1)
    assert copied is not None
    assert copied.title == "Deploy prod"


def test_edit_with_footer_preserves_title(wired_db, monkeypatch):
    """The footer has no title field yet, so a footer edit must pass the
    existing title through unchanged."""
    _seed_titled(wired_db)

    def fake_editor(path):
        # Rewrite body but keep the metadata footer intact.
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "new body\n\n---\n# Metadata\ntags: work\n"
                "shortcut: dp\ncreated_at: 2026-01-01 00:00:00\n---"
            )

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)

    row = wired_db.get_memo_by_idx(0)
    assert row.content == "new body"
    assert row.title == "Deploy prod"


def test_edit_without_footer_preserves_title(wired_db, monkeypatch):
    _seed_titled(wired_db)

    def fake_editor(path):
        # Drop the footer entirely: content-only update branch.
        with open(path, "w", encoding="utf-8") as f:
            f.write("body without footer")

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)

    row = wired_db.get_memo_by_idx(0)
    assert row.content == "body without footer"
    assert row.title == "Deploy prod"
