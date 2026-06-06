"""CLI utilities: standardized error exits and confirmation prompts."""

import sys
from enum import IntEnum
from typing import NoReturn

import typer
from rich.console import Console

console = Console()
stderr_console = Console(stderr=True)


class ExitCode(IntEnum):
    SUCCESS = 0
    INVALID_ARG = 1
    NOT_FOUND = 2
    CANCELLED = 3
    DB_ERROR = 4


def exit_error(
    message: str,
    code: ExitCode = ExitCode.INVALID_ARG,
    style: str = "red",
) -> NoReturn:
    """Print a styled error message and raise typer.Exit with the given code.

    ``style`` is a Rich style tag (e.g. ``"red"`` or ``"yellow"``) so callers
    can preserve informational ("not found") wording while still exiting with
    a non-zero status.
    """
    stderr_console.print(f"[{style}]{message}[/{style}]")
    raise typer.Exit(code=int(code))


def confirm(prompt: str, default_no: bool = True) -> bool:
    """Interactive yes/no prompt.

    Returns False on EOF or a negative answer; raises typer.Exit when stdin
    is not a TTY so callers can require explicit ``-f/--force``.
    """
    if not sys.stdin.isatty():
        exit_error("Not a TTY: use -f/--force to skip the prompt.")
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    try:
        reply = input(prompt + suffix).strip().lower()
    except EOFError:
        console.print()
        return False
    if default_no:
        return reply in ("y", "yes")
    return reply not in ("n", "no")
