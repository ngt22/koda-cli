"""Tests for the `tag` command completion message (#55).

The old message reported "Added to N entries; removed from N entries" where N
was a single loop counter, so add and remove counts were always identical even
when a tag was only added (e.g. removing a tag the entry never had). The fixed
message reports actual per-tag add/remove totals.
"""

import pytest

import koda.runtime as runtime
from koda.commands import memo
from koda.constants import TAG_SEPARATOR


@pytest.fixture
def wired_db(db, monkeypatch):
    """Point the lazy DB cache at a fresh temp database."""
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, idx, tags):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=f"entry {idx}",
        tags=TAG_SEPARATOR.join(tags),
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def _tags(db, idx):
    row = db.get_memo_by_idx(idx)
    return [t for t in (row.tags or "").split(TAG_SEPARATOR) if t.strip()]


def test_add_and_remove_counts_differ(wired_db, capsys):
    """Only some entries actually have the tag being removed: counts must differ."""
    _seed(wired_db, 1, ["bar"])
    _seed(wired_db, 2, [])
    _seed(wired_db, 3, ["bar"])

    memo.tag(indices=["1", "2", "3"], tags=["foo"], untag=["bar"])

    out = capsys.readouterr().out
    # foo added to all 3 entries; bar removed only from entries 1 and 3.
    assert "Updated 3 entries (added 3 tags, removed 2 tags)." in out
    assert _tags(wired_db, 1) == ["foo"]
    assert _tags(wired_db, 2) == ["foo"]
    assert _tags(wired_db, 3) == ["foo"]


def test_singular_wording(wired_db, capsys):
    _seed(wired_db, 1, [])
    memo.tag(indices=["1"], tags=["solo"], untag=None)
    out = capsys.readouterr().out
    assert "Updated 1 entry (added 1 tag, removed 0 tags)." in out


def test_noop_when_nothing_changes(wired_db, capsys):
    """Adding a tag the entry already has, or removing one it lacks, is a no-op."""
    _seed(wired_db, 1, ["keep"])
    memo.tag(indices=["1"], tags=["keep"], untag=["absent"])
    out = capsys.readouterr().out
    assert "Updated 0 entries (added 0 tags, removed 0 tags)." in out
    assert _tags(wired_db, 1) == ["keep"]
