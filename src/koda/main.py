import typer
import sys
import hashlib
from typer.core import TyperGroup
import os
import subprocess
import tempfile
import re
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from typing import Optional, List
from rich.console import Console
from rich.table import Table

from .db import MemoDatabase, DatabaseError, IntegrityErrors as _IntegrityErrors
from .cli_utils import confirm, exit_error
from .config import (
    ALL_KEYS as _ALL_KEYS,
    COLUMN_DEFS,
    CONFIG_PATH,
    ConfigManager,
    DEFAULT_DB_PATH,
    GIT_SYNC_FORMAT_JSONL,
    VALID_LIST_COLUMNS,
    VALID_SORT_COLUMNS,
    ValidationError,
)
from .cmd_helpers.display import print_memo as _print_memo
from .cmd_helpers.metadata import first_footer_index, last_footer_segment
from .cmd_helpers.parsing import parse_indices, parse_tag_args, parse_var_items
from .cmd_helpers.interactive import (
    pick_candidates,
    pick_with_fzf,
    resolve_pick_action,
)
from . import git_sync
from .constants import DATETIME_FMT, IDX_TEMP_OFFSET, TAG_SEPARATOR

__app_name__ = "koda"
__version__ = version("koda-cli")


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
            cmd_name = str(args[0])
            if cmd_name in ALIASES:
                args = [ALIASES[cmd_name]] + list(args[1:])
            elif self.get_command(ctx, cmd_name) is None and not cmd_name.startswith("-"):
                default_cmd = config.defaults_cmd
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

_config_manager = ConfigManager()
config, _config_sources = _config_manager.load()
DB_PATH = Path(config.db_path).expanduser()


def _validate_list_columns(columns: List[str], source: str) -> None:
    try:
        ConfigManager.validate("list.columns", columns)
    except ValidationError:
        exit_error(f"Invalid {source}: {ConfigManager.error_message('list.columns')}")

db = MemoDatabase(
    backend=config.db_backend,
    path=DB_PATH,
    turso_url=config.turso_url,
    turso_token=config.turso_token,
)


def version_callback(value: bool):
    if value:
        console.print(f"{__app_name__} version: [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()


def _generate_uid(content: str, created_at: str) -> str:
    raw = f"{content}{created_at}".encode()
    return hashlib.sha1(raw).hexdigest()[:7]


def init_db():
    try:
        db.init_db()
    except typer.Exit:
        raise
    except DatabaseError as e:
        exit_error(str(e))
    except Exception as e:
        exit_error(f"Database Error: {e}")


def resolve_ref(ref: Optional[str]):
    """Return (id, uid, idx, content, tags, shortcut, created_at) or exit.

    ref=None → latest; digit string → idx lookup; other string → shortcut lookup.
    """
    if ref is None:
        row = db.get_latest_entry()
        if row is None:
            exit_error("No entries in database.", style="yellow")
        return row
    if ref.isdigit():
        row = db.get_memo_by_idx(int(ref))
        if row is None:
            exit_error(f"No entry at index {ref}.", style="yellow")
        return row
    row = db.get_memo_by_shortcut(ref)
    if row is None:
        exit_error(f"No entry with shortcut {ref!r}.", style="yellow")
    return row


def _validate_shortcut(shortcut: Optional[str]) -> Optional[str]:
    if shortcut and len(shortcut) == 1 and shortcut in RESERVED_SHORTCUTS:
        exit_error(f"Shortcut {shortcut!r} is reserved as a 1-letter subcommand alias.")
    return shortcut


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
            for item in parse_var_items(stripped):
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
    content = _apply_vars(row.content if row.content is not None else "", vars)
    content = _strip_raw_inline_comments(content)
    # POSIX text files end in a newline; append one when the body is non-empty
    # and not already newline-terminated so `koda raw | wc -l` and friends work.
    if content and not content.endswith("\n"):
        content += "\n"
    sys.stdout.write(content)


def _read_stdin_refs() -> List[str]:
    """Read whitespace-separated entry refs from stdin (non-interactive only)."""
    if sys.stdin.isatty():
        return []
    data = sys.stdin.read().strip()
    if not data:
        return []
    return [part for part in data.split() if part]

def _run_pick_action(action: str, ref: str) -> None:
    if action == "raw":
        emit_raw(ref)
        return
    if action == "show":
        init_db()
        row = resolve_ref(ref)
        _print_memo(row.uid, row.idx, row.shortcut, row.content, row.tags, row.created_at)
        return
    if action == "edit":
        edit(ref)
        return
    if action == "exec":
        exec_memo(ref, None)
        return
    exit_error(f"Unsupported pick action: {action}")


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
        cmd = config.defaults_cmd
        if cmd == "list":
            _list_memos_impl()
        elif cmd == "show":
            init_db()
            row = resolve_ref(None)
            _print_memo(row.uid, row.idx, row.shortcut, row.content, row.tags, row.created_at)
        elif cmd == "add":
            _add_impl()
        else:
            emit_raw(None)


def update_memo_full(memo_id: int, content: str, tags: str, shortcut: Optional[str], created_at: str):
    now = datetime.now().strftime(DATETIME_FMT)
    db.update_memo(memo_id, content, tags, shortcut, created_at, now)


def _add_impl(
    text: Optional[List[str]] = None,
    tag: Optional[List[str]] = None,
    shortcut: Optional[str] = None,
) -> None:
    shortcut = _validate_shortcut(shortcut)
    init_db()
    content = ""

    if text:
        content = " ".join(text)
        if not sys.stdin.isatty() and sys.stdin.read().strip():
            print(
                "Warning: ignoring piped stdin because text arguments were given.",
                file=sys.stderr,
            )
    elif not sys.stdin.isatty():
        content = sys.stdin.read().strip()
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
        exit_error("Aborted: Empty content.", style="yellow")

    content = content.encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')

    formatted_tags = TAG_SEPARATOR.join(dict.fromkeys(parse_tag_args(tag)))

    now = datetime.now().strftime(DATETIME_FMT)
    uid = _generate_uid(content, now)
    try:
        with db.connection() as conn:
            new_idx = MemoDatabase.next_idx(conn)
            conn.execute(
                "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, new_idx, shortcut or None, content, formatted_tags, now, now)
            )
    except _IntegrityErrors:
        exit_error(f"Shortcut {shortcut!r} is already in use.")

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
        exit_error("--all requires -f/--force.")

    init_db()
    is_batch = bool(tag or query or all_entries or (indices and (len(indices) > 1 or re.search(r'\d-\d', indices[0]))))

    if is_batch:
        if indices:
            idx_list = parse_indices(indices)
            target_rows = []
            for idx in idx_list:
                row = db.get_memo_by_idx(idx)
                if row is None:
                    console.print(f"[yellow]No entry at index {idx}, skipping.[/yellow]")
                else:
                    target_rows.append(row)
        else:
            target_rows = db.get_memos_all(query=query, tag=tag, sort_by="idx")

        if not target_rows:
            console.print("[yellow]No matching entries.[/yellow]")
            return

        n = len(target_rows)
        console.print(f"\n[bold red]About to delete {n} entr{'y' if n == 1 else 'ies'}:[/bold red]")
        for row in target_rows[:10]:
            preview = (row.content or "").splitlines()[0][:60]
            console.print(f"  [{row.idx}] ({row.uid}) {preview}")
        if n > 10:
            console.print(f"  ... and {n - 10} more")

        if not force and not confirm(f"\nDelete {n} entr{'y' if n == 1 else 'ies'}?"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)

        ids = [row.id for row in target_rows]
        with db.connection() as conn:
            conn.executemany("DELETE FROM memos WHERE id = ?", [(id_,) for id_ in ids])
        console.print(f"[red]Deleted {n} entr{'y' if n == 1 else 'ies'}.[/red]")

    else:
        ref = indices[0] if indices else None
        row = resolve_ref(ref)
        _print_memo(row.uid, row.idx, row.shortcut, row.content, row.tags, row.created_at)
        console.print("\n[bold red]This entry will be deleted.[/bold red]")

        if not force and not confirm("Delete this entry?"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)

        db.delete_memo(row.id)
        preview = row.content.splitlines()[0][:50] if row.content else ""
        console.print(f"[red]Deleted [{row.idx}]: {preview}...[/red]")


@app.command(name="copy")
def copy(
    ref: Optional[str] = typer.Argument(
        None, help="Source entry index or shortcut (default: latest)."
    ),
):
    """Duplicate an entry to a new row (same body and tags, no shortcut). Alias: `koda c`."""
    init_db()
    row = resolve_ref(ref)
    now = datetime.now().strftime(DATETIME_FMT)
    new_uid = _generate_uid(row.content or "", now)
    with db.connection() as conn:
        new_idx = MemoDatabase.next_idx(conn)
        conn.execute(
            "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_uid, new_idx, None, row.content, row.tags, now, now)
        )
    console.print(f"[green]Copied [{row.idx}] → [{new_idx}] ({new_uid}).[/green]")


@app.command()
def edit(
    ref: Optional[str] = typer.Argument(
        None, help="Entry index or shortcut to edit (default: latest)."
    ),
):
    """Open an entry in $EDITOR (body plus tags/shortcut/metadata footer). Alias: `koda e`."""
    init_db()
    row = resolve_ref(ref)
    memo_id = row.id
    content, tags, shortcut, created_at, idx = (
        row.content, row.tags, row.shortcut, row.created_at, row.idx,
    )

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

        footer_at = first_footer_index(parts)
        if footer_at is not None:
            new_content = "\n---\n".join(parts[:footer_at]).strip()
            meta_section = last_footer_segment(parts) or ""
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
            except _IntegrityErrors:
                exit_error(f"Shortcut {new_shortcut!r} is already in use.")
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
    columns: Optional[List[str]] = None,
) -> None:
    init_db()

    if columns is None:
        columns = config.list_columns
        _validate_list_columns(columns, "list.columns")
    if per_page is None:
        per_page = config.list_per_page
    elif per_page < 1:
        exit_error("--per-page must be >= 1.")
    if sort_by is None:
        sort_by = config.list_sort_by
    if desc is None:
        desc = config.list_desc
    if rows is None:
        rows = str(config.list_rows)
    if truncate is None:
        truncate = config.list_truncate
    elif truncate < 0:
        exit_error("--truncate must be 0 or greater.")

    normalized_sort = sort_by.lower()
    if normalized_sort not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        exit_error(f"Invalid --sort-by '{sort_by}'. Use one of: {valid}.")

    rows_value: Optional[int]
    try:
        parsed_rows = int(rows)
        if parsed_rows < 0:
            raise ValueError
    except ValueError:
        exit_error("--rows must be an integer of 0 or greater.")
    rows_value = None if parsed_rows == 0 else parsed_rows

    total_count, max_idx = db.get_memo_stats(query, tag, exclude_tag, shortcuts_only)
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
    memos = db.get_memos(
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
    for col in columns:
        label, kwargs = COLUMN_DEFS[col]
        table.add_column(label, **kwargs)

    row_values: dict = {}
    for memo in memos:
        content_lines = (memo.content or "").splitlines()
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
        row_values = {
            "idx": str(memo.idx),
            "uid": memo.uid or "",
            "sc": memo.shortcut or "",
            "tags": memo.tags or "",
            "content": preview,
            "created_at": memo.created_at,
        }
        table.add_row(*[row_values[col] for col in columns])
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
    columns: Optional[str] = typer.Option(
        None, "--columns",
        help=(
            "Comma-separated columns to display. idx is required. "
            f"Available: {', '.join(VALID_LIST_COLUMNS)}. [config: list.columns]"
        ),
    ),
):
    """Show entries as a table with paging and sortable columns. Alias: `koda l`."""
    parsed_columns: Optional[List[str]] = None
    if columns is not None:
        parsed_columns = [c.strip() for c in columns.split(",") if c.strip()]
        _validate_list_columns(parsed_columns, "--columns")
    _list_memos_impl(query, tag, exclude_tag, shortcuts_only, per_page, page, sort_by, desc, rows, truncate, parsed_columns)


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
        exit_error("--print-id/-p cannot be combined with action flags.")

    action: Optional[str] = None if print_id else resolve_pick_action(
        config, edit_mode, exec_mode, raw_mode, show_mode, print_id
    )

    init_db()
    candidates = pick_candidates(db, config, query, tag, exclude_tag, shortcuts_only, sort_by, desc)
    if not candidates:
        exit_error("No entries found.", style="yellow")

    selected_ref = pick_with_fzf(candidates)
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
            exit_error("show accepts one ref from stdin. Got multiple values.")
        if stdin_refs:
            ref = stdin_refs[0]

    init_db()
    row = resolve_ref(ref)
    _print_memo(row.uid, row.idx, row.shortcut, row.content, row.tags, row.created_at)


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
            exit_error("ex accepts one ref from stdin. Got multiple values.")
        if stdin_refs:
            ref = stdin_refs[0]

    init_db()
    row = resolve_ref(ref)
    content = _apply_vars(row.content.strip() if row.content else "", vars)
    shell = config.exec_shell
    try:
        ConfigManager.validate("exec.shell", shell)
    except ValidationError:
        exit_error(
            f"Refusing to exec: exec.shell {shell!r} is not allowed "
            f"({ConfigManager.error_message('exec.shell')})."
        )
    os.execvp(shell, [shell, "-c", content])


@app.command()
def tag(
    indices: List[str] = typer.Argument(..., help="Entry indices or ranges (e.g. 1 3 5-8)."),
    tags: Optional[List[str]] = typer.Option(None, "--tag", "-t", help="Tag(s) to add."),
    untag: Optional[List[str]] = typer.Option(None, "--untag", "-T", help="Tag(s) to remove."),
):
    """Add or remove tags on one or more entries. Supports ranges (e.g. 2-5). Alias: `koda t`."""
    if not tags and not untag:
        exit_error("Specify at least one of -t/--tag (add) or -T/--untag (remove).")

    init_db()
    idx_list = parse_indices(indices)
    add_list = parse_tag_args(tags)
    remove_list = parse_tag_args(untag)

    updated = 0
    with db.connection() as conn:
        for idx in idx_list:
            row = conn.execute("SELECT id, tags FROM memos WHERE idx = ?", (idx,)).fetchone()
            if row is None:
                console.print(f"[yellow]No entry at index {idx}, skipping.[/yellow]")
                continue
            row_id, current_tags = row
            current = [t for t in (current_tags or "").split(TAG_SEPARATOR) if t.strip()]
            new_tags = [t for t in current if t not in remove_list]
            new_tags = new_tags + [t for t in add_list if t not in new_tags]
            conn.execute(
                "UPDATE memos SET tags = ? WHERE id = ?",
                (TAG_SEPARATOR.join(new_tags), row_id),
            )
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
    with db.connection() as conn:
        if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (from_idx,)).fetchone() is None:
            exit_error(f"No entry at index {from_idx}.")
        if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (to_idx,)).fetchone() is not None:
            console.print(f"[red]Index {to_idx} is already occupied.[/red]")
            console.print(
                f"[dim]Hint: `koda swap {from_idx} {to_idx}` to swap, "
                f"or `koda shift {to_idx}` to make room first.[/dim]"
            )
            raise typer.Exit(code=1)
        conn.execute("UPDATE memos SET idx = ? WHERE idx = ?", (to_idx, from_idx))
    console.print(f"[green]Moved {from_idx} → {to_idx}.[/green]")


@app.command()
def push(
    payload_file: Optional[Path] = typer.Option(
        None, "--file", help="Use this JSONL file instead of exporting the local database."
    ),
):
    """Write memo export (JSON Lines, uid-sorted) into the Git clone, commit, and push. Alias: `koda push`."""
    init_db()
    git_sync.require_jsonl_format(config)
    git_sync.require_git_cli()
    sync_root = git_sync.resolve_sync_root(config)
    repo = git_sync.GitSyncRepo(sync_root)
    repo.ensure_worktree()
    payload_path = git_sync.resolve_payload_path(config, sync_root)
    rel = payload_path.relative_to(sync_root).as_posix()

    if payload_file is not None:
        if not payload_file.is_file():
            exit_error(f"--file does not exist: {payload_file}")
        data = payload_file.read_bytes()
        try:
            _ = git_sync.GitSyncPayload.load(data)
        except Exception as e:
            exit_error(f"Invalid sync payload: {e}")
    else:
        data = git_sync.GitSyncPayload.dump(db)

    repo.pull_rebase_if_remote()

    git_sync.atomic_write_bytes(payload_path, data)
    subprocess.run(["git", "-C", str(sync_root), "add", "-f", rel], check=True)
    chk = subprocess.run(["git", "-C", str(sync_root), "diff", "--cached", "--quiet"])
    if chk.returncode == 0:
        console.print("[yellow]Payload unchanged — nothing to commit.[/yellow]")
        repo.push_if_remote()
        console.print("[green]Push complete.[/green]")
        return

    try:
        subprocess.run(
            ["git", "-C", str(sync_root), "commit", "-m", "koda: sync memo payload"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(
            "[red]git commit failed. Resolve working tree issues in the sync clone and retry.[/red]"
        )
        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")
        raise typer.Exit(code=1)
    repo.push_if_remote()

    console.print(f"[green]Synced payload to Git: {payload_path}[/green]")


@app.command()
def pull(
    local_payload_path: Optional[Path] = typer.Option(
        None, "--file", help="Import from this JSONL file (skip git pull in the clone)."
    ),
):
    """Pull memo JSONL from the Git clone (--file skips git); merge into local DB (uid + modified_at). Alias: `koda pull`."""
    init_db()
    git_sync.require_jsonl_format(config)
    if local_payload_path is not None:
        if not local_payload_path.is_file():
            exit_error(f"--file does not exist: {local_payload_path}")
        data = local_payload_path.read_bytes()
    else:
        git_sync.require_git_cli()
        sync_root = git_sync.resolve_sync_root(config)
        repo = git_sync.GitSyncRepo(sync_root)
        repo.ensure_worktree()
        payload_path = git_sync.resolve_payload_path(config, sync_root)
        repo.pull_rebase_if_remote()
        if not payload_path.is_file():
            exit_error(f"Payload file missing after pull: {payload_path}")
        data = payload_path.read_bytes()

    try:
        rows = git_sync.GitSyncPayload.load(data)
    except Exception as e:
        exit_error(f"Invalid sync payload: {e}")

    ins, upd, skp, dsc = git_sync.MemoMerger(db).merge(rows)
    tail = f", [yellow]{dsc}[/yellow] shortcut(s) dropped (conflicts with local shortcuts)" if dsc else ""
    console.print(
        f"merged remote memos: [cyan]{ins}[/cyan] inserted, [cyan]{upd}[/cyan] updated, "
        f"[dim]{skp}[/dim] skipped (older or invalid entries){tail}."
    )
    console.print("[green]Pull complete.[/green]")


@app.command(name="shift")
def shift_cmd(
    start: int = typer.Argument(..., help="Shift entries at this index and above."),
    count: int = typer.Option(1, "--count", "-n", help="Positions to shift (negative = shift down)."),
):
    """Shift all entries at START and above by COUNT positions. Alias: `koda h`."""
    init_db()
    if count == 0:
        return
    with db.connection() as conn:
        if count < 0:
            if start + count < 0:
                exit_error(
                    f"Cannot shift down by {abs(count)}: "
                    f"index {start} would become {start + count} (negative indices not allowed)."
                )
            collision = conn.execute(
                "SELECT 1 FROM memos WHERE idx >= ? AND idx < ?",
                (start + count, start),
            ).fetchone()
            if collision:
                exit_error(
                    f"Cannot shift down by {abs(count)}: "
                    f"entries exist in [{start + count}, {start - 1}]."
                )
        # Two-step update to avoid UNIQUE constraint violations during bulk shift
        conn.execute(
            "UPDATE memos SET idx = idx + ? WHERE idx >= ?", (IDX_TEMP_OFFSET, start),
        )
        conn.execute(
            "UPDATE memos SET idx = idx - ? + ? WHERE idx >= ?",
            (IDX_TEMP_OFFSET, count, IDX_TEMP_OFFSET + start),
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
    with db.connection() as conn:
        a = conn.execute("SELECT id FROM memos WHERE idx = ?", (idx1,)).fetchone()
        b = conn.execute("SELECT id FROM memos WHERE idx = ?", (idx2,)).fetchone()
        if a is None:
            exit_error(f"No entry at index {idx1}.")
        if b is None:
            exit_error(f"No entry at index {idx2}.")
        # Use -1 as temp to avoid UNIQUE constraint conflict
        conn.execute("UPDATE memos SET idx = -1 WHERE id = ?", (a[0],))
        conn.execute("UPDATE memos SET idx = ? WHERE id = ?", (idx1, b[0]))
        conn.execute("UPDATE memos SET idx = ? WHERE id = ?", (idx2, a[0]))
    console.print(f"[green]Swapped {idx1} ↔ {idx2}.[/green]")


@app.command(name="compact")
def compact_indices():
    """Fill index gaps by reassigning idx to contiguous values from 0. Alias: `koda k`."""
    init_db()
    with db.connection() as conn:
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
    for dotkey in _ALL_KEYS:
        val = ConfigManager.get(config, dotkey)
        src = _config_sources.get(dotkey, "default")
        label = src_labels.get(src, "[dim]default[/dim]")
        display_val = "****" if dotkey == "turso.token" and val else str(val)
        console.print(f"  {dotkey:<{key_width}} = {display_val:<24} {label}")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config key (e.g. defaults.cmd)."),
) -> None:
    """Print a single config value (plain text, for scripting)."""
    if key not in _ALL_KEYS:
        exit_error(
            f"Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}"
        )
    sys.stdout.write(str(ConfigManager.get(config, key)) + "\n")


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(..., help="Config key (e.g. list.per_page)."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Write a setting to the config file."""
    if key not in _ALL_KEYS:
        exit_error(
            f"Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}"
        )
    try:
        coerced = ConfigManager.coerce(key, value)
        ConfigManager.validate(key, coerced)
    except ValidationError as e:
        exit_error(str(e))
    sec, subkey = key.split(".", 1)
    try:
        file_data = _config_manager.read_raw()
    except ValidationError as e:
        exit_error(str(e))
    if sec not in file_data:
        file_data[sec] = {}
    file_data[sec][subkey] = coerced
    _config_manager.write_raw(file_data)
    console.print(f"[green]Set {key} = {coerced!r}[/green]")


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(..., help="Config key to remove from the file."),
) -> None:
    """Remove a key from the config file (reverts to default)."""
    if key not in _ALL_KEYS:
        exit_error(
            f"Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}"
        )
    sec, subkey = key.split(".", 1)
    try:
        file_data = _config_manager.read_raw()
    except ValidationError as e:
        exit_error(str(e))
    if sec not in file_data or subkey not in file_data[sec]:
        console.print(f"[yellow]{key} is not set in the config file.[/yellow]")
        return
    del file_data[sec][subkey]
    if not file_data[sec]:
        del file_data[sec]
    _config_manager.write_raw(file_data)
    default_val = ConfigManager.default_for(key)
    console.print(f"[green]Unset {key} (reverts to default: {default_val!r})[/green]")


@config_app.command("reset")
def config_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Reset without prompting."),
) -> None:
    """Delete the config file, reverting all settings to defaults."""
    if not CONFIG_PATH.exists():
        console.print("[yellow]No config file found.[/yellow]")
        return
    if not force and not confirm(f"Delete config file at {CONFIG_PATH}?"):
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
            f'# path = "{DEFAULT_DB_PATH}"\n'
            '# backend = "local"   # "local" or "turso"\n\n'
            "# [turso]\n"
            '# url = "libsql://your-db.turso.io"   # or set KODA_TURSO_URL\n'
            '# token = "your-auth-token"            # or set KODA_TURSO_TOKEN\n\n'
            "# [git]\n"
            '# sync_path = "/path/to/koda-sync-repo"    # clone root, or use KODA_GIT_SYNC_PATH\n'
            '# payload_file = "koda-sync.jsonl"         # relative to sync_path (JSON Lines)\n'
            '# sync_format = "jsonl"                     # or KODA_GIT_SYNC_FORMAT\n\n'
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
    app()
