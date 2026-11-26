"""Shared CLI runtime: lazily resolved config/cache and cross-command helpers.

Must not import ``koda.main`` or command modules; importing it has no side effects.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import typer

from .cli_utils import ExitCode, console, exit_error
from .cmd_helpers.parsing import parse_var_items
from .config import Config, ConfigManager, ValidationError, vault_path_allowed
from .db import DatabaseError, MemoDatabase
from .models import VALID_SORT_COLUMNS, MemoRow
from .reconcile import reconcile_all

__app_name__ = "koda"
__version__ = version("koda-cli")

_config_manager: ConfigManager | None = None
_config: Config | None = None
_config_sources: dict[str, str] | None = None
_db: MemoDatabase | None = None


def get_config_manager() -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def _resolve_config() -> None:
    global _config, _config_sources
    if _config is None:
        _config, _config_sources = get_config_manager().load()


def get_config() -> Config:
    _resolve_config()
    assert _config is not None
    return _config


def get_config_sources() -> dict[str, str]:
    _resolve_config()
    assert _config_sources is not None
    return _config_sources


def get_vault() -> Path:
    """Vault dir. Re-validated here because file/env values bypass ``config set`` validation."""
    cfg = get_config()
    if not vault_path_allowed(cfg.vault_path):
        exit_error(
            f"Refusing to use vault.path {cfg.vault_path!r}: must be a directory under "
            "$HOME. Set KODA_DB_PATH_OVERRIDE=1 to allow another location."
        )
    return Path(cfg.vault_path).expanduser()


def get_entries_dir() -> Path:
    return get_vault() / "entries"


def get_db() -> MemoDatabase:
    global _db
    if _db is None:
        _db = MemoDatabase(path=get_vault() / ".koda" / "cache.db")
    return _db


def entry_file(row: MemoRow) -> Path | None:
    rel = get_db().path_for(row.uid)
    return get_entries_dir() / rel if rel else None


def delete_entry(row: MemoRow) -> None:
    path = entry_file(row)
    if path and path.exists():
        path.unlink()
    get_db().delete_by_uid(row.uid)


def resolve_sort(sort_by: str | None, desc: bool | None) -> tuple[str, bool]:
    """Apply config defaults and validate ``--sort-by``."""
    cfg = get_config()
    column = (sort_by or cfg.list_sort_by).lower()
    if column not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        exit_error(f"Invalid --sort-by '{sort_by}'. Use one of: {valid}.")
    return column, cfg.list_desc if desc is None else desc


def version_callback(value: bool):
    if value:
        console.print(f"{__app_name__} version: [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


def resolve_editor() -> list[str]:
    """``$EDITOR`` as argv (handles empty values and ``code --wait``); defaults to vim."""
    raw = os.environ.get("EDITOR", "").strip() or "vim"
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = [raw]
    return parts or ["vim"]


def launch_editor(path: str) -> None:
    editor = resolve_editor()
    try:
        subprocess.call([*editor, path])
    except OSError as e:
        exit_error(
            f"Could not launch editor {editor[0]!r}: {e}. "
            "Set $EDITOR to a valid editor, e.g. `export EDITOR=nano`."
        )


def init_db():
    """Bring the cache to schema and reconcile it with ``entries/`` (run by every command)."""
    try:
        db = get_db()
        db.init_db()
        reconcile_all(db, get_entries_dir())
    except typer.Exit:
        raise
    except DatabaseError as e:
        exit_error(str(e), code=ExitCode.DB_ERROR)
    except Exception as e:
        exit_error(f"Database Error: {e}", code=ExitCode.DB_ERROR)


def resolve_ref(ref: str | None) -> MemoRow:
    """None → latest entry; digits → idx; otherwise → shortcut. Exits if not found."""
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


def emit_raw(ref: str | None, vars: list[str] | None = None) -> None:
    row = resolve_ref(ref)
    content = _apply_vars(row.content if row.content is not None else "", vars)
    sys.stdout.write(content)


def _read_stdin_refs() -> list[str]:
    """Whitespace-separated refs from piped stdin."""
    if sys.stdin.isatty():
        return []
    return sys.stdin.read().split()


def single_stdin_ref(command: str) -> str | None:
    refs = _read_stdin_refs()
    if len(refs) > 1:
        exit_error(f"{command} accepts one ref from stdin. Got multiple values.")
    return refs[0] if refs else None


def _validate_list_columns(columns: list[str], source: str) -> None:
    try:
        ConfigManager.validate("list.columns", columns)
    except ValidationError:
        exit_error(f"Invalid {source}: {ConfigManager.error_message('list.columns')}")


def detect_install_method() -> str | None:
    """'uv', 'pipx', 'venv-pip', 'pip', or None."""
    for name, args in (("uv", ["tool", "list"]), ("pipx", ["list"])):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if "koda-cli" in out.stdout:
            return name
    if sys.prefix != sys.base_prefix:
        return "venv-pip"
    if shutil.which("python3"):
        return "pip"
    return None


def run_update() -> None:
    method = detect_install_method()
    if method is None:
        console.print("[yellow]Could not detect how koda was installed.[/yellow]")
        console.print("Update manually with one of:")
        console.print("  uv tool upgrade koda-cli")
        console.print("  pipx upgrade koda-cli")
        console.print("  python3 -m pip install --upgrade koda-cli")
        raise typer.Exit(code=1)
    cmd = {
        "uv": ["uv", "tool", "upgrade", "koda-cli"],
        "pipx": ["pipx", "upgrade", "koda-cli"],
        "venv-pip": [sys.executable, "-m", "pip", "install", "--upgrade", "koda-cli"],
        "pip": ["python3", "-m", "pip", "install", "--upgrade", "koda-cli"],
    }[method]
    console.print(f"[cyan]Detected install: {method}[/cyan] — running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd)
    except OSError as exc:
        console.print(f"[red]Failed to run update: {exc}[/red]")
        raise typer.Exit(code=1)
    raise typer.Exit(code=result.returncode)


def update_callback(value: bool) -> None:
    if value:
        run_update()
