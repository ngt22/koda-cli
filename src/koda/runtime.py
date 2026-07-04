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
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import typer
from rich.console import Console

from .cli_utils import ExitCode, exit_error
from .cmd_helpers.parsing import parse_var_items
from .config import Config, ConfigManager, ValidationError, vault_path_allowed
from .db import DatabaseError, MemoDatabase
from .reconcile import reconcile_all

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


def get_vault() -> Path:
    """Return the vault directory (holds ``entries/`` and the ``.koda`` cache).

    vault.path from a config file or KODA_VAULT_PATH env bypasses validate(), so
    re-check here before koda creates files at an attacker-chosen location."""
    cfg = get_config()
    if not vault_path_allowed(cfg.vault_path):
        exit_error(
            f"Refusing to use vault.path {cfg.vault_path!r}: must be a directory under "
            "$HOME. Set KODA_DB_PATH_OVERRIDE=1 to allow another location."
        )
    return Path(cfg.vault_path).expanduser()


def get_entries_dir() -> Path:
    """Return ``<vault>/entries`` — the source-of-truth ``.md`` directory."""
    return get_vault() / "entries"


def _cache_path() -> Path:
    """Return ``<vault>/.koda/cache.db`` — the disposable derived cache."""
    return get_vault() / ".koda" / "cache.db"


def _resolve_db() -> MemoDatabase:
    """Return the MemoDatabase (cache) handle, constructing it lazily."""
    global _db
    if _db is None:
        _db = MemoDatabase(path=_cache_path())
    return _db


def get_db() -> MemoDatabase:
    """Return the lazily constructed MemoDatabase (cache) handle."""
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
    """Ensure the cache exists and reflects the current ``.md`` files.

    Runs on every command: the cache is brought up to schema, then reconciled
    against ``entries/`` (mtime-gated, so the steady state is just ``stat``
    calls) so external add/edit/delete/rename by Obsidian, an editor, an AI
    agent, or ``git pull`` are picked up before the command reads anything."""
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


def emit_raw(ref: str | None, vars: list[str] | None = None) -> None:
    init_db()
    row = resolve_ref(ref)
    content = _apply_vars(row.content if row.content is not None else "", vars)
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


def detect_install_method() -> str | None:
    """Detect how koda was installed: 'uv', 'pipx', 'venv-pip', 'pip', or None."""
    uv = shutil.which("uv")
    if uv:
        try:
            out = subprocess.run([uv, "tool", "list"], capture_output=True, text=True, timeout=10)
            if "koda-cli" in out.stdout:
                return "uv"
        except (OSError, subprocess.SubprocessError):
            pass
    pipx = shutil.which("pipx")
    if pipx:
        try:
            out = subprocess.run([pipx, "list"], capture_output=True, text=True, timeout=10)
            if "koda-cli" in out.stdout:
                return "pipx"
        except (OSError, subprocess.SubprocessError):
            pass
    if sys.prefix != sys.base_prefix:
        return "venv-pip"
    if shutil.which("python3"):
        return "pip"
    return None


def run_update() -> None:
    """Update koda to the latest version using the detected install method."""
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
    """Typer eager callback for ``--update``."""
    if value:
        run_update()
