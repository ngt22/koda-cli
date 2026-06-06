"""Display-index manipulation commands: move, swap, shift, compact."""

import typer

from ..cli_utils import exit_error
from ..constants import IDX_TEMP_OFFSET
from ..main import app
from ..runtime import console, get_db, init_db


@app.command(name="move")
def move(
    from_idx: int = typer.Argument(..., help="Source display index."),
    to_idx: int = typer.Argument(..., help="Destination display index (must be empty)."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without modifying the database."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the success message."),
):
    """Move entry at FROM to an unoccupied display position TO. Alias: `koda m`."""
    init_db()
    if from_idx == to_idx:
        return
    with get_db().connection() as conn:
        if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (from_idx,)).fetchone() is None:
            exit_error(f"No entry at index {from_idx}.")
        if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (to_idx,)).fetchone() is not None:
            console.print(f"[red]Index {to_idx} is already occupied.[/red]")
            console.print(
                f"[dim]Hint: `koda swap {from_idx} {to_idx}` to swap, "
                f"or `koda shift {to_idx}` to make room first.[/dim]"
            )
            raise typer.Exit(code=1)
        if dry_run:
            console.print(f"[cyan]Would move {from_idx} → {to_idx}.[/cyan]")
            return
        conn.execute("UPDATE memos SET idx = ? WHERE idx = ?", (to_idx, from_idx))
    if not quiet:
        console.print(f"[green]Moved {from_idx} → {to_idx}.[/green]")


@app.command(name="shift")
def shift_cmd(
    start: int = typer.Argument(..., help="Shift entries at this index and above."),
    count: int = typer.Option(
        1, "--count", "-n", help="Positions to shift (negative = shift down)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without modifying the database."
    ),
):
    """Shift all entries at START and above by COUNT positions. Alias: `koda h`."""
    init_db()
    if count == 0:
        return
    with get_db().connection() as conn:
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
        if dry_run:
            affected = conn.execute(
                "SELECT COUNT(*) FROM memos WHERE idx >= ?", (start,)
            ).fetchone()[0]
            console.print(
                f"[cyan]Would shift {affected} entr{'y' if affected == 1 else 'ies'} "
                f"from index {start} by {count:+d}.[/cyan]"
            )
            return
        # Two-step update to avoid UNIQUE constraint violations during bulk shift
        conn.execute(
            "UPDATE memos SET idx = idx + ? WHERE idx >= ?",
            (IDX_TEMP_OFFSET, start),
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
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress the success message."),
):
    """Swap the display positions of two entries. Alias: `koda w`."""
    init_db()
    if idx1 == idx2:
        return
    with get_db().connection() as conn:
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
    if not quiet:
        console.print(f"[green]Swapped {idx1} ↔ {idx2}.[/green]")


@app.command(name="compact")
def compact_indices(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would change without modifying the database."
    ),
):
    """Fill index gaps by reassigning idx to contiguous values from 0. Alias: `koda k`."""
    init_db()
    with get_db().connection() as conn:
        rows = conn.execute("SELECT id, idx FROM memos ORDER BY idx ASC, id ASC").fetchall()
        if not rows:
            console.print("[yellow]No entries in database.[/yellow]")
            return

        changed = sum(1 for new_idx, (_, old_idx) in enumerate(rows) if old_idx != new_idx)
        if changed == 0:
            console.print("[green]Indices are already contiguous from 0.[/green]")
            return

        if dry_run:
            console.print(
                f"[cyan]Would compact indices for {changed} "
                f"entr{'y' if changed == 1 else 'ies'}.[/cyan]"
            )
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
