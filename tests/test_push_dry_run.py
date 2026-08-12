"""koda push --dry-run (#158): preview only, never writes."""

import json
import subprocess

import pytest

import koda.runtime as runtime
from koda.commands import git as git_cmd
from koda.config import Config
from koda.git_sync import GitSyncRepo


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _line(uid, idx, content, modified="2026-01-01 00:00:00"):
    return json.dumps(
        {
            "uid": uid,
            "idx": idx,
            "shortcut": None,
            "content": content,
            "tags": "",
            "created_at": "2026-01-01 00:00:00",
            "modified_at": modified,
            "title": None,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(runtime, "_db", db)
    return db


@pytest.fixture
def sync_env(wired, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote))
    clone = tmp_path / "clone"
    _git("clone", str(remote), str(clone))
    _git("-C", str(clone), "config", "user.name", "Test")
    _git("-C", str(clone), "config", "user.email", "test@example.com")
    monkeypatch.setattr(runtime, "_config", Config(git_sync_path=str(clone)))
    return clone


def _seed_remote(clone, lines):
    (clone / "koda-sync.jsonl").write_bytes(("\n".join(lines) + "\n").encode())
    _git("-C", str(clone), "add", "koda-sync.jsonl")
    _git("-C", str(clone), "commit", "-m", "seed")
    _git("-C", str(clone), "push", "-u", "origin", "HEAD")


def test_push_dry_run_lists_new_update_delete(sync_env, capsys):
    # remote: uidA(同値) / uidB(更新される) / uidC(削除される)
    _seed_remote(
        sync_env,
        [
            _line("uidA", 0, "alpha"),
            _line("uidB", 1, "beta-old"),
            _line("uidC", 2, "charlie"),
        ],
    )
    # local: uidA(同値) / uidB(変更) / uidD(新規)
    runtime.get_db().add_memo(
        "uidA", 0, None, "alpha", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    runtime.get_db().add_memo(
        "uidB", 1, None, "beta-new", "", "2026-02-01 00:00:00", "2026-02-01 00:00:00"
    )
    runtime.get_db().add_memo(
        "uidD", 3, None, "delta", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )

    git_cmd.push(payload_file=None, dry_run=True)
    out = capsys.readouterr().out
    assert "+ new" in out and "uidD" in out
    assert "~ update" in out and "uidB" in out
    assert "- delete" in out and "uidC" in out
    assert "1 new, 1 update, 1 delete" in out
    assert "dry run" in out


def test_push_dry_run_nothing_to_push(sync_env, capsys):
    _seed_remote(sync_env, [_line("uidA", 0, "alpha")])
    runtime.get_db().add_memo(
        "uidA", 0, None, "alpha", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )

    git_cmd.push(payload_file=None, dry_run=True)
    assert "Nothing to push" in capsys.readouterr().out


def test_push_dry_run_writes_nothing(sync_env, capsys):
    _seed_remote(sync_env, [_line("uidA", 0, "alpha")])
    runtime.get_db().add_memo(
        "uidB", 0, None, "beta", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    payload_before = (sync_env / "koda-sync.jsonl").read_bytes()
    head_before = subprocess.run(
        ["git", "-C", str(sync_env), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout

    git_cmd.push(payload_file=None, dry_run=True)
    assert (sync_env / "koda-sync.jsonl").read_bytes() == payload_before  # ファイル不変
    head_after = subprocess.run(
        ["git", "-C", str(sync_env), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout
    assert head_after == head_before  # コミットなし
    assert (
        "koda: sync memo payload"
        not in subprocess.run(
            ["git", "-C", str(sync_env), "log", "--oneline"],
            capture_output=True,
            text=True,
        ).stdout
    )


def test_push_dry_run_never_pulls_or_pushes(sync_env, monkeypatch, capsys):
    def _boom(self):
        raise AssertionError("dry-run must not pull --rebase")

    monkeypatch.setattr(GitSyncRepo, "pull_rebase_if_remote", _boom)

    _seed_remote(sync_env, [_line("uidA", 0, "alpha")])
    runtime.get_db().add_memo(
        "uidB", 0, None, "beta", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    git_cmd.push(
        payload_file=None, dry_run=True
    )  # ここが通れば pull_rebase_if_remote は呼ばれていない
    assert "1 new" in capsys.readouterr().out
