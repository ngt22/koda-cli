"""Resolve duplicate display indices: the chosen entry keeps the idx, the rest move to free ones."""

import shutil
import sys

import typer
from rich.text import Text

from ..cli_utils import ExitCode, exit_error
from ..cmd_helpers.display import first_line, format_conflict_entry
from ..cmd_helpers.interactive import run_fzf
from ..conflicts import clear_conflicts, load_conflicts
from ..main import app
from ..reconcile import annotate_groups, find_conflicts, snapshot_idx
from ..runtime import console, get_db, get_vault, init_db
from .index import _apply_idx


def _pick_winner_fzf(entries: list[dict]) -> str | None:
    lines = []
    for e in entries:
        first = first_line(e["content"]).replace("\t", " ")
        label = (e["title"] or first).replace("\t", " ")
        prev = e.get("prev_idx")
        prev_s = f"was:{prev}" if prev is not None else "-"
        lines.append(
            f"{e['uid']}\t{e['side'] or '?'}\t{e['shortcut'] or '-'}\t"
            f"{e['tags'] or '-'}\t{prev_s}\t{label}"
        )
    args = [
        "--delimiter",
        "\t",
        "--with-nth",
        "2,3,4,5,6",
        "--prompt",
        "keep> ",
        "--preview",
        "printf '%s\\n' '{6}'",
        "--no-multi",
    ]
    out = run_fzf(lines, args)
    if not out or not out.strip():
        return None
    return out.strip().split("\t", 1)[0]


def _pick_winner(idx: int, entries: list[dict]) -> str:
    console.print(f"\n[bold cyan]idx {idx}[/bold cyan] is held by {len(entries)} entries:")
    for i, e in enumerate(entries, 1):
        console.print(Text.assemble(f"  {i}. ", format_conflict_entry(e)))

    if shutil.which("fzf") and sys.stdin.isatty():
        uid = _pick_winner_fzf(entries)
        if uid:
            return uid
        console.print("[dim](fzf cancelled — falling back to numbered prompt)[/dim]")

    if not sys.stdin.isatty():
        exit_error(
            "Not a TTY: use `koda resolve --ours` or `--theirs` to resolve non-interactively.",
            code=ExitCode.CANCELLED,
        )
    while True:
        try:
            raw = input("Keep which entry at this idx? (1..N, or q to abort): ").strip()
        except EOFError:
            exit_error("Aborted.", code=ExitCode.CANCELLED)
        if raw.lower() in ("q", "quit"):
            exit_error("Aborted.", code=ExitCode.CANCELLED)
        if raw.isdigit() and 1 <= int(raw) <= len(entries):
            return entries[int(raw) - 1]["uid"]
        console.print("[yellow]Invalid choice — enter a number from the list.[/yellow]")


@app.command(rich_help_panel="Git sync")
def resolve(
    ours: bool = typer.Option(
        False, "--ours", help="Keep the local (ours) entry at each conflicted idx; move theirs."
    ),
    theirs: bool = typer.Option(
        False,
        "--theirs",
        help="Keep the incoming (theirs) entry at each conflicted idx; move ours.",
    ),
):
    """Resolve duplicate display indices (fzf / prompt, or --ours / --theirs), then `koda push`."""
    if ours and theirs:
        exit_error("Use only one of --ours or --theirs.", code=ExitCode.INVALID_ARG)

    init_db()
    vault = get_vault()
    db = get_db()

    groups = load_conflicts(vault)
    if not groups:
        groups = find_conflicts(db)
        if groups:
            annotate_groups(groups, snapshot_idx(db))
    if not groups:
        console.print("[green]No conflicts to resolve.[/green]")
        return

    next_free = db.allocate_idx()
    changes: dict[str, int] = {}
    report: list[tuple[int, dict, int]] = []

    for g in groups:
        idx = g["idx"]
        entries = g["entries"]

        if ours or theirs:
            want = "ours" if ours else "theirs"
            winners = [e for e in entries if e["side"] == want]
            if not winners:
                exit_error(
                    f"idx {idx}: no '{want}' side to keep — resolve this group "
                    f"interactively (`koda resolve`).",
                    code=ExitCode.INVALID_ARG,
                )
            winner = winners[0]
        else:
            winner_uid = _pick_winner(idx, entries)
            winner = next(e for e in entries if e["uid"] == winner_uid)

        for e in entries:
            if e["uid"] == winner["uid"]:
                report.append((idx, e, idx))  # kept
                continue
            new_idx = next_free
            next_free += 1
            changes[e["uid"]] = new_idx
            report.append((idx, e, new_idx))

    _apply_idx(changes)
    clear_conflicts(vault)

    for idx, e, new_idx in report:
        if new_idx == idx:
            console.print(
                Text.assemble(
                    f"  idx {idx}: ", Text("kept ", style="green"), format_conflict_entry(e)
                )
            )
        else:
            console.print(
                Text.assemble(
                    f"  idx {idx} → {new_idx}: ", format_conflict_entry(e), f" (uid {e['uid']})"
                )
            )
    console.print(f"\n[green]Resolved {len(groups)} conflict group(s).[/green]")
    console.print("[dim]Now run `koda push` to publish the resolved version.[/dim]")
