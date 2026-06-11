"""`exec`/`x` must run the whole body in one shell, not line-by-line (issue #52).

`emit_exec` ends in ``os.execvp`` which replaces the process, so these run the
CLI as a real subprocess and inspect its output.
"""

import subprocess
import sys

from koda.db import MemoDatabase

ENV_KEYS = ("PATH", "HOME")


def _base_env(tmp_path, db_path):
    import os

    env = {k: os.environ[k] for k in ENV_KEYS if k in os.environ}
    env["KODA_DB_PATH"] = str(db_path)
    env["KODA_DB_PATH_OVERRIDE"] = "1"  # allow the temp DB path outside the data dir
    env["KODA_CONFIG_PATH"] = str(tmp_path / "nonexistent.toml")
    return env


def _run_x(tmp_path, body, extra=()):
    db_path = tmp_path / "exec.db"
    seed = MemoDatabase(backend="local", path=db_path)
    seed.init_db()
    seed.add_memo(
        uid="exec001",
        idx=1,
        shortcut=None,
        content=body,
        tags="",
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )
    return subprocess.run(
        [sys.executable, "-c", "from koda.main import app; app()", "x", *extra],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=_base_env(tmp_path, db_path),
    )


def test_three_line_script_runs_in_one_shell(tmp_path):
    """A 3-line script must share state across lines, not run each separately."""
    result = _run_x(tmp_path, 'a=1\nb=2\necho "sum=$((a + b))"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "sum=3"


def test_for_loop_body(tmp_path):
    result = _run_x(tmp_path, 'for i in 1 2 3; do\n  echo "n=$i"\ndone')
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["n=1", "n=2", "n=3"]


def test_function_definition_body(tmp_path):
    result = _run_x(tmp_path, 'greet() {\n  echo "hi $1"\n}\ngreet world')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "hi world"


def test_heredoc_body(tmp_path):
    result = _run_x(tmp_path, "cat <<EOF\nline A\nline B\nEOF")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["line A", "line B"]


def _seed_remote(tmp_path, body):
    db_path = tmp_path / "exec.db"
    seed = MemoDatabase(backend="local", path=db_path)
    seed.init_db()
    seed.add_memo("rem00001", 1, None, body, "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    with seed.connection() as conn:
        conn.execute("UPDATE memos SET source = 'remote' WHERE uid = ?", ("rem00001",))
    return db_path


def _run(tmp_path, db_path, *args):
    return subprocess.run(
        [sys.executable, "-c", "from koda.main import app; app()", "x", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=_base_env(tmp_path, db_path),
    )


def test_remote_entry_refuses_without_confirmation(tmp_path):
    """A source=remote entry must not execute unattended (no TTY to confirm),
    and the message steers toward `koda edit` rather than habitual -f."""
    db_path = _seed_remote(tmp_path, "echo SHOULD_NOT_RUN")
    result = _run(tmp_path, db_path, "1")
    assert result.returncode != 0
    assert "SHOULD_NOT_RUN" not in result.stdout
    assert "koda edit" in result.stderr


def test_remote_entry_runs_with_force(tmp_path):
    """-f skips the confirmation prompt for remote entries."""
    db_path = _seed_remote(tmp_path, "echo FORCED_RUN")
    result = _run(tmp_path, db_path, "1", "-f")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FORCED_RUN"


def test_local_entry_runs_without_prompt(tmp_path):
    """A normal (local) entry executes with no confirmation, even without a TTY."""
    result = _run_x(tmp_path, "echo LOCAL_OK")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "LOCAL_OK"


def test_remote_entry_runs_when_confirm_disabled(tmp_path):
    """With exec.confirm_remote=false, a remote entry runs without prompting."""
    db_path = _seed_remote(tmp_path, "echo OPTED_OUT")
    config_path = tmp_path / "config.toml"
    config_path.write_text("[exec]\nconfirm_remote = false\n", encoding="utf-8")
    env = _base_env(tmp_path, db_path)
    env["KODA_CONFIG_PATH"] = str(config_path)
    result = subprocess.run(
        [sys.executable, "-c", "from koda.main import app; app()", "x", "1"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "OPTED_OUT"


def test_dry_run_prints_command_without_executing(tmp_path):
    """--dry-run prints the `<shell> -c '<body>'` preview, not the body's output."""
    result = _run_x(tmp_path, "echo SHOULD_NOT_EXECUTE", extra=("-n",))
    assert result.returncode == 0, result.stderr
    # The preview framing is printed; had the body actually run, stdout would be
    # just `SHOULD_NOT_EXECUTE`. Asserting the `-c '...'` shape (rather than a
    # hardcoded shell name) keeps this robust to the configured default shell.
    assert " -c '" in result.stdout
    assert result.stdout.rstrip().endswith("'echo SHOULD_NOT_EXECUTE'")


def test_dry_run_has_no_side_effect(tmp_path):
    """Definitive non-execution proof: a body that would create a file does so on
    a real run (control) but not under --dry-run."""
    marker = tmp_path / "executed.marker"
    body = f"touch {marker}"

    # Control: a real run executes the body and creates the marker.
    real = _run_x(tmp_path, body)
    assert real.returncode == 0, real.stderr
    assert marker.exists()
    marker.unlink()
    (tmp_path / "exec.db").unlink()  # reset so _run_x can re-seed cleanly

    # Dry run: identical body, but nothing executes -> the marker stays absent.
    dry = _run_x(tmp_path, body, extra=("-n",))
    assert dry.returncode == 0, dry.stderr
    assert not marker.exists()


def test_dry_run_substitutes_variables(tmp_path):
    """Variables are expanded in the previewed command, same as a real run."""
    result = _run_x(tmp_path, "echo $1", extra=("-n", "-V", "world"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("'echo world'")


def test_dry_run_on_remote_entry_does_not_prompt(tmp_path):
    """A source=remote entry can be previewed safely: no refusal, no execution."""
    db_path = _seed_remote(tmp_path, "echo REMOTE_BODY")
    result = _run(tmp_path, db_path, "1", "-n")
    assert result.returncode == 0, result.stderr
    assert "koda edit" not in result.stderr  # not the refusal path
    assert result.stdout.rstrip().endswith("'echo REMOTE_BODY'")


def test_dry_run_reads_ref_from_stdin(tmp_path):
    """`koda x -n` with no ref argument reads a single ref from stdin."""
    db_path = tmp_path / "exec.db"
    seed = MemoDatabase(backend="local", path=db_path)
    seed.init_db()
    seed.add_memo(
        "stdin001", 7, None, "echo FROM_STDIN", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    result = subprocess.run(
        [sys.executable, "-c", "from koda.main import app; app()", "x", "-n"],
        capture_output=True,
        text=True,
        input="7\n",
        env=_base_env(tmp_path, db_path),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("'echo FROM_STDIN'")


# --- Trailing args: appended when the body has no placeholder, or used to fill
# --- $1/"$@"/${1:-default} when it does (the seeded entry is at idx 1). ---


def test_no_trailing_args_runs_base_command(tmp_path):
    """With no extra args the body runs exactly as written (back-compat)."""
    result = _run_x(tmp_path, "echo BASE", extra=("1",))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BASE"


def test_trailing_arg_appended_when_body_has_no_placeholder(tmp_path):
    """`koda x dcu a b` appends the args at the end, like a shell alias."""
    result = _run_x(tmp_path, "echo BASE", extra=("1", "x1", "x2"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "BASE x1 x2"


def test_trailing_arg_fills_positional_reference(tmp_path):
    """When the body uses $1, the trailing arg fills it (no append)."""
    result = _run_x(tmp_path, "echo logs-$1", extra=("1", "mypod"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "logs-mypod"


def test_shell_default_value_used_when_no_arg(tmp_path):
    """`${1:-default}` yields the default when no trailing arg is given."""
    result = _run_x(tmp_path, "echo Hello ${1:-world}", extra=("1",))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Hello world"


def test_shell_default_value_overridden_by_arg(tmp_path):
    """A trailing arg overrides the `${1:-default}` fallback."""
    result = _run_x(tmp_path, "echo Hello ${1:-world}", extra=("1", "Alice"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Hello Alice"


def test_trailing_args_preserve_word_boundaries(tmp_path):
    """Args are real shell positionals, so spaces in a value stay one word."""
    result = _run_x(tmp_path, 'printf "[%s]\\n" "$@"', extra=("1", "a b", "c"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["[a b]", "[c]"]


def test_dry_run_shows_appended_positional_args(tmp_path):
    """--dry-run renders the real argv, including the appended `"$@"` tail."""
    result = _run_x(tmp_path, "docker compose up -d", extra=("1", "-n", "svc"))
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    assert " -c 'docker compose up -d \"$@\"' " in out
    assert out.endswith(" svc")


def test_trailing_option_like_args_via_double_dash(tmp_path):
    """`--` lets args that look like options pass through to the command."""
    result = _run_x(tmp_path, "docker compose up -d", extra=("1", "-n", "--", "--build"))
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    assert '"$@"' in out
    assert out.endswith(" --build")


def test_trailing_unknown_option_passes_through(tmp_path):
    """Unknown options are forwarded to the command, not rejected by koda."""
    result = _run_x(tmp_path, "docker compose up -d", extra=("1", "--build", "-n"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith(" --build")
