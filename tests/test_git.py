"""End-to-end smoke test for `koda push`/`koda pull` against a real local
git remote — the most fragile part of the vault-as-git-repo design (subprocess
calls, upstream detection, rebase-on-pull) had no coverage."""

import subprocess

import pytest

import koda.runtime as runtime
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

    git.push()

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
    git.push()
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
