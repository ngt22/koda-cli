"""Tests for the export / import commands (#70)."""

import json

import pytest

import koda.runtime as runtime
from koda.commands import git as git_cmd
from koda.db import MemoDatabase


@pytest.fixture
def wired_db(db, monkeypatch):
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, idx, content):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=content,
        tags="",
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def test_export_stdout_is_jsonl(wired_db, capsysbinary):
    """capsysbinary is a pytest built-in capturing the raw stdout buffer."""
    _seed(wired_db, 0, "alpha")
    _seed(wired_db, 1, "beta")
    git_cmd.export(out=None)
    out = capsysbinary.readouterr().out.decode()
    lines = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert {r["content"] for r in lines} == {"alpha", "beta"}


def test_export_to_file(wired_db, tmp_path):
    _seed(wired_db, 0, "alpha")
    dest = tmp_path / "dump.jsonl"
    git_cmd.export(out=dest)
    assert dest.is_file()
    assert "alpha" in dest.read_text()


def test_import_roundtrip(wired_db, tmp_path, monkeypatch, capsys):
    _seed(wired_db, 0, "alpha")
    _seed(wired_db, 1, "beta")
    dump = tmp_path / "dump.jsonl"
    git_cmd.export(out=dump)

    # Fresh DB, import the dump.
    target = MemoDatabase(backend="local", path=tmp_path / "target.db")
    target.init_db()
    monkeypatch.setattr(runtime, "_db", target)
    git_cmd.import_memos(file=dump)

    contents = {r.content for r in target.get_memos(limit=None)}
    assert contents == {"alpha", "beta"}
    assert "Import complete" in capsys.readouterr().out


def test_import_missing_file_errors(wired_db, tmp_path):
    import typer

    with pytest.raises(typer.Exit):
        git_cmd.import_memos(file=tmp_path / "nope.jsonl")
