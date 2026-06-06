"""Git sync and payload I/O commands: push, pull, export, import."""

import subprocess
import sys
from pathlib import Path

import typer

from .. import git_sync
from ..cli_utils import exit_error
from ..main import app
from ..runtime import console, get_config, get_db, init_db


def _merge_payload(data: bytes) -> None:
    """Load a JSONL payload and merge it into the local DB, printing a summary."""
    try:
        rows = git_sync.GitSyncPayload.load(data)
    except Exception as e:
        exit_error(f"Invalid sync payload: {e}")

    ins, upd, skp, dsc = git_sync.MemoMerger(get_db()).merge(rows)
    tail = (
        f", [yellow]{dsc}[/yellow] shortcut(s) dropped (conflicts with local shortcuts)"
        if dsc
        else ""
    )
    console.print(
        f"merged memos: [cyan]{ins}[/cyan] inserted, [cyan]{upd}[/cyan] updated, "
        f"[dim]{skp}[/dim] skipped (older or invalid entries){tail}."
    )


@app.command()
def push(
    payload_file: Path | None = typer.Option(
        None, "--file", help="Use this JSONL file instead of exporting the local database."
    ),
):
    """Write memo export (JSON Lines, uid-sorted) into the Git clone, commit, and push.

    Alias: `koda push`.
    """
    init_db()
    git_sync.require_jsonl_format(get_config())
    git_sync.require_git_cli()
    sync_root = git_sync.resolve_sync_root(get_config())
    repo = git_sync.GitSyncRepo(sync_root)
    repo.ensure_worktree()
    payload_path = git_sync.resolve_payload_path(get_config(), sync_root)
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
        data = git_sync.GitSyncPayload.dump(get_db())

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
    local_payload_path: Path | None = typer.Option(
        None, "--file", help="Import from this JSONL file (skip git pull in the clone)."
    ),
):
    """Pull memo JSONL from the Git clone (--file skips git); merge into local DB.

    Merge key is uid + modified_at. Alias: `koda pull`.
    """
    init_db()
    git_sync.require_jsonl_format(get_config())
    if local_payload_path is not None:
        if not local_payload_path.is_file():
            exit_error(f"--file does not exist: {local_payload_path}")
        data = local_payload_path.read_bytes()
    else:
        git_sync.require_git_cli()
        sync_root = git_sync.resolve_sync_root(get_config())
        repo = git_sync.GitSyncRepo(sync_root)
        repo.ensure_worktree()
        payload_path = git_sync.resolve_payload_path(get_config(), sync_root)
        repo.pull_rebase_if_remote()
        if not payload_path.is_file():
            exit_error(f"Payload file missing after pull: {payload_path}")
        data = payload_path.read_bytes()

    _merge_payload(data)
    console.print("[green]Pull complete.[/green]")


@app.command()
def export(
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Write JSONL to this file instead of stdout."
    ),
):
    """Export all entries as JSON Lines (uid-sorted) to stdout or a file.

    The output is the same payload format used by `push` / `pull`.
    """
    init_db()
    data = git_sync.GitSyncPayload.dump(get_db())
    if out is not None:
        git_sync.atomic_write_bytes(out, data)
        console.print(f"[green]Exported to {out}.[/green]")
    else:
        sys.stdout.buffer.write(data)


@app.command(name="import")
def import_memos(
    file: Path = typer.Argument(..., help="JSONL file to import (merged by uid + modified_at)."),
):
    """Import entries from a local JSONL file, merging into the local database.

    Equivalent to `pull --file <file>` but without touching any Git clone.
    """
    init_db()
    if not file.is_file():
        exit_error(f"File does not exist: {file}")
    data = file.read_bytes()
    _merge_payload(data)
    console.print("[green]Import complete.[/green]")
