"""Consoles, exit codes, error exits and prompts."""

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
    CONFLICT = 5


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural_form or singular + 's')}"


def entries(n: int) -> str:
    return plural(n, "entry", "entries")


def exit_error(
    message: str,
    code: ExitCode = ExitCode.INVALID_ARG,
    style: str = "red",
) -> NoReturn:
    stderr_console.print(f"[{style}]{message}[/{style}]")
    raise typer.Exit(code=int(code))


def confirm(prompt: str, default_no: bool = True) -> bool:
    """Yes/no prompt; exits on a non-TTY so callers must offer -f/--force."""
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
