"""Shared CLI runtime: lazy config/DB resolution and cross-command helpers.

Everything here is imported by the command modules in ``koda.commands`` and by
``koda.main``. It deliberately does NOT import the Typer ``app`` or any command
module, so importing it (and therefore ``koda.main``) has no side effects: no
config read, no DB handle. Config and the database are resolved lazily on first
use.
"""

import os
import re
import shlex
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import typer
from rich.console import Console

from .cli_utils import ExitCode, exit_error
from .cmd_helpers.parsing import parse_var_items
from .config import Config, ConfigManager, ValidationError, db_path_allowed
from .db import DatabaseError, MemoDatabase

__app_name__ = "koda"
__version__ = version("koda-cli")

console = Console()

# Config and DB are resolved lazily so that ``import koda.main`` has no
# side effects (no config load, no DB handle). This keeps the module
# importable in environments without HOME/env set and makes it testable.
_config_manager: ConfigManager | None = None
_config: Config | None = None
_config_sources: dict[str, str] | None = None
_db: MemoDatabase | None = None


def get_config_manager() -> ConfigManager:
    """Return the process-wide ConfigManager, creating it on first use."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def _resolve_config() -> None:
    """Load the config + per-key source map once and cache them."""
    global _config, _config_sources
    if _config is None:
        _config, _config_sources = get_config_manager().load()


def get_config() -> Config:
    """Return the loaded Config, loading it lazily on first use."""
    _resolve_config()
    assert _config is not None
    return _config


def get_config_sources() -> dict[str, str]:
    """Return the per-key config source map, loading lazily on first use."""
    _resolve_config()
    assert _config_sources is not None
    return _config_sources


def _resolve_db() -> MemoDatabase:
    """Return the MemoDatabase handle, constructing it lazily on first use."""
    global _db
    if _db is None:
        cfg = get_config()
        # db.path from a config file or KODA_DB_PATH env bypasses validate(),
        # so re-check here before init_db can mkdir/create a file at an
        # attacker-chosen location (e.g. ~/.ssh/authorized_keys).
        if cfg.db_backend == "local" and not db_path_allowed(cfg.db_path):
            exit_error(
                f"Refusing to use db.path {cfg.db_path!r}: outside the koda data dir "
                "(~/.local/share/koda or $XDG_DATA_HOME/koda). "
                "Set KODA_DB_PATH_OVERRIDE=1 to allow another location."
            )
        _db = MemoDatabase(
            backend=cfg.db_backend,
            path=Path(cfg.db_path).expanduser(),
            turso_url=cfg.turso_url,
            turso_token=cfg.turso_token,
        )
    return _db


def get_db() -> MemoDatabase:
    """Return the lazily constructed MemoDatabase handle."""
    return _resolve_db()


def version_callback(value: bool):
    if value:
        console.print(f"{__app_name__} version: [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


def resolve_editor() -> list[str]:
    """Resolve ``$EDITOR`` to a command vector, falling back to ``vim``.

    Handles an empty/whitespace ``EDITOR`` (which would otherwise try to exec
    ``""`` and crash) and multi-word editors such as ``code --wait``.
    """
    raw = os.environ.get("EDITOR", "").strip() or "vim"
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = [raw]
    return parts or ["vim"]


def launch_editor(path: str) -> None:
    """Open ``path`` in the user's editor, exiting cleanly if it cannot run."""
    editor = resolve_editor()
    try:
        subprocess.call([*editor, path])
    except OSError as e:
        exit_error(
            f"Could not launch editor {editor[0]!r}: {e}. "
            "Set $EDITOR to a valid editor, e.g. `export EDITOR=nano`."
        )


def init_db():
    try:
        get_db().init_db()
    except typer.Exit:
        raise
    except DatabaseError as e:
        exit_error(str(e), code=ExitCode.DB_ERROR)
    except Exception as e:
        exit_error(f"Database Error: {e}", code=ExitCode.DB_ERROR)


def resolve_ref(ref: str | None):
    """Return (id, uid, idx, content, tags, shortcut, created_at) or exit.

    ref=None → latest; digit string → idx lookup; other string → shortcut lookup.
    """
    if ref is None:
        row = get_db().get_latest_entry()
        if row is None:
            exit_error("No entries in database.", code=ExitCode.NOT_FOUND, style="yellow")
        return row
    if ref.isdigit():
        row = get_db().get_memo_by_idx(int(ref))
        if row is None:
            exit_error(f"No entry at index {ref}.", code=ExitCode.NOT_FOUND, style="yellow")
        return row
    row = get_db().get_memo_by_shortcut(ref)
    if row is None:
        exit_error(f"No entry with shortcut {ref!r}.", code=ExitCode.NOT_FOUND, style="yellow")
    return row


def _apply_vars(content: str, vars: list[str] | None) -> str:
    if not vars:
        return content
    pos_index = 1
    for var_spec in vars:
        stripped = var_spec.strip()
        m = re.match(r"^(\w+)=(.*)", stripped, re.DOTALL)
        if m:
            key, value = m.group(1), m.group(2)
            content = content.replace(f"${{{key}}}", value)
        else:
            for item in parse_var_items(stripped):
                content = re.sub(rf"\${pos_index}(?!\d)", item.replace("\\", "\\\\"), content)
                pos_index += 1
    return content


def _strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False

    for i, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if in_single:
            if ch == "'":
                in_single = False
            continue
        if in_double:
            if ch == '"':
                in_double = False
            continue
        if ch == "'":
            in_single = True
            continue
        if ch == '"':
            in_double = True
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line


def _strip_raw_inline_comments(content: str) -> str:
    lines = content.splitlines(keepends=True)
    if not lines:
        return content

    stripped_lines = []
    for line in lines:
        newline = ""
        body = line
        if line.endswith("\r\n"):
            newline = "\r\n"
            body = line[:-2]
        elif line.endswith("\n") or line.endswith("\r"):
            newline = line[-1]
            body = line[:-1]
        stripped_lines.append(_strip_inline_comment(body) + newline)
    return "".join(stripped_lines)


def emit_raw(ref: str | None, vars: list[str] | None = None) -> None:
    init_db()
    row = resolve_ref(ref)
    content = _apply_vars(row.content if row.content is not None else "", vars)
    content = _strip_raw_inline_comments(content)
    # POSIX text files end in a newline; append one when the body is non-empty
    # and not already newline-terminated so `koda raw | wc -l` and friends work.
    if content and not content.endswith("\n"):
        content += "\n"
    sys.stdout.write(content)


def _read_stdin_refs() -> list[str]:
    """Read whitespace-separated entry refs from stdin (non-interactive only)."""
    if sys.stdin.isatty():
        return []
    data = sys.stdin.read().strip()
    if not data:
        return []
    return [part for part in data.split() if part]


def _validate_list_columns(columns: list[str], source: str) -> None:
    try:
        ConfigManager.validate("list.columns", columns)
    except ValidationError:
        exit_error(f"Invalid {source}: {ConfigManager.error_message('list.columns')}")
