import tomllib
import typer
import sqlite3
import sys
import hashlib
import copy
import csv
from click.utils import make_str
from typer.core import TyperGroup
import os
import subprocess
import tempfile
import re
import signal
import shutil
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Optional, List, Callable
from rich.console import Console
from rich.table import Table

__app_name__ = "koda"
__version__ = version("koda")
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


ALIASES = {
    "a": "add",
    "c": "copy",
    "d": "remove",
    "e": "edit",
    "g": "config",
    "h": "shift",
    "k": "compact",
    "l": "list",
    "m": "move",
    "p": "pick",
    "r": "raw",
    "s": "show",
    "t": "tag",
    "w": "swap",
    "x": "exec",
}
RESERVED_SHORTCUTS = set(ALIASES.keys())


class KodaGroup(TyperGroup):
    """Resolve bare refs (numeric idx or shortcut string) to the default command."""

    def resolve_command(self, ctx, args):
        if args:
            cmd_name = make_str(args[0])
            if cmd_name in ALIASES:
                args = [ALIASES[cmd_name]] + list(args[1:])
            elif self.get_command(ctx, cmd_name) is None and not cmd_name.startswith("-"):
                default_cmd = _config["defaults"]["cmd"]
                target_name = ALIASES.get(default_cmd, default_cmd)
                target_cmd = self.get_command(ctx, target_name)
                if target_cmd is not None:
                    return target_name, target_cmd, list(args)
        return super().resolve_command(ctx, args)


app = typer.Typer(
    help=(
        "Koda — memos and terminal snippets in SQLite. "
        "Run with no subcommand to print the latest entry body (same as `koda raw`).\n\n"
        "One-letter aliases:\n"
        "a=add c=copy d=remove e=edit g=config h=shift k=compact\n"
        "l=list m=move p=pick r=raw s=show t=tag w=swap x=exec"
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
    cls=KodaGroup,
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()

# ── Database path ─────────────────────────────────────────────────────────────
DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "koda"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "koda.db"

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "koda"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"
CONFIG_PATH = Path(os.getenv("KODA_CONFIG_PATH", DEFAULT_CONFIG_PATH))

VALID_SORT_COLUMNS = {"id", "idx", "uid", "tags", "content", "created_at", "modified_at", "shortcut"}

CONFIG_DEFAULTS: dict = {
    "defaults": {"cmd": "raw"},
    "list":     {"per_page": 20, "rows": 1, "truncate": 80, "sort_by": "idx", "desc": False},
    "db":       {"path": str(DEFAULT_DB_PATH)},
    "exec":     {"shell": "sh"},
}

_ALL_KEYS: set[str] = {
    f"{sec}.{k}" for sec, vals in CONFIG_DEFAULTS.items() for k in vals
}

_CONFIG_TYPES: dict[str, type] = {
    "defaults.cmd":  str,
    "list.per_page": int,
    "list.rows":     int,
    "list.truncate": int,
    "list.sort_by":  str,
    "list.desc":     bool,
    "db.path":       str,
    "exec.shell":    str,
}

_CONFIG_VALIDATORS: dict[str, tuple[Callable, str]] = {
    "defaults.cmd":  (lambda v: v in ("raw", "list", "show", "add"), "must be 'raw', 'list', 'show', or 'add'"),
    "list.per_page": (lambda v: v >= 1,                       "must be >= 1"),
    "list.rows":     (lambda v: v >= 0,                       "must be >= 0"),
    "list.truncate": (lambda v: v >= 0,                       "must be >= 0"),
    "list.sort_by":  (
        lambda v: v in VALID_SORT_COLUMNS,
        f"must be one of: {', '.join(sorted(VALID_SORT_COLUMNS))}",
    ),
}


def load_config() -> tuple[dict, dict]:
    """Return (merged_config, source_map). source_map[dotkey] = 'default'|'file'|'env'."""
    config = copy.deepcopy(CONFIG_DEFAULTS)
    sources: dict[str, str] = {
        f"{sec}.{key}": "default"
        for sec, vals in CONFIG_DEFAULTS.items()
        for key in vals
    }

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "rb") as f:
                file_data = tomllib.load(f)
            for sec, vals in file_data.items():
                if sec in config and isinstance(vals, dict):
                    for key, val in vals.items():
                        if key in config[sec]:
                            config[sec][key] = val
                            sources[f"{sec}.{key}"] = "file"
        except Exception as e:
            console.print(f"[yellow]Warning: could not read config: {e}[/yellow]")

    env_cmd = os.getenv("KODA_DEFAULT_CMD")
    if env_cmd:
        config["defaults"]["cmd"] = env_cmd
        sources["defaults.cmd"] = "env"

    env_db = os.getenv("KODA_DB_PATH")
    if env_db:
        config["db"]["path"] = env_db
        sources["db.path"] = "env"

    return config, sources


def _read_config_file() -> dict:
    """Read the config file as-is (no merging with defaults). Returns {} if absent."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        console.print(f"[red]Could not read config file: {e}[/red]")
        raise typer.Exit(code=1)


def _write_config_file(data: dict) -> None:
    """Serialize data to TOML and write to CONFIG_PATH."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for section, values in data.items():
        lines.append(f"[{section}]")
        for k, v in values.items():
            if isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            else:
                lines.append(f"{k} = {v}")
        lines.append("")
    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")


def _coerce_config_value(key: str, raw: str):
    typ = _CONFIG_TYPES.get(key, str)
    try:
        if typ is bool:
            if raw.lower() in ("true", "1", "yes"):
                return True
            elif raw.lower() in ("false", "0", "no"):
                return False
            else:
                raise ValueError(raw)
        return typ(raw)
    except (ValueError, TypeError):
        console.print(
            f"[red]Invalid value for {key!r}: {raw!r} (expected {typ.__name__})[/red]"
        )
        raise typer.Exit(code=1)


_config, _config_sources = load_config()
DB_PATH = Path(_config["db"]["path"])


def version_callback(value: bool):
    if value:
        console.print(f"{__app_name__} version: [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


def signal_handler(sig, frame):
    sys.exit(0)


def _generate_uid(content: str, created_at: str) -> str:
    raw = f"{content}{created_at}".encode()
    return hashlib.sha1(raw).hexdigest()[:7]


def init_db():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memos (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid         TEXT UNIQUE,
                    idx         INTEGER UNIQUE,
                    shortcut    TEXT,
                    content     TEXT,
                    tags        TEXT,
                    created_at  TIMESTAMP,
                    modified_at TIMESTAMP
                )
            """)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(memos)").fetchall()}
            if "shortcut" not in cols:
                conn.execute("ALTER TABLE memos ADD COLUMN shortcut TEXT")
            # Unique index allows NULL (multiple NULLs are OK), enforces uniqueness for non-NULL values
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memos_shortcut "
                "ON memos(shortcut) WHERE shortcut IS NOT NULL"
            )
    except Exception as e:
        console.print(f"[red]Database Error:[/red] {e}")
        raise typer.Exit(code=1)


def _next_idx(conn) -> int:
    row = conn.execute("SELECT MAX(idx) FROM memos").fetchone()
    return (row[0] + 1) if row[0] is not None else 0


def _build_memo_filters(query=None, tag=None, exclude_tag=None, shortcuts_only=False):
    sql = " WHERE 1=1"
    params = []
    if query:
        sql += " AND content LIKE ?"
        params.append(f"%{query}%")
    if tag:
        sql += " AND tags LIKE ?"
        params.append(f"%{tag}%")
    if exclude_tag:
        sql += " AND (tags IS NULL OR tags = '' OR tags NOT LIKE ?)"
        params.append(f"%{exclude_tag}%")
    if shortcuts_only:
        sql += " AND shortcut IS NOT NULL AND shortcut != ''"
    return sql, params


def get_memos(
    query=None,
    tag=None,
    exclude_tag=None,
    shortcuts_only=False,
    limit=20,
    offset=0,
    sort_by="idx",
    desc=False,
):
    order_column = sort_by if sort_by in VALID_SORT_COLUMNS else "idx"
    order_direction = "DESC" if desc else "ASC"
    where_sql, params = _build_memo_filters(query, tag, exclude_tag, shortcuts_only)

    sql = (
        "SELECT id, uid, idx, content, tags, shortcut, created_at FROM memos"
        f"{where_sql} ORDER BY {order_column} {order_direction}, id ASC LIMIT ? OFFSET ?"
    )
    params.append(limit)
    params.append(offset)
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(sql, params).fetchall()


def get_memo_stats(query=None, tag=None, exclude_tag=None, shortcuts_only=False):
    where_sql, params = _build_memo_filters(query, tag, exclude_tag, shortcuts_only)
    sql = f"SELECT COUNT(*), MAX(idx) FROM memos{where_sql}"
    with sqlite3.connect(DB_PATH) as conn:
        total_count, max_idx = conn.execute(sql, params).fetchone()
    return total_count, max_idx


def get_memos_all(
    query=None,
    tag=None,
    exclude_tag=None,
    shortcuts_only=False,
    sort_by="idx",
    desc=False,
):
    order_column = sort_by if sort_by in VALID_SORT_COLUMNS else "idx"
    order_direction = "DESC" if desc else "ASC"
    where_sql, params = _build_memo_filters(query, tag, exclude_tag, shortcuts_only)
    sql = (
        "SELECT id, uid, idx, content, tags, shortcut, created_at FROM memos"
        f"{where_sql} ORDER BY {order_column} {order_direction}, id ASC"
    )
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(sql, params).fetchall()


def delete_memo(memo_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))


def get_latest_entry():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT id, uid, idx, content, tags, shortcut, created_at FROM memos ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()


def resolve_ref(ref: Optional[str]):
    """Return (id, uid, idx, content, tags, shortcut, created_at) or exit.

    ref=None → latest; digit string → idx lookup; other string → shortcut lookup.
    """
    if ref is None:
        row = get_latest_entry()
        if row is None:
            console.print("[yellow]No entries in database.[/yellow]")
            raise typer.Exit(code=1)
        return row
    if ref.isdigit():
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, uid, idx, content, tags, shortcut, created_at FROM memos WHERE idx = ?",
                (int(ref),)
            ).fetchone()
        if row is None:
            console.print(f"[yellow]No entry at index {ref}.[/yellow]")
            raise typer.Exit(code=1)
        return row
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id, uid, idx, content, tags, shortcut, created_at FROM memos WHERE shortcut = ?",
            (ref,)
        ).fetchone()
    if row is None:
        console.print(f"[yellow]No entry with shortcut {ref!r}.[/yellow]")
        raise typer.Exit(code=1)
    return row


def _parse_indices(specs: List[str]) -> List[int]:
    result = []
    for spec in specs:
        m = re.fullmatch(r'(\d+)-(\d+)', spec)
        if m:
            result.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif spec.isdigit():
            result.append(int(spec))
        else:
            console.print(f"[red]Invalid index or range: {spec!r}[/red]")
            raise typer.Exit(code=1)
    return result


def _print_memo(uid, idx, shortcut, content, tags, created_at) -> None:
    sc_str = f" | SC: [bold green]{shortcut}[/bold green]" if shortcut else ""
    console.print(
        f"\n[bold cyan]IDX: {idx}[/bold cyan] ({uid}){sc_str} | {created_at}\n"
        f"Tags: [magenta]{tags}[/magenta]\n"
        + "-" * 20
        + f"\n{content}"
    )


def _parse_tag_args(tag_args: Optional[List[str]]) -> List[str]:
    result = []
    for t in (tag_args or []):
        result.extend(item.strip() for item in t.split(",") if item.strip())
    return result


def _validate_shortcut(shortcut: Optional[str]) -> Optional[str]:
    if shortcut and len(shortcut) == 1 and shortcut in RESERVED_SHORTCUTS:
        console.print(
            f"[red]Shortcut {shortcut!r} is reserved as a 1-letter subcommand alias.[/red]"
        )
        raise typer.Exit(code=1)
    return shortcut


def _parse_var_items(var_spec: str) -> List[str]:
    """Parse a var spec into items using CSV rules: comma-delimited, "..." for quoting."""
    reader = csv.reader([var_spec], quotechar='"', delimiter=',', skipinitialspace=True)
    return list(reader)[0]


def _apply_vars(content: str, vars: Optional[List[str]]) -> str:
    if not vars:
        return content
    pos_index = 1
    for var_spec in vars:
        stripped = var_spec.strip()
        m = re.match(r'^(\w+)=(.*)', stripped, re.DOTALL)
        if m:
            key, value = m.group(1), m.group(2)
            content = content.replace(f"${{{key}}}", value)
        else:
            for item in _parse_var_items(stripped):
                content = re.sub(rf'\${pos_index}(?!\d)', item.replace('\\', '\\\\'), content)
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


def emit_raw(ref: Optional[str], vars: Optional[List[str]] = None) -> None:
    init_db()
    row = resolve_ref(ref)
    content = _apply_vars(row[3] if row[3] is not None else "", vars)
    content = _strip_raw_inline_comments(content)
    sys.stdout.write(content)


def _read_stdin_refs() -> List[str]:
    """Read whitespace-separated entry refs from stdin (non-interactive only)."""
    if sys.stdin.isatty():
        return []
    data = sys.stdin.read().strip()
    if not data:
        return []
    return [part for part in data.split() if part]
  
def _pick_candidates(
    query: Optional[str],
    tag: Optional[str],
    exclude_tag: Optional[str],
    shortcuts_only: bool,
    sort_by: Optional[str],
    desc: Optional[bool],
):
    cfg = _config["list"]
    effective_sort = (sort_by or cfg["sort_by"]).lower()
    if effective_sort not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        console.print(f"[red]Invalid --sort-by '{sort_by}'. Use one of: {valid}.[/red]")
        raise typer.Exit(code=1)
    effective_desc = cfg["desc"] if desc is None else desc
    return get_memos_all(
        query=query,
        tag=tag,
        exclude_tag=exclude_tag,
        shortcuts_only=shortcuts_only,
        sort_by=effective_sort,
        desc=effective_desc,
    )

def _pick_with_fzf(candidates) -> Optional[str]:
    if shutil.which("fzf") is None:
        console.print("[red]fzf is not installed. Install fzf to use `koda pick`.[/red]")
        raise typer.Exit(code=1)

    if not sys.stdin.isatty():
        console.print("[red]`koda pick` requires an interactive TTY.[/red]")
        raise typer.Exit(code=1)

    lines = []
    for _, uid, idx, content, tags, shortcut, created_at in candidates:
        first_line = (content or "").splitlines()[0] if content else ""
        display = (
            f"{idx}\t{uid}\t{shortcut or '-'}\t{tags or '-'}\t{created_at}\t{first_line}"
        )
        lines.append(display)

    term_cols = shutil.get_terminal_size(fallback=(120, 40)).columns
    # Keep list area readable on narrower terminals by switching to bottom preview.
    preview_window = "right:55%:wrap" if term_cols >= 170 else "down:55%:wrap"

    proc = subprocess.run(
        [
            "fzf",
            "--delimiter", "\t",
            "--with-nth", "1,3,4,6",
            "--prompt", "koda> ",
            "--preview",
            "printf 'IDX: %s\\nUID: %s\\nSC: %s\\nTags: %s\\nCreated: %s\\n\\n%s\\n' {1} {2} {3} {4} {5} {6}",
            "--preview-window", preview_window,
        ],
        input="\n".join(lines),
        text=True,
        stdout=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return None

    selected = proc.stdout.strip()
    if not selected:
        return None
    return selected.split("\t", 1)[0].strip()


def _resolve_pick_action(
    edit_mode: bool,
    exec_mode: bool,
    raw_mode: bool,
    show_mode: bool,
    print_id: bool,
) -> str:
    selected = [
        name
        for enabled, name in (
            (edit_mode, "edit"),
            (exec_mode, "exec"),
            (raw_mode, "raw"),
            (show_mode, "show"),
        )
        if enabled
    ]
    if len(selected) > 1:
        console.print("[red]Use only one of --edit/-e, --exec/-x, --raw/-r, or --show/-s.[/red]")
        raise typer.Exit(code=1)
    if print_id and selected:
        console.print("[red]--print-id/-p cannot be combined with action flags.[/red]")
        raise typer.Exit(code=1)
    if selected:
        return selected[0]
    default_cmd = _config["defaults"]["cmd"]
    if default_cmd in ("raw", "show"):
        return default_cmd
    console.print(
        "[red]defaults.cmd must be 'raw' or 'show' for `koda pick` without action flags.[/red]"
    )
    console.print("[dim]Hint: use --exec/-x, --edit/-e, --raw/-r, or --show/-s.[/dim]")
    raise typer.Exit(code=1)


def _run_pick_action(action: str, ref: str) -> None:
    if action == "raw":
        emit_raw(ref)
        return
    if action == "show":
        init_db()
        row = resolve_ref(ref)
        _, uid, idx, content, tags, shortcut, created_at = row
        _print_memo(uid, idx, shortcut, content, tags, created_at)
        return
    if action == "edit":
        edit(ref)
        return
    if action == "exec":
        exec_memo(ref, None)
        return
    console.print(f"[red]Unsupported pick action: {action}[/red]")
    raise typer.Exit(code=1)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
):
    """Default: run the default command (see `koda config get defaults.cmd`)."""
    if ctx.invoked_subcommand is None:
        cmd = _config["defaults"]["cmd"]
        if cmd == "list":
            _list_memos_impl()
        elif cmd == "show":
            init_db()
            row = resolve_ref(None)
            _, uid, idx, content, tags, shortcut, created_at = row
            _print_memo(uid, idx, shortcut, content, tags, created_at)
        elif cmd == "add":
            _add_impl()
        else:
            emit_raw(None)


def update_memo_full(memo_id: int, content: str, tags: str, shortcut: Optional[str], created_at: str):
    now = datetime.now().strftime(DATETIME_FMT)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE memos SET content = ?, tags = ?, shortcut = ?, created_at = ?, modified_at = ? WHERE id = ?",
            (content.strip(), tags, shortcut or None, created_at, now, memo_id)
        )


def _normalize_footer_segment(segment: str) -> str:
    lines = segment.strip().splitlines()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if not t:
            i += 1
            continue
        if re.fullmatch(r"-{3,}", t):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def _looks_like_koda_footer(segment: str) -> bool:
    s = _normalize_footer_segment(segment)
    if not s:
        return False
    if s.startswith("# Metadata"):
        return True
    lines = [ln for ln in s.splitlines() if ln.strip()]
    return bool(lines and lines[0].strip().startswith("tags:"))


def _first_footer_index(parts: List[str]) -> Optional[int]:
    for i in range(1, len(parts)):
        if _looks_like_koda_footer(parts[i]):
            return i
    return None


def _last_footer_segment(parts: List[str]) -> Optional[str]:
    for seg in reversed(parts):
        if _looks_like_koda_footer(seg):
            return _normalize_footer_segment(seg)
    return None


def _add_impl(
    text: Optional[List[str]] = None,
    tag: Optional[List[str]] = None,
    shortcut: Optional[str] = None,
) -> None:
    shortcut = _validate_shortcut(shortcut)
    init_db()
    content = ""

    if not sys.stdin.isatty():
        content = sys.stdin.read().strip()
    elif text:
        content = " ".join(text)
    else:
        editor = os.environ.get('EDITOR', 'vim')
        with tempfile.NamedTemporaryFile(suffix=".tmp", mode='w+', delete=False) as tf:
            temp_path = tf.name
        try:
            subprocess.call([editor, temp_path])
            with open(temp_path, 'r') as f:
                content = f.read().strip()
        finally:
            Path(temp_path).unlink(missing_ok=True)

    if not content:
        console.print("[yellow]Aborted: Empty content.[/yellow]")
        return

    content = content.encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')

    formatted_tags = ",".join(dict.fromkeys(_parse_tag_args(tag)))

    now = datetime.now().strftime(DATETIME_FMT)
    uid = _generate_uid(content, now)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            new_idx = _next_idx(conn)
            conn.execute(
                "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, new_idx, shortcut or None, content, formatted_tags, now, now)
            )
    except sqlite3.IntegrityError:
        console.print(f"[red]Shortcut {shortcut!r} is already in use.[/red]")
        raise typer.Exit(code=1)

    sc_str = f" sc=[bold green]{shortcut}[/bold green]" if shortcut else ""
    console.print(f"[green]Saved [{new_idx}] ({uid}) tags: {formatted_tags}{sc_str}[/green]")


@app.command()
def add(
    text: Optional[List[str]] = typer.Argument(
        None, help="Text to save (optional if using stdin or $EDITOR)."
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", "-t", help="Comma-separated tag(s); repeat -t for more."
    ),
    shortcut: Optional[str] = typer.Option(
        None, "--shortcut", "-s", help="Short alias for this entry (e.g. 'deploy')."
    ),
):
    """Create an entry from arguments, stdin, or your editor. Alias: `koda a`."""
    _add_impl(text, tag, shortcut)


@app.command(name="remove")
def rm(
    indices: Optional[List[str]] = typer.Argument(
        None, help="Entry indices, ranges (e.g. 1 3 5-8), or a single shortcut. Default: latest."
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Delete entries whose tags match this substring."
    ),
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Delete entries whose body matches this substring."
    ),
    all_entries: bool = typer.Option(
        False, "--all", help="Delete ALL entries (requires -f)."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Delete without prompting."
    ),
):
    """Delete entries. Defaults to latest; supports ranges, -t, -q, and --all for batch. Alias: `koda d`."""
    if all_entries and not force:
        console.print("[red]--all requires -f/--force.[/red]")
        raise typer.Exit(code=1)

    init_db()
    is_batch = bool(tag or query or all_entries or (indices and (len(indices) > 1 or re.search(r'\d-\d', indices[0]))))

    if is_batch:
        if indices:
            idx_list = _parse_indices(indices)
            with sqlite3.connect(DB_PATH) as conn:
                target_rows = []
                for idx in idx_list:
                    row = conn.execute(
                        "SELECT id, uid, idx, content, tags, shortcut, created_at FROM memos WHERE idx = ?", (idx,)
                    ).fetchone()
                    if row is None:
                        console.print(f"[yellow]No entry at index {idx}, skipping.[/yellow]")
                    else:
                        target_rows.append(row)
        else:
            where_sql, params = _build_memo_filters(query=query, tag=tag)
            with sqlite3.connect(DB_PATH) as conn:
                target_rows = conn.execute(
                    f"SELECT id, uid, idx, content, tags, shortcut, created_at FROM memos{where_sql} ORDER BY idx ASC",
                    params,
                ).fetchall()

        if not target_rows:
            console.print("[yellow]No matching entries.[/yellow]")
            return

        n = len(target_rows)
        console.print(f"\n[bold red]About to delete {n} entr{'y' if n == 1 else 'ies'}:[/bold red]")
        for row in target_rows[:10]:
            _, uid, idx, content, _, _, _ = row
            preview = (content or "").splitlines()[0][:60]
            console.print(f"  [{idx}] ({uid}) {preview}")
        if n > 10:
            console.print(f"  ... and {n - 10} more")

        if not force:
            if not sys.stdin.isatty():
                console.print("[red]Not a TTY: use -f/--force to skip the prompt.[/red]")
                raise typer.Exit(code=1)
            try:
                reply = input(f"\nDelete {n} entr{'y' if n == 1 else 'ies'}? [y/N]: ").strip().lower()
            except EOFError:
                console.print("\n[yellow]Cancelled.[/yellow]")
                raise typer.Exit(code=0)
            if reply not in ("y", "yes"):
                console.print("[yellow]Cancelled.[/yellow]")
                raise typer.Exit(code=0)

        ids = [row[0] for row in target_rows]
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany("DELETE FROM memos WHERE id = ?", [(id_,) for id_ in ids])
        console.print(f"[red]Deleted {n} entr{'y' if n == 1 else 'ies'}.[/red]")

    else:
        ref = indices[0] if indices else None
        row = resolve_ref(ref)
        memo_id, uid, idx, content, tags, shortcut, created_at = row
        _print_memo(uid, idx, shortcut, content, tags, created_at)
        console.print("\n[bold red]This entry will be deleted.[/bold red]")

        if not force:
            if not sys.stdin.isatty():
                console.print(
                    "[red]Not a TTY: use [bold]-f/--force[/bold] to delete without a prompt.[/red]"
                )
                raise typer.Exit(code=1)
            try:
                reply = input("Delete this entry? [y/N]: ").strip().lower()
            except EOFError:
                console.print("\n[yellow]Cancelled.[/yellow]")
                raise typer.Exit(code=0)
            if reply not in ("y", "yes"):
                console.print("[yellow]Cancelled.[/yellow]")
                raise typer.Exit(code=0)

        delete_memo(memo_id)
        preview = content.splitlines()[0][:50] if content else ""
        console.print(f"[red]Deleted [{idx}]: {preview}...[/red]")


@app.command(name="copy")
def copy(
    ref: Optional[str] = typer.Argument(
        None, help="Source entry index or shortcut (default: latest)."
    ),
):
    """Duplicate an entry to a new row (same body and tags, no shortcut). Alias: `koda c`."""
    init_db()
    row = resolve_ref(ref)
    memo_id, uid, idx, content, tags, shortcut, created_at = row
    now = datetime.now().strftime(DATETIME_FMT)
    new_uid = _generate_uid(content, now)
    with sqlite3.connect(DB_PATH) as conn:
        new_idx = _next_idx(conn)
        conn.execute(
            "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_uid, new_idx, None, content, tags, now, now)
        )
    console.print(f"[green]Copied [{idx}] → [{new_idx}] ({new_uid}).[/green]")


@app.command()
def edit(
    ref: Optional[str] = typer.Argument(
        None, help="Entry index or shortcut to edit (default: latest)."
    ),
):
    """Open an entry in $EDITOR (body plus tags/shortcut/metadata footer). Alias: `koda e`."""
    init_db()
    row = resolve_ref(ref)
    memo_id, uid, idx, content, tags, shortcut, created_at = row

    sc_line = f"shortcut: {shortcut}" if shortcut else "shortcut: "
    template = f"{content}\n\n---\n# Metadata\ntags: {tags}\n{sc_line}\ncreated_at: {created_at}\n---"

    editor = os.environ.get('EDITOR', 'vim')
    with tempfile.NamedTemporaryFile(
        suffix=".tmp", mode="w+", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(template)
        temp_path = tf.name

    try:
        subprocess.call([editor, temp_path])
        with open(temp_path, 'r') as f:
            new_data = f.read()

        parts = re.split(r'\n---+\s*\n', new_data)
        while parts and not parts[-1].strip():
            parts.pop()

        footer_at = _first_footer_index(parts)
        if footer_at is not None:
            new_content = "\n---\n".join(parts[:footer_at]).strip()
            meta_section = _last_footer_segment(parts) or ""
            new_tags, new_shortcut, new_created_at = tags, shortcut, created_at
            for line in meta_section.splitlines():
                if line.startswith("tags:"):
                    new_tags = line.removeprefix("tags:").strip()
                elif line.startswith("shortcut:"):
                    val = line.removeprefix("shortcut:").strip()
                    new_shortcut = val if val else None
                elif line.startswith("created_at:"):
                    new_created_at = line.removeprefix("created_at:").strip()
            new_shortcut = _validate_shortcut(new_shortcut)

            try:
                update_memo_full(memo_id, new_content, new_tags, new_shortcut, new_created_at)
            except sqlite3.IntegrityError:
                console.print(f"[red]Shortcut {new_shortcut!r} is already in use.[/red]")
                raise typer.Exit(code=1)
            console.print(f"[green]Entry [{idx}] updated.[/green]")
        else:
            new_content = "\n---\n".join(parts).strip() if parts else new_data.strip()
            update_memo_full(memo_id, new_content, tags, shortcut, created_at)
            console.print(
                "[yellow]No metadata footer found; content updated, metadata preserved.[/yellow]"
            )
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _list_memos_impl(
    query: Optional[str] = None,
    tag: Optional[str] = None,
    exclude_tag: Optional[str] = None,
    shortcuts_only: bool = False,
    per_page: Optional[int] = None,
    page: int = 1,
    sort_by: Optional[str] = None,
    desc: Optional[bool] = None,
    rows: Optional[str] = None,
    truncate: Optional[int] = None,
) -> None:
    init_db()

    cfg = _config["list"]
    if per_page is None:
        per_page = cfg["per_page"]
    elif per_page < 1:
        console.print("[red]--per-page must be >= 1.[/red]")
        raise typer.Exit(code=1)
    if sort_by is None:
        sort_by = cfg["sort_by"]
    if desc is None:
        desc = cfg["desc"]
    if rows is None:
        rows = str(cfg["rows"])
    if truncate is None:
        truncate = cfg["truncate"]
    elif truncate < 0:
        console.print("[red]--truncate must be 0 or greater.[/red]")
        raise typer.Exit(code=1)

    normalized_sort = sort_by.lower()
    if normalized_sort not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        console.print(f"[red]Invalid --sort-by '{sort_by}'. Use one of: {valid}.[/red]")
        raise typer.Exit(code=1)

    rows_value: Optional[int]
    try:
        parsed_rows = int(rows)
        if parsed_rows < 0:
            raise ValueError
    except ValueError:
        console.print("[red]--rows must be an integer of 0 or greater.[/red]")
        raise typer.Exit(code=1)
    rows_value = None if parsed_rows == 0 else parsed_rows

    total_count, max_idx = get_memo_stats(query, tag, exclude_tag, shortcuts_only)
    total_pages = (total_count + per_page - 1) // per_page if total_count else 0

    if total_pages > 0 and page > total_pages:
        console.print(
            f"[yellow]Page {page} is out of range. Available pages: 1-{total_pages}.[/yellow]"
        )
        console.print(
            f"[dim]Total: {total_count} | Pages: {total_pages} | Max IDX: {max_idx}[/dim]"
        )
        raise typer.Exit(code=1)

    offset = (page - 1) * per_page
    memos = get_memos(
        query,
        tag,
        exclude_tag,
        shortcuts_only,
        limit=per_page,
        offset=offset,
        sort_by=normalized_sort,
        desc=desc,
    )
    if not memos:
        console.print("[yellow]No entries found.[/yellow]")
        console.print("[dim]Total: 0 | Pages: 0 | Max IDX: -[/dim]")
        return

    table = Table(box=None, header_style="bold magenta", expand=True)
    table.add_column("IDX", justify="right", width=4)
    table.add_column("UID", width=7, style="dim")
    table.add_column("SC", width=10, style="bold green")
    table.add_column("Tags", style="magenta", width=15)
    table.add_column("Content", ratio=1)
    table.add_column("Created At", width=19)
    for _, uid, idx, content, tags, sc, dt in memos:
        content_lines = (content or "").splitlines()
        if rows_value is None:
            preview_lines = content_lines if content_lines else [""]
        else:
            preview_lines = content_lines[:rows_value] if content_lines else [""]
            if content_lines and len(content_lines) > rows_value:
                preview_lines = preview_lines + ["..."]

        if truncate > 0:
            ellipsis = "..."
            if truncate <= len(ellipsis):
                preview_lines = [line[:truncate] for line in preview_lines]
            else:
                preview_lines = [
                    line if len(line) <= truncate else line[: truncate - len(ellipsis)] + ellipsis
                    for line in preview_lines
                ]

        preview = "\n".join(preview_lines)
        table.add_row(
            str(idx),
            uid or "",
            sc or "",
            tags or "",
            preview,
            dt,
        )
    console.print(table)
    rows_text = "0" if rows_value is None else str(rows_value)
    truncate_text = "off" if truncate == 0 else str(truncate)
    console.print(
        f"[dim]Total: {total_count} | Pages: {total_pages} | Max IDX: {max_idx} | "
        f"Rows: {rows_text} | Truncate: {truncate_text}[/dim]"
    )


@app.command(name="list")
def list_memos(
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Substring match on memo body."
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Substring match on tags."
    ),
    exclude_tag: Optional[str] = typer.Option(
        None, "--exclude-tag", "-T",
        help="Exclude entries whose tags include this substring.",
    ),
    shortcuts_only: bool = typer.Option(
        False, "--shortcuts", "-S", help="Show only entries that have a shortcut."
    ),
    per_page: Optional[int] = typer.Option(
        None, "--per-page", "-n", help="Entries per page. [config: list.per_page]"
    ),
    page: int = typer.Option(
        1, "--page", "-p", min=1, help="1-based page number to display."
    ),
    sort_by: Optional[str] = typer.Option(
        None, "--sort-by", "-s", case_sensitive=False,
        help="Sort column: id, idx, uid, tags, content, created_at, modified_at, shortcut. [config: list.sort_by]",
    ),
    desc: Optional[bool] = typer.Option(
        None, "--desc/--asc", help="Sort order. [config: list.desc]",
    ),
    rows: Optional[str] = typer.Option(
        None, "--rows", "-r",
        help="Content preview lines per entry (0 = all lines). [config: list.rows]",
    ),
    truncate: Optional[int] = typer.Option(
        None, "--truncate",
        help="Max characters per content line (0 = no truncation). [config: list.truncate]",
    ),
):
    """Show entries as a table with paging and sortable columns. Alias: `koda l`."""
    _list_memos_impl(query, tag, exclude_tag, shortcuts_only, per_page, page, sort_by, desc, rows, truncate)


@app.command()
def pick(
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Substring match on memo body."
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Substring match on tags."
    ),
    exclude_tag: Optional[str] = typer.Option(
        None, "--exclude-tag", "-T", help="Exclude entries whose tags include this substring."
    ),
    shortcuts_only: bool = typer.Option(
        False, "--shortcuts", "-S", help="Show only entries that have a shortcut."
    ),
    sort_by: Optional[str] = typer.Option(
        None, "--sort-by", case_sensitive=False,
        help="Sort column: id, idx, uid, tags, content, created_at, modified_at, shortcut. [config: list.sort_by]",
    ),
    desc: Optional[bool] = typer.Option(
        None, "--desc/--asc", help="Sort order. [config: list.desc]",
    ),
    print_id: bool = typer.Option(
        False, "--print-id", "-p", help="Print selected IDX and exit without running a command."
    ),
    edit_mode: bool = typer.Option(
        False, "--edit", "-e", help="Open selected entry in editor."
    ),
    exec_mode: bool = typer.Option(
        False, "--exec", "-x", help="Execute selected entry."
    ),
    raw_mode: bool = typer.Option(
        False, "--raw", "-r", help="Print selected entry body."
    ),
    show_mode: bool = typer.Option(
        False, "--show", "-s", help="Show selected entry with metadata."
    ),
):
    """Pick an entry with fzf, then run an action (or print IDX). Alias: `koda p`."""
    if print_id and (edit_mode or exec_mode or raw_mode or show_mode):
        console.print("[red]--print-id/-p cannot be combined with action flags.[/red]")
        raise typer.Exit(code=1)

    action: Optional[str] = None if print_id else _resolve_pick_action(
        edit_mode, exec_mode, raw_mode, show_mode, print_id
    )

    init_db()
    candidates = _pick_candidates(query, tag, exclude_tag, shortcuts_only, sort_by, desc)
    if not candidates:
        console.print("[yellow]No entries found.[/yellow]")
        raise typer.Exit(code=1)

    selected_ref = _pick_with_fzf(candidates)
    if selected_ref is None:
        raise typer.Exit(code=0)

    if print_id:
        sys.stdout.write(selected_ref + "\n")
        return

    _run_pick_action(action, selected_ref)


@app.command()
def show(
    ref: Optional[str] = typer.Argument(
        None, help="Entry index or shortcut (default: latest)."
    ),
):

    """Print one entry with index, uid, tags, and timestamps (Rich formatted). Alias: `koda s`.
    
    When no argument is given, this command also accepts one ref from stdin.
    """
    if ref is None:
        stdin_refs = _read_stdin_refs()
        if len(stdin_refs) > 1:
            console.print("[red]show accepts one ref from stdin. Got multiple values.[/red]")
            raise typer.Exit(code=1)
        if stdin_refs:
            ref = stdin_refs[0]

    init_db()
    row = resolve_ref(ref)
    memo_id, uid, idx, content, tags, shortcut, created_at = row
    _print_memo(uid, idx, shortcut, content, tags, created_at)


@app.command()
def raw(
    entry_refs: Optional[List[str]] = typer.Argument(
        None,
        help="Entry index(es) or shortcut(s) (default: latest). Body only, for pipes and shell substitution.",
    ),
    vars: Optional[List[str]] = typer.Option(
        None, "--var", "-V",
        help=(
            "Variable substitution. Named: KEY=VALUE → replaces ${KEY}. "
            "Positional: VALUE → replaces $1,$2,... in order. "
            'Comma-separate multiple values; use "..." to include spaces or commas. '
            'Examples: -V \'localhost,5432\' -V \'name=prod\' -V \'"hello world","foo,bar"\''
        ),
    ),
):

    """Print memo body to stdout only (plain text, no Rich). Same as bare `koda <idx>`. Alias: `koda r`.

    When no argument is given, refs can also be passed from stdin.
    """
    refs = entry_refs
    if not refs:
        stdin_refs = _read_stdin_refs()
        refs = stdin_refs or None

    if not refs:

        emit_raw(None, vars)
    else:
        for ref in refs:
            emit_raw(ref, vars)


@app.command(name="exec")
def exec_memo(
    ref: Optional[str] = typer.Argument(
        None, help="Entry index or shortcut to execute (default: latest)."
    ),
    vars: Optional[List[str]] = typer.Option(
        None, "--var", "-V",
        help=(
            "Variable substitution. Named: KEY=VALUE → replaces ${KEY}. "
            "Positional: VALUE → replaces $1,$2,... in order. "
            'Comma-separate multiple values; use "..." to include spaces or commas. '
            'Examples: -V \'localhost,5432\' -V \'name=prod\' -V \'"hello world","foo,bar"\''
        ),
    ),
):

    """Execute the memo body as a shell command. Alias: `koda x`.

    When no argument is given, this command also accepts one ref from stdin.
    """
    if ref is None:
        stdin_refs = _read_stdin_refs()
        if len(stdin_refs) > 1:
            console.print("[red]ex accepts one ref from stdin. Got multiple values.[/red]")
            raise typer.Exit(code=1)
        if stdin_refs:
            ref = stdin_refs[0]

    init_db()
    row = resolve_ref(ref)
    memo_id, uid, idx, content, tags, shortcut, created_at = row
    content = _apply_vars(content.strip() if content else "", vars)
    shell = _config["exec"]["shell"]
    os.execvp(shell, [shell, "-c", content])


@app.command()
def tag(
    indices: List[str] = typer.Argument(..., help="Entry indices or ranges (e.g. 1 3 5-8)."),
    tags: Optional[List[str]] = typer.Option(None, "--tag", "-t", help="Tag(s) to add."),
    untag: Optional[List[str]] = typer.Option(None, "--untag", "-T", help="Tag(s) to remove."),
):
    """Add or remove tags on one or more entries. Supports ranges (e.g. 2-5). Alias: `koda t`."""
    if not tags and not untag:
        console.print("[red]Specify at least one of -t/--tag (add) or -T/--untag (remove).[/red]")
        raise typer.Exit(code=1)

    init_db()
    idx_list = _parse_indices(indices)
    add_list = _parse_tag_args(tags)
    remove_list = _parse_tag_args(untag)

    updated = 0
    with sqlite3.connect(DB_PATH) as conn:
        for idx in idx_list:
            row = conn.execute("SELECT id, tags FROM memos WHERE idx = ?", (idx,)).fetchone()
            if row is None:
                console.print(f"[yellow]No entry at index {idx}, skipping.[/yellow]")
                continue
            row_id, current_tags = row
            current = [t for t in (current_tags or "").split(",") if t.strip()]
            new_tags = [t for t in current if t not in remove_list]
            new_tags = new_tags + [t for t in add_list if t not in new_tags]
            conn.execute("UPDATE memos SET tags = ? WHERE id = ?", (",".join(new_tags), row_id))
            updated += 1

    parts = []
    if add_list:
        parts.append(f"Added to {updated} entr{'y' if updated == 1 else 'ies'}")
    if remove_list:
        parts.append(f"removed from {updated} entr{'y' if updated == 1 else 'ies'}" if add_list
                     else f"Removed from {updated} entr{'y' if updated == 1 else 'ies'}")
    console.print(f"[green]{'; '.join(parts)}.[/green]")


@app.command(name="move")
def move(
    from_idx: int = typer.Argument(..., help="Source display index."),
    to_idx: int = typer.Argument(..., help="Destination display index (must be empty)."),
):
    """Move entry at FROM to an unoccupied display position TO. Alias: `koda m`."""
    init_db()
    if from_idx == to_idx:
        return
    with sqlite3.connect(DB_PATH) as conn:
        if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (from_idx,)).fetchone() is None:
            console.print(f"[red]No entry at index {from_idx}.[/red]")
            raise typer.Exit(code=1)
        if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (to_idx,)).fetchone() is not None:
            console.print(f"[red]Index {to_idx} is already occupied.[/red]")
            console.print(
                f"[dim]Hint: `koda swap {from_idx} {to_idx}` to swap, "
                f"or `koda shift {to_idx}` to make room first.[/dim]"
            )
            raise typer.Exit(code=1)
        conn.execute("UPDATE memos SET idx = ? WHERE idx = ?", (to_idx, from_idx))
    console.print(f"[green]Moved {from_idx} → {to_idx}.[/green]")


@app.command(name="shift")
def shift_cmd(
    start: int = typer.Argument(..., help="Shift entries at this index and above."),
    count: int = typer.Option(1, "--count", "-n", help="Positions to shift (negative = shift down)."),
):
    """Shift all entries at START and above by COUNT positions. Alias: `koda h`."""
    init_db()
    if count == 0:
        return
    with sqlite3.connect(DB_PATH) as conn:
        if count < 0:
            if start + count < 0:
                console.print(
                    f"[red]Cannot shift down by {abs(count)}: "
                    f"index {start} would become {start + count} (negative indices not allowed).[/red]"
                )
                raise typer.Exit(code=1)
            collision = conn.execute(
                "SELECT 1 FROM memos WHERE idx >= ? AND idx < ?",
                (start + count, start),
            ).fetchone()
            if collision:
                console.print(
                    f"[red]Cannot shift down by {abs(count)}: "
                    f"entries exist in [{start + count}, {start - 1}].[/red]"
                )
                raise typer.Exit(code=1)
        # Two-step update to avoid UNIQUE constraint violations during bulk shift
        OFFSET = 2_000_000
        conn.execute("UPDATE memos SET idx = idx + ? WHERE idx >= ?", (OFFSET, start))
        conn.execute(
            "UPDATE memos SET idx = idx - ? + ? WHERE idx >= ?",
            (OFFSET, count, OFFSET + start),
        )
    console.print(f"[green]Shifted entries from index {start} by {count:+d}.[/green]")


@app.command(name="swap")
def swap(
    idx1: int = typer.Argument(..., help="First display index."),
    idx2: int = typer.Argument(..., help="Second display index."),
):
    """Swap the display positions of two entries. Alias: `koda w`."""
    init_db()
    if idx1 == idx2:
        return
    with sqlite3.connect(DB_PATH) as conn:
        a = conn.execute("SELECT id FROM memos WHERE idx = ?", (idx1,)).fetchone()
        b = conn.execute("SELECT id FROM memos WHERE idx = ?", (idx2,)).fetchone()
        if a is None:
            console.print(f"[red]No entry at index {idx1}.[/red]")
            raise typer.Exit(code=1)
        if b is None:
            console.print(f"[red]No entry at index {idx2}.[/red]")
            raise typer.Exit(code=1)
        # Use -1 as temp to avoid UNIQUE constraint conflict
        conn.execute("UPDATE memos SET idx = -1 WHERE id = ?", (a[0],))
        conn.execute("UPDATE memos SET idx = ? WHERE id = ?", (idx1, b[0]))
        conn.execute("UPDATE memos SET idx = ? WHERE id = ?", (idx2, a[0]))
    console.print(f"[green]Swapped {idx1} ↔ {idx2}.[/green]")


@app.command(name="compact")
def compact_indices():
    """Fill index gaps by reassigning idx to contiguous values from 0. Alias: `koda k`."""
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, idx FROM memos ORDER BY idx ASC, id ASC").fetchall()
        if not rows:
            console.print("[yellow]No entries in database.[/yellow]")
            return

        changed = sum(1 for new_idx, (_, old_idx) in enumerate(rows) if old_idx != new_idx)
        if changed == 0:
            console.print("[green]Indices are already contiguous from 0.[/green]")
            return

        max_idx = max(idx for _, idx in rows)
        offset = max_idx + len(rows) + 1

        # Temporary offset avoids UNIQUE collisions while reassigning idx.
        conn.execute("UPDATE memos SET idx = idx + ?", (offset,))
        conn.executemany(
            "UPDATE memos SET idx = ? WHERE id = ?",
            [(new_idx, memo_id) for new_idx, (memo_id, _) in enumerate(rows)],
        )

    console.print(
        f"[green]Compacted indices for {changed} entr{'y' if changed == 1 else 'ies'}.[/green]"
    )


# ── config subcommand ─────────────────────────────────────────────────────────

config_app = typer.Typer(
    name="config",
    help="View and modify Koda configuration. Alias: `koda g`.",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(config_app, name="config")


@config_app.callback(invoke_without_command=True)
def config_show(ctx: typer.Context) -> None:
    """Show all settings with their current values and source."""
    if ctx.invoked_subcommand is not None:
        return
    key_width = max(len(k) for k in _ALL_KEYS)
    src_labels = {
        "default": "[dim]default[/dim]",
        "file":    "[green]file[/green]",
        "env":     "[cyan]env[/cyan]",
    }
    for sec, vals in CONFIG_DEFAULTS.items():
        for subkey in vals:
            dotkey = f"{sec}.{subkey}"
            val = _config[sec][subkey]
            src = _config_sources.get(dotkey, "default")
            label = src_labels.get(src, "[dim]default[/dim]")
            console.print(f"  {dotkey:<{key_width}} = {str(val):<24} {label}")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config key (e.g. defaults.cmd)."),
) -> None:
    """Print a single config value (plain text, for scripting)."""
    if key not in _ALL_KEYS:
        console.print(
            f"[red]Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}[/red]"
        )
        raise typer.Exit(code=1)
    sec, subkey = key.split(".", 1)
    sys.stdout.write(str(_config[sec][subkey]) + "\n")


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(..., help="Config key (e.g. list.per_page)."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Write a setting to the config file."""
    if key not in _ALL_KEYS:
        console.print(
            f"[red]Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}[/red]"
        )
        raise typer.Exit(code=1)
    coerced = _coerce_config_value(key, value)
    validator = _CONFIG_VALIDATORS.get(key)
    if validator:
        fn, msg = validator
        if not fn(coerced):
            console.print(f"[red]Invalid value for {key!r}: {msg}[/red]")
            raise typer.Exit(code=1)
    sec, subkey = key.split(".", 1)
    file_data = _read_config_file()
    if sec not in file_data:
        file_data[sec] = {}
    file_data[sec][subkey] = coerced
    _write_config_file(file_data)
    console.print(f"[green]Set {key} = {coerced!r}[/green]")


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(..., help="Config key to remove from the file."),
) -> None:
    """Remove a key from the config file (reverts to default)."""
    if key not in _ALL_KEYS:
        console.print(
            f"[red]Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}[/red]"
        )
        raise typer.Exit(code=1)
    sec, subkey = key.split(".", 1)
    file_data = _read_config_file()
    if sec not in file_data or subkey not in file_data[sec]:
        console.print(f"[yellow]{key} is not set in the config file.[/yellow]")
        return
    del file_data[sec][subkey]
    if not file_data[sec]:
        del file_data[sec]
    _write_config_file(file_data)
    default_val = CONFIG_DEFAULTS[sec][subkey]
    console.print(f"[green]Unset {key} (reverts to default: {default_val!r})[/green]")


@config_app.command("reset")
def config_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Reset without prompting."),
) -> None:
    """Delete the config file, reverting all settings to defaults."""
    if not CONFIG_PATH.exists():
        console.print("[yellow]No config file found.[/yellow]")
        return
    if not force:
        if not sys.stdin.isatty():
            console.print("[red]Not a TTY: use -f/--force to reset without a prompt.[/red]")
            raise typer.Exit(code=1)
        try:
            reply = input(f"Delete config file at {CONFIG_PATH}? [y/N]: ").strip().lower()
        except EOFError:
            console.print("\n[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
        if reply not in ("y", "yes"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
    CONFIG_PATH.unlink()
    console.print(f"[green]Config reset (deleted {CONFIG_PATH}).[/green]")


@config_app.command("edit")
def config_edit_cmd() -> None:
    """Open the config file in $EDITOR."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        template = (
            "# Koda configuration\n"
            "# Uncomment and edit values to override defaults.\n\n"
            "# [defaults]\n"
            '# cmd = "raw"      # "raw" or "list"\n\n'
            "# [list]\n"
            "# per_page = 20\n"
            "# rows = 1         # 0 = all lines\n"
            "# truncate = 80    # 0 = no truncation\n"
            '# sort_by = "idx"\n'
            "# desc = false\n\n"
            "# [db]\n"
            f'# path = "{DEFAULT_DB_PATH}"\n\n'
            "# [exec]\n"
            '# shell = "sh"\n'
        )
        CONFIG_PATH.write_text(template, encoding="utf-8")
    editor = os.environ.get("EDITOR", "vim")
    subprocess.call([editor, str(CONFIG_PATH)])


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the path to the config file."""
    sys.stdout.write(str(CONFIG_PATH) + "\n")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    try:
        app()
    except Exception as e:
        console.print(f"[red]Fatal Error:[/red] {e}")
        sys.exit(1)
