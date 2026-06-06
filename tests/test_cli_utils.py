"""`exit_error` must write to stderr, not stdout (issue #50)."""

import pytest
import typer

from koda.cli_utils import ExitCode, exit_error


def test_exit_error_writes_to_stderr_not_stdout(capsys):
    with pytest.raises(typer.Exit) as exc_info:
        exit_error("boom")
    assert exc_info.value.exit_code == ExitCode.INVALID_ARG
    captured = capsys.readouterr()
    assert "boom" not in captured.out
    assert "boom" in captured.err


def test_exit_error_honors_custom_code(capsys):
    with pytest.raises(typer.Exit) as exc_info:
        exit_error("nope", code=ExitCode.NOT_FOUND)
    assert exc_info.value.exit_code == ExitCode.NOT_FOUND
    assert "nope" in capsys.readouterr().err
