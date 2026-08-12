"""End-to-end smoke test for `koda push`/`koda pull` against a real local
git remote — the most fragile part of the vault-as-git-repo design (subprocess
calls, upstream detection, rebase-on-pull) had no coverage."""

import subprocess

import pytest

import koda.runtime as runtime
from koda import md_store
from koda.commands import git, memo


def _switch_vault(monkeypatch, vault_dir, config_path):
    """Point koda.runtime at ``vault_dir`` and reset its lazy singletons, so a
    single test can act as two different machines sharing one git remote."""
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


def test_push_then_pull_round_trip(tmp_path, bare_remote, monkeypatch):
    machine_a = tmp_path / "machine-a"
    machine_a.mkdir()
    _switch_vault(monkeypatch, machine_a, tmp_path / "none-a.toml")
    runtime.init_db()
    entry = memo._write_new_entry("echo from machine A", "", "from-a", None, None)

    subprocess.run(["git", "init", "-q", str(machine_a)], check=True)
    subprocess.run(
        ["git", "-C", str(machine_a), "remote", "add", "origin", str(bare_remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(machine_a), "config", "user.email", "a@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine_a), "config", "user.name", "Machine A"], check=True)

    git.push(dry_run=False)

    assert subprocess.run(
        ["git", "-C", str(bare_remote), "log", "--oneline"], capture_output=True, text=True
    ).stdout.strip()

    machine_b = tmp_path / "machine-b"
    machine_b.mkdir()
    subprocess.run(["git", "init", "-q", str(machine_b)], check=True)
    subprocess.run(
        ["git", "-C", str(machine_b), "remote", "add", "origin", str(bare_remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(machine_b), "config", "user.email", "b@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine_b), "config", "user.name", "Machine B"], check=True)
    # Machine B needs an upstream to rebase onto — mirror what a real first
    # `koda pull` would see after a manual `git fetch`/branch checkout.
    subprocess.run(["git", "-C", str(machine_b), "fetch", "-q", "origin"], check=True)
    default_branch = subprocess.run(
        ["git", "-C", str(machine_a), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(machine_b),
            "checkout",
            "-q",
            "-B",
            default_branch,
            f"origin/{default_branch}",
        ],
        check=True,
    )

    _switch_vault(monkeypatch, machine_b, tmp_path / "none-b.toml")
    runtime.init_db()
    git.pull()

    assert (machine_b / "entries").glob("*.md")
    row = runtime.get_db().get_memo_by_uid(entry.uid)
    assert row is not None
    assert row.content == "echo from machine A"
    assert row.shortcut == "from-a"
    # Pulled-in entries are untrusted until reviewed on this machine.
    assert row.source == "remote"


def test_pull_dry_run_previews_without_applying(tmp_path, bare_remote, monkeypatch, capsys):
    machine_a = tmp_path / "machine-a"
    machine_a.mkdir()
    _switch_vault(monkeypatch, machine_a, tmp_path / "none-a.toml")
    runtime.init_db()
    memo._write_new_entry("echo hi", "", None, None, None)
    subprocess.run(["git", "init", "-q", str(machine_a)], check=True)
    subprocess.run(
        ["git", "-C", str(machine_a), "remote", "add", "origin", str(bare_remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(machine_a), "config", "user.email", "a@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine_a), "config", "user.name", "Machine A"], check=True)
    git.push(dry_run=False)
    capsys.readouterr()  # discard machine A's push output

    machine_b = tmp_path / "machine-b"
    machine_b.mkdir()
    subprocess.run(["git", "init", "-q", str(machine_b)], check=True)
    subprocess.run(
        ["git", "-C", str(machine_b), "remote", "add", "origin", str(bare_remote)], check=True
    )

    _switch_vault(monkeypatch, machine_b, tmp_path / "none-b.toml")
    runtime.init_db()
    git.pull(dry_run=True)

    # No local branch tracks a remote yet on this fresh vault, so the dry-run
    # can only report that (rather than a diff) — nothing gets applied either way.
    assert not (machine_b / "entries").exists() or not list((machine_b / "entries").glob("*.md"))
    out = capsys.readouterr().out
    assert "No upstream branch" in out


def test_push_dry_run_lists_changes_without_writing(tmp_path, bare_remote, monkeypatch, capsys):
    machine = tmp_path / "machine"
    machine.mkdir()
    _switch_vault(monkeypatch, machine, tmp_path / "none.toml")
    runtime.init_db()
    entry1 = memo._write_new_entry("echo one", "", "one", None, None)
    entry3 = memo._write_new_entry("echo three", "", None, None, None)
    subprocess.run(["git", "init", "-q", str(machine)], check=True)
    subprocess.run(
        ["git", "-C", str(machine), "remote", "add", "origin", str(bare_remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(machine), "config", "user.email", "a@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine), "config", "user.name", "Machine"], check=True)
    git.push(dry_run=False)
    capsys.readouterr()  # discard the real push output
    log_before = subprocess.run(
        ["git", "-C", str(machine), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Local edits: modify entry1, add entry2, delete entry3.
    p1 = machine / "entries" / (runtime.get_db().path_for(entry1.uid) or "")
    entry = md_store.read_entry(p1)
    entry.content = "echo one edited"
    md_store.write_entry(machine / "entries", entry, path=p1)
    memo._write_new_entry("echo two", "", None, None, None)
    (machine / "entries" / (runtime.get_db().path_for(entry3.uid) or "")).unlink()

    git.push(dry_run=True)

    out = capsys.readouterr().out
    assert "updated" in out and "one" in out
    assert "new" in out and "two" in out
    assert "deleted" in out and "three" in out
    # Nothing was committed or pushed.
    assert subprocess.run(
        ["git", "-C", str(machine), "rev-list", "--count", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip() == log_before
    assert "Push complete." not in out


def test_push_dry_run_nothing_to_push(tmp_path, bare_remote, monkeypatch, capsys):
    machine = tmp_path / "machine"
    machine.mkdir()
    _switch_vault(monkeypatch, machine, tmp_path / "none.toml")
    runtime.init_db()
    memo._write_new_entry("echo one", "", None, None, None)
    subprocess.run(["git", "init", "-q", str(machine)], check=True)
    subprocess.run(
        ["git", "-C", str(machine), "remote", "add", "origin", str(bare_remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(machine), "config", "user.email", "a@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine), "config", "user.name", "Machine"], check=True)
    git.push(dry_run=False)
    capsys.readouterr()

    git.push(dry_run=True)

    out = capsys.readouterr().out
    assert "nothing to push" in out


def test_push_dry_run_warns_when_remote_ahead_without_rebasing(
    tmp_path, bare_remote, monkeypatch, capsys
):
    # Machine A pushes entry1.
    machine_a = tmp_path / "machine-a"
    machine_a.mkdir()
    _switch_vault(monkeypatch, machine_a, tmp_path / "none-a.toml")
    runtime.init_db()
    memo._write_new_entry("echo one", "", None, None, None)
    subprocess.run(["git", "init", "-q", str(machine_a)], check=True)
    subprocess.run(
        ["git", "-C", str(machine_a), "remote", "add", "origin", str(bare_remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(machine_a), "config", "user.email", "a@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine_a), "config", "user.name", "A"], check=True)
    git.push(dry_run=False)

    # Machine B clones the state (fetch + checkout, as in the existing test).
    machine_b = tmp_path / "machine-b"
    machine_b.mkdir()
    subprocess.run(["git", "init", "-q", str(machine_b)], check=True)
    subprocess.run(
        ["git", "-C", str(machine_b), "remote", "add", "origin", str(bare_remote)], check=True
    )
    subprocess.run(
        ["git", "-C", str(machine_b), "config", "user.email", "b@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine_b), "config", "user.name", "B"], check=True)
    subprocess.run(["git", "-C", str(machine_b), "fetch", "-q", "origin"], check=True)
    default_branch = subprocess.run(
        ["git", "-C", str(machine_a), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(machine_b), "checkout", "-q", "-B", default_branch, f"origin/{default_branch}"],
        check=True,
    )
    # Machine B never runs `koda push`, so _ensure_repo never wrote a
    # .gitignore — without this, .koda/cache.db shows up as untracked and
    # pollutes every status-based check. Mirror what _ensure_repo would write.
    (machine_b / ".gitignore").write_text(".koda/\n.obsidian/\n", encoding="utf-8")

    # Machine A pushes a second commit; machine B is now behind.
    _switch_vault(monkeypatch, machine_a, tmp_path / "none-a.toml")
    runtime.init_db()
    memo._write_new_entry("echo two", "", None, None, None)
    git.push(dry_run=False)

    # Machine B dry-runs its push: must warn, and must NOT rebase (read-only).
    _switch_vault(monkeypatch, machine_b, tmp_path / "none-b.toml")
    runtime.init_db()
    capsys.readouterr()
    git.push(dry_run=True)

    out = capsys.readouterr().out
    assert "run `koda pull` first" in out
    # The remote's new entry was NOT pulled into B's worktree.
    assert not list((machine_b / "entries").glob("two*.md"))


def test_push_dry_run_local_only_when_no_remote(tmp_path, monkeypatch, capsys):
    machine = tmp_path / "machine"
    machine.mkdir()
    _switch_vault(monkeypatch, machine, tmp_path / "none.toml")
    runtime.init_db()
    memo._write_new_entry("echo one", "", None, None, None)
    subprocess.run(["git", "init", "-q", str(machine)], check=True)
    subprocess.run(
        ["git", "-C", str(machine), "config", "user.email", "a@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(machine), "config", "user.name", "Machine"], check=True)
    # No `koda push` has run here either — write the same .gitignore _ensure_repo would.
    (machine / ".gitignore").write_text(".koda/\n.obsidian/\n", encoding="utf-8")

    git.push(dry_run=True)

    out = capsys.readouterr().out
    assert "new" in out and "one" in out
