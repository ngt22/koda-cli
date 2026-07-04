"""Title threading through copy/edit under the Markdown-native store.

``edit`` now opens the entry's ``.md`` directly (frontmatter + body), so title
lives in the file the user edits. copy must carry the source title to the new
entry; an edit that leaves the frontmatter intact must keep the title.
"""

from pathlib import Path

import pytest
from _helpers import put_entry

import koda.runtime as runtime
from koda import md_store
from koda.commands import memo


@pytest.fixture
def wired_db(db):
    return db


def _seed_titled(db, title="Deploy prod"):
    return put_entry(
        "deploy body", idx=0, shortcut="dp", tags="work", title=title, uid="uid00010000"
    )


def test_copy_preserves_source_title(wired_db):
    _seed_titled(wired_db)
    memo.copy("0")
    copied = wired_db.get_memo_by_idx(1)
    assert copied is not None
    assert copied.title == "Deploy prod"


def test_edit_body_only_preserves_title(wired_db, monkeypatch):
    """An editor that rewrites only the body (keeping frontmatter) keeps title."""
    _seed_titled(wired_db)

    def fake_editor(path):
        entry = md_store.read_entry(Path(path))
        entry.content = "new body"
        md_store.write_entry(runtime.get_entries_dir(), entry, path=Path(path))

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)

    row = wired_db.get_memo_by_idx(0)
    assert row.content == "new body"
    assert row.title == "Deploy prod"


def test_edit_can_change_title_in_frontmatter(wired_db, monkeypatch):
    _seed_titled(wired_db)

    def fake_editor(path):
        entry = md_store.read_entry(Path(path))
        entry.title = "Renamed"
        md_store.write_entry(runtime.get_entries_dir(), entry, path=Path(path))

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)

    assert wired_db.get_memo_by_idx(0).title == "Renamed"


def test_edit_wiping_frontmatter_drops_title(wired_db, monkeypatch):
    """You edit the real file: a body-only rewrite with no frontmatter removes
    the title (uid/idx are still restored so the entry survives)."""
    _seed_titled(wired_db)

    def fake_editor(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("body without frontmatter")

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)

    row = wired_db.get_memo_by_idx(0)
    assert row.content == "body without frontmatter"
    assert row.title is None
