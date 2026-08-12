"""Trust (``source``) tracking end-to-end through the memo commands.

koda-authored/edited entries are ``local``; an entry changed outside koda (or
pulled) is detected as ``remote`` and gates ``koda x``. The flag is local-only
and must never be written into the ``.md`` file.
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


class FakeStdin:
    def __init__(self, tty=True):
        self._tty = tty

    def isatty(self):
        return self._tty

    def read(self):
        return ""


def test_add_is_local(wired_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    memo._add_impl(text=["hello"], quiet=True)
    row = wired_db.get_latest_entry()
    assert row.source == "local"


def test_external_edit_becomes_remote(wired_db):
    """An entry rewritten outside koda flips to remote on the next reconcile."""
    row = put_entry("original", idx=0, uid="uid00010000")
    assert row.source == "local"
    path = runtime.get_entries_dir() / wired_db.path_for(row.uid)
    entry = md_store.read_entry(path)
    entry.content = "changed outside koda"
    md_store.write_entry(runtime.get_entries_dir(), entry, path=path)
    import os

    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))

    runtime.init_db()  # triggers reconcile_all
    assert wired_db.get_memo_by_uid("uid00010000").source == "remote"


def test_edit_reviews_remote_back_to_local(wired_db, monkeypatch):
    put_entry("remote body", idx=0, uid="rem00010000", source="remote")
    assert wired_db.get_memo_by_idx(0).source == "remote"

    def fake_editor(path):
        entry = md_store.read_entry(Path(path))
        entry.content = "reviewed"
        md_store.write_entry(runtime.get_entries_dir(), entry, path=Path(path))

    monkeypatch.setattr(memo, "launch_editor", fake_editor)
    memo.edit("0", quiet=True)
    assert wired_db.get_memo_by_idx(0).source == "local"


def test_source_never_written_to_md(wired_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", FakeStdin(tty=True))
    memo._add_impl(text=["body"], quiet=True)
    row = wired_db.get_latest_entry()
    path = runtime.get_entries_dir() / wired_db.path_for(row.uid)
    assert "source" not in path.read_text(encoding="utf-8")
