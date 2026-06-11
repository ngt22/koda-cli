"""Group (reference-list) exec entries: `koda x <group>` expands `@ref` lines
and runs each child sequentially (issue #143).

Like ``test_exec.py`` these run the CLI as a real subprocess, since exec ends in
``os.execvp`` / ``subprocess.run`` and replaces or spawns processes. Children
write to files under tmp so order and execution are observable.
"""

import subprocess
import sys

from koda.db import MemoDatabase

ENV_KEYS = ("PATH", "HOME")


def _base_env(tmp_path, db_path):
    import os

    env = {k: os.environ[k] for k in ENV_KEYS if k in os.environ}
    env["KODA_DB_PATH"] = str(db_path)
    env["KODA_DB_PATH_OVERRIDE"] = "1"
    env["KODA_CONFIG_PATH"] = str(tmp_path / "nonexistent.toml")
    return env


def _seed(db_path, entries):
    """entries: list of (idx, shortcut, content). uid is derived from idx."""
    seed = MemoDatabase(backend="local", path=db_path)
    seed.init_db()
    for idx, shortcut, content in entries:
        seed.add_memo(
            uid=f"uid{idx:05d}",
            idx=idx,
            shortcut=shortcut,
            content=content,
            tags="",
            created_at="2026-01-01 00:00:00",
            modified_at="2026-01-01 00:00:00",
        )
    return seed


def _mark_remote(db_path, *uids):
    seed = MemoDatabase(backend="local", path=db_path)
    with seed.connection() as conn:
        for uid in uids:
            conn.execute("UPDATE memos SET source = 'remote' WHERE uid = ?", (uid,))


def _run(tmp_path, db_path, *args, input=None):
    return subprocess.run(
        [sys.executable, "-c", "from koda.main import app; app()", "x", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL if input is None else None,
        input=input,
        env=_base_env(tmp_path, db_path),
    )


# --- Detection ---


def test_all_ref_lines_is_a_group(tmp_path):
    """Every line starting with @ → group; children run in order."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (10, "grp", "@1\n@2"),
        ],
    )
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode == 0, result.stderr
    assert out.read_text().split() == ["A", "B"]


def test_plain_body_is_unchanged(tmp_path):
    """A body with no @ lines runs as a normal single entry (execvp path)."""
    db_path = tmp_path / "g.db"
    _seed(db_path, [(1, "p", "echo PLAIN_OK")])
    result = _run(tmp_path, db_path, "1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PLAIN_OK"


def test_mixed_body_errors(tmp_path):
    """Some-but-not-all @ lines is an error; nothing runs."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(db_path, [(1, "a", f"echo A >> {out}"), (10, "grp", "@1\necho plain")])
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode != 0
    assert "Mixed body" in result.stderr
    assert not out.exists()


def test_comment_and_blank_lines_ignored(tmp_path):
    """Inline comments and blank lines don't break group detection."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (10, "grp", "# header\n@1\n\n@2  # run b\n"),
        ],
    )
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode == 0, result.stderr
    assert out.read_text().split() == ["A", "B"]


# --- Fail fast ---


def test_unknown_ref_aborts_before_running_anything(tmp_path):
    """An unknown ref on line 3 aborts expansion; no child runs (fail fast)."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (10, "grp", "@1\n@2\n@nope"),
        ],
    )
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode != 0
    assert "nope" in result.stderr
    assert not out.exists()


# --- Order + stop on failure ---


def test_stop_on_first_failure_with_exit_code(tmp_path):
    """First non-zero exit stops the run and propagates that code; later
    children do not run."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}; exit 7"),
            (3, "c", f"echo C >> {out}"),
            (10, "grp", "@1\n@2\n@3"),
        ],
    )
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode == 7
    assert out.read_text().split() == ["A", "B"]  # C never ran


# --- Per-line args (both branches of #138 semantics) ---


def test_per_line_args_append_when_no_positional(tmp_path):
    """A child body with no positional ref gets the line's args appended."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo >> {out}"),
            (10, "grp", "@1 hello world"),
        ],
    )
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode == 0, result.stderr
    assert out.read_text().strip() == "hello world"


def test_per_line_args_fill_positionals(tmp_path):
    """A child body using $1 has the line's args fill it (no append)."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo logs-$1 >> {out}"),
            (10, "grp", "@1 mypod"),
        ],
    )
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode == 0, result.stderr
    assert out.read_text().strip() == "logs-mypod"


# --- Nesting + cycles + depth ---


def test_nested_group_runs(tmp_path):
    """A child that is itself a group expands recursively, in order."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (3, "c", f"echo C >> {out}"),
            (20, "inner", "@2\n@3"),
            (10, "outer", "@1\n@inner"),
        ],
    )
    result = _run(tmp_path, db_path, "outer")
    assert result.returncode == 0, result.stderr
    assert out.read_text().split() == ["A", "B", "C"]


def test_direct_cycle_errors(tmp_path):
    """A group that references itself errors with a cycle message."""
    db_path = tmp_path / "g.db"
    _seed(db_path, [(10, "grp", "@grp")])
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode != 0
    assert "cycle" in result.stderr.lower()


def test_indirect_cycle_errors(tmp_path):
    """A -> B -> A is detected as a cycle."""
    db_path = tmp_path / "g.db"
    _seed(db_path, [(10, "ga", "@gb"), (11, "gb", "@ga")])
    result = _run(tmp_path, db_path, "ga")
    assert result.returncode != 0
    assert "cycle" in result.stderr.lower()


def test_depth_limit_errors(tmp_path):
    """Nesting deeper than the max errors (chain of groups, no cycle)."""
    db_path = tmp_path / "g.db"
    # 12 groups each pointing at the next, then a leaf — exceeds max depth 10.
    entries = [(100 + i, f"g{i}", f"@g{i + 1}") for i in range(12)]
    entries.append((200, "leaf", "echo LEAF"))
    entries[-2] = (100 + 11, "g11", "@leaf")
    _seed(db_path, entries)
    result = _run(tmp_path, db_path, "g0")
    assert result.returncode != 0
    assert "deep" in result.stderr.lower()


# --- Remote confirmation ---


def test_remote_child_refuses_without_tty(tmp_path):
    """A source=remote child makes the whole group refuse to run unattended."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (10, "grp", "@1\n@2"),
        ],
    )
    _mark_remote(db_path, "uid00002")
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode != 0
    assert "koda edit" in result.stderr
    assert not out.exists()


def test_remote_child_bypassed_with_force(tmp_path):
    """-f skips the group remote prompt and runs everything."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (10, "grp", "@1\n@2"),
        ],
    )
    _mark_remote(db_path, "uid00002")
    result = _run(tmp_path, db_path, "grp", "-f")
    assert result.returncode == 0, result.stderr
    assert out.read_text().split() == ["A", "B"]


def test_remote_group_bypassed_when_confirm_disabled(tmp_path):
    """exec.confirm_remote=false disables the group remote prompt."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (10, "grp", "@1\n@2"),
        ],
    )
    _mark_remote(db_path, "uid00002")
    config_path = tmp_path / "config.toml"
    config_path.write_text("[exec]\nconfirm_remote = false\n", encoding="utf-8")
    env = _base_env(tmp_path, db_path)
    env["KODA_CONFIG_PATH"] = str(config_path)
    result = subprocess.run(
        [sys.executable, "-c", "from koda.main import app; app()", "x", "grp"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text().split() == ["A", "B"]


def test_remote_group_entry_itself_triggers_refusal(tmp_path):
    """A remote GROUP entry (all children local) still gates on confirmation."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (10, "grp", "@1"),
        ],
    )
    _mark_remote(db_path, "uid00010")
    result = _run(tmp_path, db_path, "grp")
    assert result.returncode != 0
    assert "koda edit" in result.stderr
    assert not out.exists()


# --- Dry run ---


def test_dry_run_prints_one_line_per_child_runs_nothing(tmp_path):
    """--dry-run prints a shlex-quoted argv per child, in order, and runs none."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "a", f"echo A >> {out}"),
            (2, "b", f"echo B >> {out}"),
            (10, "grp", "@1\n@2"),
        ],
    )
    result = _run(tmp_path, db_path, "grp", "-n")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert "echo A" in lines[0]
    assert "echo B" in lines[1]
    assert not out.exists()  # nothing executed


# --- Trailing args after a group ref ---


def test_trailing_args_after_group_ref_error(tmp_path):
    """v1 does not support trailing args on a group; point to -V instead."""
    db_path = tmp_path / "g.db"
    _seed(db_path, [(1, "a", "echo A"), (10, "grp", "@1")])
    result = _run(tmp_path, db_path, "grp", "extra")
    assert result.returncode != 0
    assert "parameterize with -V" in result.stderr


# --- -V into the group body ---


def test_var_substitution_into_group_body(tmp_path):
    """-V parameterizes the group body itself, e.g. `@${SVC}` resolves to a ref."""
    db_path = tmp_path / "g.db"
    out = tmp_path / "out.txt"
    _seed(
        db_path,
        [
            (1, "web", f"echo WEB >> {out}"),
            (10, "grp", "@${SVC}"),
        ],
    )
    result = _run(tmp_path, db_path, "grp", "-V", "SVC=web")
    assert result.returncode == 0, result.stderr
    assert out.read_text().strip() == "WEB"
