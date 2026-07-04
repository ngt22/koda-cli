"""Tests for --json output on list / show / config (#69)."""

import json

import pytest
from _helpers import put_entry

from koda.commands import config as config_cmd
from koda.commands import memo
from koda.constants import TAG_SEPARATOR


@pytest.fixture
def wired_db(db):
    return db


def _seed(db, idx, content, tags=()):
    put_entry(content, idx=idx, tags=TAG_SEPARATOR.join(tags), uid=f"uid{idx:04d}00000000")


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


def test_config_show_subcommand_matches_callback(wired_db, capsys):
    """`config show` (explicit subcommand) produces the same JSON as bare `config`."""
    config_cmd.config_show_cmd(json_output=True)
    via_subcommand = capsys.readouterr().out

    ctx = type("Ctx", (), {"invoked_subcommand": None})()
    config_cmd.config_show(ctx, json_output=True)
    via_callback = capsys.readouterr().out

    assert via_subcommand == via_callback
    assert json.loads(via_subcommand)["defaults"]["cmd"] == "raw"


def test_config_json_includes_vault_path(wired_db, capsys):
    ctx = type("Ctx", (), {"invoked_subcommand": None})()
    config_cmd.config_show(ctx, json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert "path" in data["vault"]
