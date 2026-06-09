"""Display-index ops: swap temp-sentinel safety, move/swap edge cases."""

import pytest

import koda.runtime as runtime
from koda.commands import index
from koda.constants import TAG_SEPARATOR


class FakeStdin:
    def isatty(self):
        return True

    def read(self):
        return ""


@pytest.fixture
def wired_db(db, monkeypatch):
    monkeypatch.setattr(runtime, "_db", db)
    monkeypatch.setattr("sys.stdin", FakeStdin())
    return db


def _seed(db, idx, content=None):
    db.add_memo(
        uid=f"uid{idx:+05d}",
        idx=idx,
        shortcut=None,
        content=content or f"e{idx}",
        tags=TAG_SEPARATOR.join(()),
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def test_swap_two_entries(wired_db):
    _seed(wired_db, 0, "a")
    _seed(wired_db, 1, "b")
    index.swap(idx1=0, idx2=1, quiet=True)
    assert wired_db.get_memo_by_idx(0).content == "b"
    assert wired_db.get_memo_by_idx(1).content == "a"


def test_swap_with_negative_idx_does_not_crash(wired_db):
    """An entry can land at idx -1 via `koda move 0 -1`; swapping it must not
    collide with a hardcoded -1 temp sentinel (regression)."""
    _seed(wired_db, -1, "neg")
    _seed(wired_db, 5, "five")
    index.swap(idx1=-1, idx2=5, quiet=True)
    assert wired_db.get_memo_by_idx(-1).content == "five"
    assert wired_db.get_memo_by_idx(5).content == "neg"


def test_swap_missing_first_index_errors(wired_db):
    _seed(wired_db, 0)
    import typer

    with pytest.raises(typer.Exit):
        index.swap(idx1=9, idx2=0, quiet=True)
