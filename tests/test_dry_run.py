"""--dry-run on compact / shift / move / tag must not touch the DB (#75)."""

import pytest

import koda.runtime as runtime
from koda.commands import index, memo
from koda.constants import TAG_SEPARATOR


@pytest.fixture
def wired_db(db, monkeypatch):
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, idx, content="x", tags=()):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=content,
        tags=TAG_SEPARATOR.join(tags),
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def _idxs(db):
    return sorted(r.idx for r in db.get_memos(limit=None))


def test_move_dry_run_does_not_change(wired_db, capsys):
    _seed(wired_db, 0)
    index.move(from_idx=0, to_idx=5, dry_run=True)
    assert _idxs(wired_db) == [0]
    assert "Would move" in capsys.readouterr().out


def test_shift_dry_run_does_not_change(wired_db, capsys):
    _seed(wired_db, 0)
    _seed(wired_db, 1)
    index.shift_cmd(start=0, count=3, dry_run=True)
    assert _idxs(wired_db) == [0, 1]
    assert "Would shift 2 entries" in capsys.readouterr().out


def test_compact_dry_run_does_not_change(wired_db, capsys):
    _seed(wired_db, 0)
    _seed(wired_db, 5)
    index.compact_indices(dry_run=True)
    assert _idxs(wired_db) == [0, 5]
    assert "Would compact" in capsys.readouterr().out


def test_tag_dry_run_does_not_change(wired_db, capsys):
    _seed(wired_db, 0, tags=["keep"])
    memo.tag(indices=["0"], tags=["new"], untag=None, dry_run=True)
    row = wired_db.get_memo_by_idx(0)
    assert row.tags == "keep"
    assert "Would update 1 entry (added 1 tag, removed 0 tags)" in capsys.readouterr().out
