"""`raw` output must be newline-terminated for POSIX tool interop (issue #51)."""

import subprocess
import sys

import pytest

import koda.runtime as runtime
from koda.db import MemoDatabase


@pytest.fixture
def wired_db(db, monkeypatch):
    """Point the lazy DB cache at a fresh temp database."""
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, content):
    db.add_memo(
        uid="seed001",
        idx=1,
        shortcut=None,
        content=content,
        tags="",
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def test_appends_newline_when_missing(wired_db, capsys):
    _seed(wired_db, "no trailing newline")
    runtime.emit_raw(None)
    assert capsys.readouterr().out == "no trailing newline\n"


def test_does_not_double_newline(wired_db, capsys):
    _seed(wired_db, "already terminated\n")
    runtime.emit_raw(None)
    assert capsys.readouterr().out == "already terminated\n"


def test_empty_content_stays_empty(wired_db, capsys):
    _seed(wired_db, "")
    runtime.emit_raw(None)
    assert capsys.readouterr().out == ""


def test_raw_subprocess_is_newline_terminated(tmp_path, monkeypatch):
    """Acceptance criterion: run `raw` as a real process and inspect the tail."""
    db_path = tmp_path / "raw.db"
    seed = MemoDatabase(backend="local", path=db_path)
    seed.init_db()
    _seed(seed, "subprocess body")

    env = {
        "KODA_DB_PATH": str(db_path),
        "KODA_DB_PATH_OVERRIDE": "1",  # allow the temp DB path outside the data dir
        "KODA_CONFIG_PATH": str(tmp_path / "nonexistent.toml"),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    result = subprocess.run(
        [sys.executable, "-c", "from koda.main import app; app()", "raw"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith("\n")
    assert result.stdout.count("\n") == 1  # `koda raw | wc -l` == 1
