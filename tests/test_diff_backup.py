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


"""--- #158: diff must not mutate the clone worktree ---"""
import subprocess


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _sync_pair(tmp_path):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote))
    clone = tmp_path / "clone"
    _git("clone", str(remote), str(clone))
    _git("-C", str(clone), "config", "user.name", "Test")
    _git("-C", str(clone), "config", "user.email", "test@example.com")
    return remote, clone


def _seed_payload(clone, payload_bytes):
    (clone / "koda-sync.jsonl").write_bytes(payload_bytes)
    _git("-C", str(clone), "add", "koda-sync.jsonl")
    _git("-C", str(clone), "commit", "-m", "seed")
    _git("-C", str(clone), "push", "-u", "origin", "HEAD")


def test_diff_readonly_does_not_pull_rebase(wired_db, tmp_path, monkeypatch, capsys):
    import koda.runtime as runtime
    from koda.config import Config
    from koda.git_sync import GitSyncRepo

    _, clone = _sync_pair(tmp_path)
    _seed_payload(clone, b"")  # remote に空 payload（＝差分なし想定）
    monkeypatch.setattr(runtime, "_config", Config(git_sync_path=str(clone)))
    monkeypatch.setattr(runtime, "_db", wired_db)

    # pull --rebase が呼ばれたら即失敗させる（読み取り専用の証明）
    def _boom(self):
        raise AssertionError("diff must not call pull_rebase_if_remote")

    monkeypatch.setattr(GitSyncRepo, "pull_rebase_if_remote", _boom)

    git_cmd.diff(local_payload_path=None)
    assert "in sync" in capsys.readouterr().out
