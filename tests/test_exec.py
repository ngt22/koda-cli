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
    env["KODA_CONFIG_PATH"] = str(tmp_path / "nonexistent.toml")
    return env


def _run_x(tmp_path, body):
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
        [sys.executable, "-c", "from koda.main import app; app()", "x"],
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
