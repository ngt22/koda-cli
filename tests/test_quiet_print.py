"""--quiet / --print-uid / --print-idx on mutating commands (#71)."""

import pytest

import koda.runtime as runtime
from koda.commands import index, memo
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


def _seed(db, idx, tags=()):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=f"e{idx}",
        tags=TAG_SEPARATOR.join(tags),
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def test_add_print_uid_only(wired_db, capsys):
    memo._add_impl(text=["body"], quiet=True, print_uid=True)
    out = capsys.readouterr().out.strip()
    assert out == wired_db.get_latest_entry().uid
    assert "Saved" not in out


def test_add_print_idx_only(wired_db, capsys):
    memo._add_impl(text=["body"], quiet=True, print_idx=True)
    out = capsys.readouterr().out.strip()
    assert out == str(wired_db.get_latest_entry().idx)


def test_add_quiet_suppresses_message(wired_db, capsys):
    memo._add_impl(text=["body"], quiet=True)
    assert capsys.readouterr().out == ""
    assert wired_db.get_latest_entry().content == "body"


def test_add_default_prints_saved(wired_db, capsys):
    memo._add_impl(text=["body"])
    assert "Saved" in capsys.readouterr().out


def test_tag_quiet(wired_db, capsys):
    _seed(wired_db, 0)
    memo.tag(indices=["0"], tags=["x"], untag=None, dry_run=False, quiet=True)
    assert capsys.readouterr().out == ""
    assert wired_db.get_memo_by_idx(0).tags == "x"


def test_move_quiet(wired_db, capsys):
    _seed(wired_db, 0)
    index.move(from_idx=0, to_idx=5, dry_run=False, quiet=True)
    assert capsys.readouterr().out == ""
    assert wired_db.get_memo_by_idx(5) is not None


def test_swap_quiet(wired_db, capsys):
    _seed(wired_db, 0)
    _seed(wired_db, 1)
    index.swap(idx1=0, idx2=1, quiet=True)
    assert capsys.readouterr().out == ""
