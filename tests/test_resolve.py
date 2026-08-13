"""Tests for idx-conflict detection and `koda resolve`.

Covers the reconcile-level helpers (``snapshot_idx`` / ``compute_sync_diff`` /
``find_conflicts`` / ``annotate_groups``), the ``resolve`` command's
``--ours``/``--theirs`` behavior, and an end-to-end two-machine convergence
test (concurrent adds → conflict → resolve → push → pull).
"""

import subprocess

import pytest
import typer

import koda.runtime as runtime
from koda import conflicts as conflicts_mod
from koda.commands import git, memo
from koda.commands import resolve as resolve_cmd
from koda.reconcile import (
    annotate_groups,
    compute_sync_diff,
    find_conflicts,
    snapshot_idx,
)

# ── reconcile helpers ────────────────────────────────────────────────────────


def test_snapshot_idx_returns_uid_to_idx_map(db, seed):
    a = seed("alpha")
    b = seed("beta")
    assert snapshot_idx(db) == {a.uid: a.idx, b.uid: b.idx}


def test_compute_sync_diff_classifies_added_removed_moved():
    diff = compute_sync_diff(
        {"a": 0, "b": 1, "c": 2},
        {"a": 0, "c": 5, "d": 6},
    )
    assert diff["added"] == ["d"]
    assert diff["removed"] == ["b"]
    assert diff["moved"] == [("c", 2, 5)]


def test_find_conflicts_detects_duplicate_idx(db, write_md):
    write_md("first", idx=3, uid="first-uid-000001")
    write_md("second", idx=3, uid="second-uid-00001")
    write_md("solo", idx=7, uid="solo-uid-0000001")

    groups = find_conflicts(db)
    assert len(groups) == 1
    assert groups[0]["idx"] == 3
    uids = {e["uid"] for e in groups[0]["entries"]}
    assert uids == {"first-uid-000001", "second-uid-00001"}


def test_find_conflicts_empty_when_unique(db, seed):
    seed("alpha")
    seed("beta")
    assert find_conflicts(db) == []


def test_annotate_groups_marks_ours_vs_theirs():
    groups = [
        {
            "idx": 2,
            "entries": [
                {"uid": "ours-uid", "idx": 2, "side": None, "prev_idx": None},
                {"uid": "moved-in", "idx": 2, "side": None, "prev_idx": None},
                {"uid": "new-in", "idx": 2, "side": None, "prev_idx": None},
            ],
        }
    ]
    # Before the pull: ours held idx 2, moved-in held idx 9, new-in didn't exist.
    annotate_groups(groups, {"ours-uid": 2, "moved-in": 9})
    by_uid = {e["uid"]: e for e in groups[0]["entries"]}
    assert by_uid["ours-uid"]["side"] == "ours"
    assert by_uid["ours-uid"]["prev_idx"] == 2
    assert by_uid["moved-in"]["side"] == "theirs"
    assert by_uid["moved-in"]["prev_idx"] == 9
    assert by_uid["new-in"]["side"] == "theirs"
    assert by_uid["new-in"]["prev_idx"] is None


# ── resolve command ──────────────────────────────────────────────────────────


def _saved_groups():
    return [
        {
            "idx": 2,
            "entries": [
                {
                    "uid": "local-uid-000001",
                    "path": "local.md",
                    "idx": 2,
                    "shortcut": "",
                    "tags": "",
                    "title": "",
                    "content": "local entry",
                    "created_at": "2026-01-01 00:00:00",
                    "side": "ours",
                    "prev_idx": 2,
                },
                {
                    "uid": "remote-uid-0001",
                    "path": "remote.md",
                    "idx": 2,
                    "shortcut": "",
                    "tags": "",
                    "title": "",
                    "content": "remote entry",
                    "created_at": "2026-01-01 00:00:00",
                    "side": "theirs",
                    "prev_idx": None,
                },
            ],
        }
    ]


def test_resolve_ours_keeps_local_moves_theirs(vault, db, write_md):
    write_md("local entry", idx=2, uid="local-uid-000001")
    write_md("remote entry", idx=2, uid="remote-uid-0001", source="remote")
    conflicts_mod.save_conflicts(vault, _saved_groups())

    resolve_cmd.resolve(ours=True, theirs=False)

    assert db.get_memo_by_uid("local-uid-000001").idx == 2
    assert db.get_memo_by_uid("remote-uid-0001").idx == 3
    assert conflicts_mod.load_conflicts(vault) == []


def test_resolve_theirs_keeps_remote_moves_ours(vault, db, write_md):
    write_md("local entry", idx=2, uid="local-uid-000001")
    write_md("remote entry", idx=2, uid="remote-uid-0001", source="remote")
    conflicts_mod.save_conflicts(vault, _saved_groups())

    resolve_cmd.resolve(ours=False, theirs=True)

    assert db.get_memo_by_uid("remote-uid-0001").idx == 2
    assert db.get_memo_by_uid("local-uid-000001").idx == 3
    assert conflicts_mod.load_conflicts(vault) == []


def test_resolve_rejects_both_flags(vault, db, write_md):
    write_md("local entry", idx=2, uid="local-uid-000001")
    write_md("remote entry", idx=2, uid="remote-uid-0001", source="remote")
    conflicts_mod.save_conflicts(vault, _saved_groups())

    with pytest.raises(typer.Exit) as e:
        resolve_cmd.resolve(ours=True, theirs=True)
    assert e.value.exit_code == 1


def test_resolve_no_conflicts_is_noop(vault, db, seed, capsys):
    seed("just one")
    resolve_cmd.resolve(ours=True, theirs=False)
    out = capsys.readouterr().out
    assert "No conflicts to resolve" in out


# ── end-to-end two-machine convergence ───────────────────────────────────────


def _switch_vault(monkeypatch, vault_dir, config_path):
    monkeypatch.setenv("KODA_VAULT_PATH", str(vault_dir))
    monkeypatch.setenv("KODA_DB_PATH_OVERRIDE", "1")
    monkeypatch.setenv("KODA_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(runtime, "_config", None)
    monkeypatch.setattr(runtime, "_config_sources", None)
    monkeypatch.setattr(runtime, "_config_manager", None)
    monkeypatch.setattr(runtime, "_db", None)


@pytest.fixture
def bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    return remote


def _configure_git(vault, remote, email, name):
    subprocess.run(["git", "init", "-q", str(vault)], check=True)
    subprocess.run(["git", "-C", str(vault), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.email", email], check=True)
    subprocess.run(["git", "-C", str(vault), "config", "user.name", name], check=True)


def _checkout_from_remote(vault, remote, ref_machine):
    subprocess.run(["git", "-C", str(vault), "fetch", "-q", "origin"], check=True)
    branch = subprocess.run(
        ["git", "-C", str(ref_machine), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(vault), "checkout", "-q", "-B", branch, f"origin/{branch}"],
        check=True,
    )


def test_concurrent_add_conflict_resolve_then_converge(tmp_path, bare_remote, monkeypatch, capsys):
    machine_a = tmp_path / "machine-a"
    machine_a.mkdir()
    _switch_vault(monkeypatch, machine_a, tmp_path / "a.toml")
    runtime.init_db()
    memo._write_new_entry("entry one", "", None, None, None)
    memo._write_new_entry("entry two", "", None, None, None)
    _configure_git(machine_a, bare_remote, "a@example.com", "A")
    git.push(dry_run=False)

    machine_b = tmp_path / "machine-b"
    machine_b.mkdir()
    _configure_git(machine_b, bare_remote, "b@example.com", "B")
    _checkout_from_remote(machine_b, bare_remote, machine_a)

    # A adds X (idx 2) and pushes.
    _switch_vault(monkeypatch, machine_a, tmp_path / "a.toml")
    runtime.init_db()
    x = memo._write_new_entry("X from A", "", None, None, None)
    git.push(dry_run=False)

    # B adds Y (idx 2) without pulling.
    _switch_vault(monkeypatch, machine_b, tmp_path / "b.toml")
    runtime.init_db()
    y = memo._write_new_entry("Y from B", "", None, None, None)

    # B push → blocked by conflict (exit code 5).
    with pytest.raises(typer.Exit) as e:
        git.push(dry_run=False)
    assert e.value.exit_code == 5
    capsys.readouterr()

    groups = conflicts_mod.load_conflicts(machine_b)
    assert len(groups) == 1 and groups[0]["idx"] == 2
    sides = {entry["uid"]: entry["side"] for entry in groups[0]["entries"]}
    assert sides[y.uid] == "ours"
    assert sides[x.uid] == "theirs"

    # B resolves --ours, keeping Y@2 and moving X@3, then pushes.
    resolve_cmd.resolve(ours=True, theirs=False)
    assert runtime.get_db().get_memo_by_uid(y.uid).idx == 2
    assert runtime.get_db().get_memo_by_uid(x.uid).idx == 3
    git.push(dry_run=False)

    # A pulls and converges to the same idx mapping.
    _switch_vault(monkeypatch, machine_a, tmp_path / "a.toml")
    runtime.init_db()
    git.pull(dry_run=False)
    assert runtime.get_db().get_memo_by_uid(x.uid).idx == 3
    assert runtime.get_db().get_memo_by_uid(y.uid).idx == 2
