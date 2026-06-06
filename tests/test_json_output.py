"""Tests for --json output on list / show / config (#69)."""

import json

import pytest

import koda.runtime as runtime
from koda.commands import config as config_cmd
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


def test_list_json_is_array_and_parses(wired_db, capsys):
    _seed(wired_db, 0, "alpha", ["work", "home"])
    _seed(wired_db, 1, "beta")
    memo._emit_list_json(None, None, None, False, None, None)
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert [d["content"] for d in data] == ["alpha", "beta"]
    assert data[0]["tags"] == ["work", "home"]  # tags split into a list
    assert data[1]["tags"] == []


def test_list_json_ignores_paging(wired_db, capsys):
    """The JSON path returns every match regardless of page size."""
    for i in range(25):
        _seed(wired_db, i, f"entry {i}")
    memo._emit_list_json(None, None, None, False, None, None)
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 25


def test_show_json_is_object(wired_db, capsys):
    _seed(wired_db, 3, "gamma", ["x"])
    memo.show(ref="3", json_output=True)
    obj = json.loads(capsys.readouterr().out)
    assert isinstance(obj, dict)
    assert obj["idx"] == 3
    assert obj["content"] == "gamma"
    assert obj["tags"] == ["x"]


def test_config_json_is_hierarchical(wired_db, capsys):
    ctx = type("Ctx", (), {"invoked_subcommand": None})()
    config_cmd.config_show(ctx, json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data["list"], dict)
    assert data["defaults"]["cmd"] == "raw"
    assert "per_page" in data["list"]


def test_config_json_masks_token(wired_db, capsys, monkeypatch):
    cfg = runtime.get_config()
    monkeypatch.setattr(cfg, "turso_token", "super-secret-token")
    ctx = type("Ctx", (), {"invoked_subcommand": None})()
    config_cmd.config_show(ctx, json_output=True)
    out = capsys.readouterr().out
    assert "super-secret-token" not in out
    assert json.loads(out)["turso"]["token"] == "****"
