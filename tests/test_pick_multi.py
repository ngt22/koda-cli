"""Tests for pick multi-select fzf integration (#72).

fzf is not available in CI, so subprocess.run and TTY/availability checks are
mocked; these tests cover ref parsing, the --multi flag, and KODA_FZF_OPTS.
"""

import subprocess

import pytest

from koda.cmd_helpers import interactive
from koda.models import MemoRow


def _row(idx, uid):
    return MemoRow(
        id=idx,
        uid=uid,
        idx=idx,
        content=f"body {idx}",
        tags="",
        shortcut=None,
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


@pytest.fixture
def fzf_env(monkeypatch):
    monkeypatch.setattr(interactive.shutil, "which", lambda _: "/usr/bin/fzf")
    monkeypatch.setattr(interactive.sys.stdin, "isatty", lambda: True)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=captured["stdout"])

    monkeypatch.setattr(interactive.subprocess, "run", fake_run)
    return captured


def test_single_pick_returns_first_ref(fzf_env):
    fzf_env["stdout"] = "3\tuid3\t-\t-\t2026\tbody 3\n"
    assert interactive.pick_with_fzf([_row(3, "uid3")]) == "3"
    assert "--multi" not in fzf_env["cmd"]


def test_multi_pick_returns_all_refs(fzf_env):
    fzf_env["stdout"] = "1\tuid1\t-\t-\t2026\tbody 1\n2\tuid2\t-\t-\t2026\tbody 2\n"
    refs = interactive.pick_with_fzf_multi([_row(1, "uid1"), _row(2, "uid2")])
    assert refs == ["1", "2"]
    assert "--multi" in fzf_env["cmd"]


def test_multi_no_selection_returns_empty(fzf_env):
    fzf_env["stdout"] = ""
    assert interactive.pick_with_fzf_multi([_row(1, "uid1")]) == []


def test_koda_fzf_opts_appended(fzf_env, monkeypatch):
    monkeypatch.setenv("KODA_FZF_OPTS", "--height 40% --reverse")
    fzf_env["stdout"] = "1\tuid1\t-\t-\t2026\tbody 1\n"
    interactive.pick_with_fzf([_row(1, "uid1")])
    assert "--height" in fzf_env["cmd"]
    assert "40%" in fzf_env["cmd"]
    assert "--reverse" in fzf_env["cmd"]
