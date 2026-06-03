"""Tests for `koda add` input resolution: argument vs stdin priority (#49).

Covers all four combinations of (argument present?, stdin piped?):
- arg + no stdin   → argument
- no arg + stdin   → stdin
- arg + stdin      → argument, with a warning on stderr
- no arg + no stdin (non-interactive) → empty, aborts cleanly
"""

import pytest
from typer.testing import CliRunner

from koda import main as koda_main


@pytest.fixture
def runner(tmp_path, monkeypatch):
    """A CliRunner whose koda app writes to a fresh temp DB."""
    from koda.db import MemoDatabase

    database = MemoDatabase(backend="local", path=tmp_path / "test.db")
    database.init_db()
    monkeypatch.setattr(koda_main, "db", database)
    return CliRunner()


def _latest_content(monkeypatched_db):
    row = monkeypatched_db.get_latest_entry()
    return row.content if row else None


def test_arg_only(runner):
    result = runner.invoke(koda_main.app, ["add", "hello from arg"])
    assert result.exit_code == 0
    assert _latest_content(koda_main.db) == "hello from arg"


def test_stdin_only(runner):
    result = runner.invoke(koda_main.app, ["add"], input="hello from stdin\n")
    assert result.exit_code == 0
    assert _latest_content(koda_main.db) == "hello from stdin"


def test_arg_wins_over_stdin(runner):
    result = runner.invoke(
        koda_main.app, ["add", "hello from arg"], input="hello from stdin\n"
    )
    assert result.exit_code == 0
    assert _latest_content(koda_main.db) == "hello from arg"
    assert "Warning" in result.stderr
    assert "argument" in result.stderr


def test_no_arg_no_stdin_aborts(runner):
    # Non-interactive stdin that is empty: must not hang on $EDITOR and must
    # abort with empty content instead of dying obscurely.
    result = runner.invoke(koda_main.app, ["add"], input="")
    assert result.exit_code == 0
    assert _latest_content(koda_main.db) is None
    assert "Empty content" in result.stdout


def test_multiword_arg_joined(runner):
    result = runner.invoke(koda_main.app, ["add", "one", "two", "three"])
    assert result.exit_code == 0
    assert _latest_content(koda_main.db) == "one two three"
