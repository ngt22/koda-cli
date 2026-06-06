"""Tests for the diff and backup commands (#74)."""

import pytest

import koda.runtime as runtime
from koda.commands import git as git_cmd
from koda.db import MemoDatabase


@pytest.fixture
def wired_db(db, monkeypatch):
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, idx, content, tags=""):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=content,
        tags=tags,
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def test_diff_reports_local_only_and_changed(wired_db, tmp_path, capsys):
    _seed(wired_db, 0, "alpha")
    _seed(wired_db, 1, "beta")
    remote = tmp_path / "remote.jsonl"
    git_cmd.export(out=remote)

    # Diverge: add a local-only entry and change an existing one.
    _seed(wired_db, 2, "gamma")
    with wired_db.connection() as conn:
        conn.execute(
            "UPDATE memos SET tags = ?, modified_at = ? WHERE idx = 1",
            ("edited", "2026-02-01 00:00:00"),
        )

    git_cmd.diff(local_payload_path=remote)
    out = capsys.readouterr().out
    assert "1 local-only" in out
    assert "1 changed" in out
    assert "uid0002" in out  # local-only gamma


def test_diff_in_sync(wired_db, tmp_path, capsys):
    _seed(wired_db, 0, "alpha")
    remote = tmp_path / "remote.jsonl"
    git_cmd.export(out=remote)
    git_cmd.diff(local_payload_path=remote)
    assert "No differences" in capsys.readouterr().out


def test_backup_creates_snapshot(wired_db, tmp_path, capsys):
    _seed(wired_db, 0, "alpha")
    _seed(wired_db, 1, "beta")
    dest = tmp_path / "snap.db"
    git_cmd.backup(out=dest)
    assert dest.is_file()
    assert "Backup written" in capsys.readouterr().out

    # The snapshot is a usable database with the same rows.
    snap = MemoDatabase(backend="local", path=dest)
    assert {r.content for r in snap.get_memos(limit=None)} == {"alpha", "beta"}


def test_backup_refuses_existing(wired_db, tmp_path):
    import typer

    dest = tmp_path / "exists.db"
    dest.write_text("x")
    with pytest.raises(typer.Exit):
        git_cmd.backup(out=dest)
