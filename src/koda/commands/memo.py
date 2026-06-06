"""Memo CRUD and display commands: add, remove, copy, edit, list, show, raw, tag."""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import typer
from rich.table import Table

from ..cli_utils import confirm, exit_error
from ..cmd_helpers.display import print_memo as _print_memo
from ..cmd_helpers.metadata import first_footer_index, last_footer_segment
from ..cmd_helpers.parsing import parse_indices, parse_tag_args
from ..config import COLUMN_DEFS, VALID_LIST_COLUMNS, VALID_SORT_COLUMNS
from ..constants import DATETIME_FMT, TAG_SEPARATOR
from ..db import IntegrityErrors as _IntegrityErrors
from ..db import MemoDatabase, compute_uid
from ..main import RESERVED_SHORTCUTS, app
from ..runtime import (
    _read_stdin_refs,
    _validate_list_columns,
    console,
    emit_raw,
    get_config,
    get_db,
    init_db,
    resolve_ref,
)


def _generate_uid(content: str, created_at: str) -> str:
    return compute_uid(content, created_at)


def _validate_shortcut(shortcut: str | None) -> str | None:
    if shortcut and len(shortcut) == 1 and shortcut in RESERVED_SHORTCUTS:
        exit_error(f"Shortcut {shortcut!r} is reserved as a 1-letter subcommand alias.")
    return shortcut


def update_memo_full(memo_id: int, content: str, tags: str, shortcut: str | None, created_at: str):
    now = datetime.now().strftime(DATETIME_FMT)
    get_db().update_memo(memo_id, content, tags, shortcut, created_at, now)


def _add_impl(
    text: list[str] | None = None,
    tag: list[str] | None = None,
    shortcut: str | None = None,
    quiet: bool = False,
    print_uid: bool = False,
    print_idx: bool = False,
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
        editor = os.environ.get("EDITOR", "vim")
        with tempfile.NamedTemporaryFile(suffix=".tmp", mode="w+", delete=False) as tf:
            temp_path = tf.name
        try:
            subprocess.call([editor, temp_path])
            with open(temp_path) as f:
                content = f.read().strip()
        finally:
            Path(temp_path).unlink(missing_ok=True)

    if not content:
        exit_error("Aborted: Empty content.", style="yellow")

    content = content.encode("utf-8", "surrogateescape").decode("utf-8", "ignore")

    formatted_tags = TAG_SEPARATOR.join(dict.fromkeys(parse_tag_args(tag)))

    now = datetime.now().strftime(DATETIME_FMT)
    uid = _generate_uid(content, now)
    try:
        with get_db().connection() as conn:
            new_idx = MemoDatabase.next_idx(conn)
            conn.execute(
                "INSERT INTO memos "
                "(uid, idx, shortcut, content, tags, created_at, modified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, new_idx, shortcut or None, content, formatted_tags, now, now),
            )
    except _IntegrityErrors:
        exit_error(f"Shortcut {shortcut!r} is already in use.")

    if print_uid:
        sys.stdout.write(uid + "\n")
    if print_idx:
        sys.stdout.write(str(new_idx) + "\n")
    if not quiet:
        sc_str = f" sc=[bold green]{shortcut}[/bold green]" if shortcut else ""
        console.print(f"[green]Saved [{new_idx}] ({uid}) tags: {formatted_tags}{sc_str}[/green]")


@app.command()
def add(
    text: list[str] | None = typer.Argument(
        None, help="Text to save (optional if using stdin or $EDITOR)."
    ),
    tag: list[str] | None = typer.Option(
        None, "--tag", "-t", help="Comma-separated tag(s); repeat -t for more."
    ),
    shortcut: str | None = typer.Option(
        None, "--shortcut", "-s", help="Short alias for this entry (e.g. 'deploy')."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress the success message (for scripting)."
    ),
    print_uid: bool = typer.Option(
        False, "--print-uid", help="Print the new entry's uid to stdout."
    ),
    print_idx: bool = typer.Option(
        False, "--print-idx", help="Print the new entry's idx to stdout."
    ),
):
    """Create an entry from arguments, stdin, or your editor. Alias: `koda a`."""
    _add_impl(text, tag, shortcut, quiet=quiet, print_uid=print_uid, print_idx=print_idx)


@app.command(name="remove")
def rm(
    indices: list[str] | None = typer.Argument(
        None, help="Entry indices, ranges (e.g. 1 3 5-8), or a single shortcut. Default: latest."
    ),
    tag: str | None = typer.Option(
        None, "--tag", "-t", help="Delete entries whose tags match this substring."
    ),
    query: str | None = typer.Option(
        None, "--query", "-q", help="Delete entries whose body matches this substring."
    ),
    all_entries: bool = typer.Option(False, "--all", help="Delete ALL entries (requires -f)."),
    force: bool = typer.Option(False, "--force", "-f", help="Delete without prompting."),
):
    """Delete entries. Defaults to latest; supports ranges, -t, -q, and --all for batch.

    Alias: `koda d`.
    """
    if all_entries and not force:
        exit_error("--all requires -f/--force.")

    init_db()
    is_batch = bool(
        tag
        or query
        or all_entries
        or (indices and (len(indices) > 1 or re.search(r"\d-\d", indices[0])))
    )

    if is_batch:
        if indices:
            idx_list = parse_indices(indices)
            target_rows = []
            for idx in idx_list:
                row = get_db().get_memo_by_idx(idx)
                if row is None:
                    console.print(f"[yellow]No entry at index {idx}, skipping.[/yellow]")
                else:
                    target_rows.append(row)
        else:
            target_rows = get_db().get_memos(query=query, tag=tag, sort_by="idx")

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
        with get_db().connection() as conn:
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

        get_db().delete_memo(row.id)
        preview = row.content.splitlines()[0][:50] if row.content else ""
        console.print(f"[red]Deleted [{row.idx}]: {preview}...[/red]")


@app.command(name="copy")
def copy(
    ref: str | None = typer.Argument(
        None, help="Source entry index or shortcut (default: latest)."
    ),
):
    """Duplicate an entry to a new row (same body and tags, no shortcut). Alias: `koda c`."""
    init_db()
    row = resolve_ref(ref)
    now = datetime.now().strftime(DATETIME_FMT)
    new_uid = _generate_uid(row.content or "", now)
    with get_db().connection() as conn:
        new_idx = MemoDatabase.next_idx(conn)
        conn.execute(
            "INSERT INTO memos "
            "(uid, idx, shortcut, content, tags, created_at, modified_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_uid, new_idx, None, row.content, row.tags, now, now),
        )
    console.print(f"[green]Copied [{row.idx}] → [{new_idx}] ({new_uid}).[/green]")


@app.command()
def edit(
    ref: str | None = typer.Argument(
        None, help="Entry index or shortcut to edit (default: latest)."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the success message."),
):
    """Open an entry in $EDITOR (body plus tags/shortcut/metadata footer). Alias: `koda e`."""
    init_db()
    row = resolve_ref(ref)
    memo_id = row.id
    content, tags, shortcut, created_at, idx = (
        row.content,
        row.tags,
        row.shortcut,
        row.created_at,
        row.idx,
    )

    sc_line = f"shortcut: {shortcut}" if shortcut else "shortcut: "
    template = (
        f"{content}\n\n---\n# Metadata\ntags: {tags}\n{sc_line}\ncreated_at: {created_at}\n---"
    )

    editor = os.environ.get("EDITOR", "vim")
    with tempfile.NamedTemporaryFile(
        suffix=".tmp", mode="w+", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(template)
        temp_path = tf.name

    try:
        subprocess.call([editor, temp_path])
        with open(temp_path) as f:
            new_data = f.read()

        parts = re.split(r"\n---+\s*\n", new_data)
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
            if not quiet:
                console.print(f"[green]Entry [{idx}] updated.[/green]")
        else:
            new_content = "\n---\n".join(parts).strip() if parts else new_data.strip()
            update_memo_full(memo_id, new_content, tags, shortcut, created_at)
            if not quiet:
                console.print(
                    "[yellow]No metadata footer found; "
                    "content updated, metadata preserved.[/yellow]"
                )
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _emit_list_json(
    query: str | None,
    tag: str | None,
    exclude_tag: str | None,
    shortcuts_only: bool,
    sort_by: str | None,
    desc: bool | None,
) -> None:
    """Print all matching entries as a JSON array (no paging)."""
    init_db()
    if sort_by is None:
        sort_by = get_config().list_sort_by
    if desc is None:
        desc = get_config().list_desc
    normalized_sort = sort_by.lower()
    if normalized_sort not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        exit_error(f"Invalid --sort-by '{sort_by}'. Use one of: {valid}.")
    rows = get_db().get_memos(
        query,
        tag,
        exclude_tag,
        shortcuts_only,
        limit=None,
        sort_by=normalized_sort,
        desc=desc,
    )
    sys.stdout.write(json.dumps([r.to_dict() for r in rows], ensure_ascii=False, indent=2) + "\n")


def _list_memos_impl(
    query: str | None = None,
    tag: str | None = None,
    exclude_tag: str | None = None,
    shortcuts_only: bool = False,
    per_page: int | None = None,
    page: int = 1,
    sort_by: str | None = None,
    desc: bool | None = None,
    rows: str | None = None,
    truncate: int | None = None,
    columns: list[str] | None = None,
) -> None:
    init_db()

    if columns is None:
        columns = get_config().list_columns
        _validate_list_columns(columns, "list.columns")
    if per_page is None:
        per_page = get_config().list_per_page
    elif per_page < 1:
        exit_error("--per-page must be >= 1.")
    if sort_by is None:
        sort_by = get_config().list_sort_by
    if desc is None:
        desc = get_config().list_desc
    if rows is None:
        rows = str(get_config().list_rows)
    if truncate is None:
        truncate = get_config().list_truncate
    elif truncate < 0:
        exit_error("--truncate must be 0 or greater.")

    normalized_sort = sort_by.lower()
    if normalized_sort not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        exit_error(f"Invalid --sort-by '{sort_by}'. Use one of: {valid}.")

    rows_value: int | None
    try:
        parsed_rows = int(rows)
        if parsed_rows < 0:
            raise ValueError
    except ValueError:
        exit_error("--rows must be an integer of 0 or greater.")
    rows_value = None if parsed_rows == 0 else parsed_rows

    total_count, max_idx = get_db().get_memo_stats(query, tag, exclude_tag, shortcuts_only)
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
    memos = get_db().get_memos(
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
    ref: str | None = typer.Argument(
        None,
        help="If given, show that single entry (index or shortcut) instead of the table.",
    ),
    query: str | None = typer.Option(None, "--query", "-q", help="Substring match on memo body."),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Substring match on tags."),
    exclude_tag: str | None = typer.Option(
        None,
        "--exclude-tag",
        "-T",
        help="Exclude entries whose tags include this substring.",
    ),
    shortcuts_only: bool = typer.Option(
        False, "--shortcuts", "-S", help="Show only entries that have a shortcut."
    ),
    per_page: int | None = typer.Option(
        None, "--per-page", "-n", help="Entries per page. [config: list.per_page]"
    ),
    page: int = typer.Option(1, "--page", "-p", min=1, help="1-based page number to display."),
    sort_by: str | None = typer.Option(
        None,
        "--sort-by",
        "-s",
        case_sensitive=False,
        help=(
            "Sort column: id, idx, uid, tags, content, created_at, modified_at, shortcut. "
            "[config: list.sort_by]"
        ),
    ),
    desc: bool | None = typer.Option(
        None,
        "--desc/--asc",
        help="Sort order. [config: list.desc]",
    ),
    rows: str | None = typer.Option(
        None,
        "--rows",
        "-r",
        help="Content preview lines per entry (0 = all lines). [config: list.rows]",
    ),
    truncate: int | None = typer.Option(
        None,
        "--truncate",
        help="Max characters per content line (0 = no truncation). [config: list.truncate]",
    ),
    columns: str | None = typer.Option(
        None,
        "--columns",
        help=(
            "Comma-separated columns to display. idx is required. "
            f"Available: {', '.join(VALID_LIST_COLUMNS)}. [config: list.columns]"
        ),
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output all matching entries as a JSON array (ignores paging)."
    ),
):
    """Show entries as a table with paging and sortable columns. Alias: `koda l`.

    `koda list <idx|shortcut>` is shorthand for `koda show <idx|shortcut>`.
    """
    if ref is not None:
        show(ref, json_output=json_output)
        return
    if json_output:
        _emit_list_json(query, tag, exclude_tag, shortcuts_only, sort_by, desc)
        return
    parsed_columns: list[str] | None = None
    if columns is not None:
        parsed_columns = [c.strip() for c in columns.split(",") if c.strip()]
        _validate_list_columns(parsed_columns, "--columns")
    _list_memos_impl(
        query,
        tag,
        exclude_tag,
        shortcuts_only,
        per_page,
        page,
        sort_by,
        desc,
        rows,
        truncate,
        parsed_columns,
    )


@app.command()
def show(
    ref: str | None = typer.Argument(None, help="Entry index or shortcut (default: latest)."),
    json_output: bool = typer.Option(
        False, "--json", help="Output the entry as a single JSON object."
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
    if json_output:
        sys.stdout.write(json.dumps(row.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return
    _print_memo(row.uid, row.idx, row.shortcut, row.content, row.tags, row.created_at)


@app.command()
def raw(
    entry_refs: list[str] | None = typer.Argument(
        None,
        help=(
            "Entry index(es) or shortcut(s) (default: latest). "
            "Body only, for pipes and shell substitution."
        ),
    ),
    vars: list[str] | None = typer.Option(
        None,
        "--var",
        "-V",
        help=(
            "Variable substitution. Named: KEY=VALUE → replaces ${KEY}. "
            "Positional: VALUE → replaces $1,$2,... in order. "
            'Comma-separate multiple values; use "..." to include spaces or commas. '
            "Examples: -V 'localhost,5432' -V 'name=prod' -V '\"hello world\",\"foo,bar\"'"
        ),
    ),
):
    """Print memo body to stdout only (plain text, no Rich). Same as bare `koda <idx>`.

    Alias: `koda r`. When no argument is given, refs can also be passed from stdin.
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


@app.command()
def tag(
    indices: list[str] = typer.Argument(..., help="Entry indices or ranges (e.g. 1 3 5-8)."),
    tags: list[str] | None = typer.Option(None, "--tag", "-t", help="Tag(s) to add."),
    untag: list[str] | None = typer.Option(None, "--untag", "-T", help="Tag(s) to remove."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without modifying the database."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the success message."),
):
    """Add or remove tags on one or more entries. Supports ranges (e.g. 2-5). Alias: `koda t`."""
    if not tags and not untag:
        exit_error("Specify at least one of -t/--tag (add) or -T/--untag (remove).")

    init_db()
    idx_list = parse_indices(indices)
    add_list = parse_tag_args(tags)
    remove_list = parse_tag_args(untag)

    updated = 0
    added_count = 0
    removed_count = 0
    with get_db().connection() as conn:
        for idx in idx_list:
            row = conn.execute("SELECT id, tags FROM memos WHERE idx = ?", (idx,)).fetchone()
            if row is None:
                console.print(f"[yellow]No entry at index {idx}, skipping.[/yellow]")
                continue
            row_id, current_tags = row
            current = [t for t in (current_tags or "").split(TAG_SEPARATOR) if t.strip()]
            removed = [t for t in current if t in remove_list]
            kept = [t for t in current if t not in remove_list]
            added = [t for t in add_list if t not in kept]
            new_tags = kept + added
            if new_tags == current:
                continue
            if not dry_run:
                conn.execute(
                    "UPDATE memos SET tags = ? WHERE id = ?",
                    (TAG_SEPARATOR.join(new_tags), row_id),
                )
            updated += 1
            added_count += len(added)
            removed_count += len(removed)

    if quiet:
        return
    entry_word = "entry" if updated == 1 else "entries"
    verb = "Would update" if dry_run else "Updated"
    color = "cyan" if dry_run else "green"
    console.print(
        f"[{color}]{verb} {updated} {entry_word} "
        f"(added {added_count} tag{'' if added_count == 1 else 's'}, "
        f"removed {removed_count} tag{'' if removed_count == 1 else 's'}).[/{color}]"
    )
