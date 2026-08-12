"""Display-index manipulation commands: move, swap, shift, compact.

``idx`` lives in each entry's Markdown frontmatter (the source of truth), so
these commands rewrite the affected ``.md`` files and reflect the change into
the cache. Reordering is not a content edit, so it never bumps ``modified_at``
and never changes trust (``sync_path(blessed=False)`` preserves local/remote).
"""

import typer

from ..cli_utils import exit_error
from ..main import app
from ..md_store import read_entry, write_entry
from ..models import MemoRow
from ..reconcile import sync_path
from ..runtime import console, get_db, get_entries_dir, init_db


def _rows_by_idx() -> tuple[list[MemoRow], dict[int, MemoRow]]:
    rows = get_db().get_memos(sort_by="idx")
    return rows, {r.idx: r for r in rows}


def _apply_idx(changes: dict[str, int]) -> None:
    """Rewrite frontmatter ``idx`` for each ``uid → new_idx`` and sync the cache.
    Uses ``blessed=False`` so a reorder preserves each entry's trust state."""
    entries_dir = get_entries_dir()
    db = get_db()
    for uid, new_idx in changes.items():
        rel = db.path_for(uid)
        if not rel:
            continue
        path = entries_dir / rel
        if not path.exists():
            continue
        entry = read_entry(path)
        if entry.idx == new_idx:
            continue
        entry.uid = uid
        entry.idx = new_idx
        write_entry(entries_dir, entry, path=path)
        sync_path(db, entries_dir, path, blessed=False)


@app.command(name="move", rich_help_panel="Index")
def move(
    from_idx: int = typer.Argument(..., help="Source display index."),
    to_idx: int = typer.Argument(..., help="Destination display index (must be empty)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would change without modifying entries."
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress the success message."),
):
    """Move entry at FROM to an unoccupied display position TO. Alias: `koda m`."""
    init_db()
    if from_idx == to_idx:
        return
    _, by_idx = _rows_by_idx()
    if from_idx not in by_idx:
        exit_error(f"No entry at index {from_idx}.")
    if to_idx in by_idx:
        console.print(f"[red]Index {to_idx} is already occupied.[/red]")
        console.print(
            f"[dim]Hint: `koda swap {from_idx} {to_idx}` to swap, "
            f"or `koda shift {to_idx}` to make room first.[/dim]"
        )
        raise typer.Exit(code=1)
    if dry_run:
        console.print(f"[cyan]Would move {from_idx} → {to_idx}.[/cyan]")
        return
    _apply_idx({by_idx[from_idx].uid: to_idx})
    if not quiet:
        console.print(f"[green]Moved {from_idx} → {to_idx}.[/green]")


@app.command(name="shift", rich_help_panel="Index")
def shift_cmd(
    start: int = typer.Argument(..., help="Shift entries at this index and above."),
    count: int = typer.Option(
        1, "--count", "-c", help="Positions to shift (negative = shift down)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would change without modifying entries."
    ),
):
    """Shift all entries at START and above by COUNT positions. Alias: `koda h`."""
    init_db()
    if count == 0:
        return
    rows, by_idx = _rows_by_idx()
    affected = [r for r in rows if r.idx >= start]
    if count < 0:
        if start + count < 0:
            exit_error(
                f"Cannot shift down by {abs(count)}: "
                f"index {start} would become {start + count} (negative indices not allowed)."
            )
        if any(start + count <= idx < start for idx in by_idx):
            exit_error(
                f"Cannot shift down by {abs(count)}: "
                f"entries exist in [{start + count}, {start - 1}]."
            )
    if dry_run:
        n = len(affected)
        console.print(
            f"[cyan]Would shift {n} entr{'y' if n == 1 else 'ies'} "
            f"from index {start} by {count:+d}.[/cyan]"
        )
        return
    _apply_idx({r.uid: r.idx + count for r in affected})
    console.print(f"[green]Shifted entries from index {start} by {count:+d}.[/green]")


@app.command(name="swap", rich_help_panel="Index")
def swap(
    idx1: int = typer.Argument(..., help="First display index."),
    idx2: int = typer.Argument(..., help="Second display index."),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress the success message."),
):
    """Swap the display positions of two entries. Alias: `koda w`."""
    init_db()
    if idx1 == idx2:
        return
    _, by_idx = _rows_by_idx()
    if idx1 not in by_idx:
        exit_error(f"No entry at index {idx1}.")
    if idx2 not in by_idx:
        exit_error(f"No entry at index {idx2}.")
    _apply_idx({by_idx[idx1].uid: idx2, by_idx[idx2].uid: idx1})
    if not quiet:
        console.print(f"[green]Swapped {idx1} ↔ {idx2}.[/green]")


@app.command(name="compact", rich_help_panel="Index")
def compact_indices(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would change without modifying entries."
    ),
):
    """Fill index gaps by reassigning idx to contiguous values from 0. Alias: `koda k`."""
    init_db()
    rows, _ = _rows_by_idx()
    if not rows:
        console.print("[yellow]No entries in database.[/yellow]")
        return
    changes = {r.uid: new_idx for new_idx, r in enumerate(rows) if r.idx != new_idx}
    if not changes:
        console.print("[green]Indices are already contiguous from 0.[/green]")
        return
    if dry_run:
        n = len(changes)
        console.print(f"[cyan]Would compact indices for {n} entr{'y' if n == 1 else 'ies'}.[/cyan]")
        return
    _apply_idx(changes)
    n = len(changes)
    console.print(f"[green]Compacted indices for {n} entr{'y' if n == 1 else 'ies'}.[/green]")
