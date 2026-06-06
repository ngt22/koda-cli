"""`koda list <idx>` should dispatch to `show` (#73)."""

import pytest

import koda.runtime as runtime
from koda.commands import memo
from koda.constants import TAG_SEPARATOR


@pytest.fixture
def wired_db(db, monkeypatch):
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, idx, content, tags=()):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=content,
        tags=TAG_SEPARATOR.join(tags),
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def test_list_with_idx_shows_single_entry(wired_db, capsys, monkeypatch):
    _seed(wired_db, 0, "alpha")
    _seed(wired_db, 1, "beta")
    called = {}

    def fake_show(ref, json_output=False):
        called["ref"] = ref
        called["json"] = json_output

    monkeypatch.setattr(memo, "show", fake_show)
    memo.list_memos(ref="1", json_output=False)
    assert called == {"ref": "1", "json": False}


def test_list_with_idx_forwards_json(wired_db, monkeypatch):
    called = {}
    monkeypatch.setattr(
        memo, "show", lambda ref, json_output=False: called.update(ref=ref, json=json_output)
    )
    memo.list_memos(ref="0", json_output=True)
    assert called == {"ref": "0", "json": True}
