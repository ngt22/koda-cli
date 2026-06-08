"""Git sync and payload I/O commands: push, pull, export, import."""

import subprocess
import sys
from pathlib import Path

import typer

from .. import git_sync
from ..cli_utils import exit_error
from ..main import app
from ..runtime import console, get_config, get_db, init_db


def _obtain_remote_payload(local_payload_path: Path | None) -> bytes:
    """Return payload bytes from a local --file or, failing that, the Git clone."""
    git_sync.require_jsonl_format(get_config())
    if local_payload_path is not None:
        if not local_payload_path.is_file():
            exit_error(f"--file does not exist: {local_payload_path}")
        return local_payload_path.read_bytes()
    git_sync.require_git_cli()
    sync_root = git_sync.resolve_sync_root(get_config())
    repo = git_sync.GitSyncRepo(sync_root)
    repo.ensure_worktree()
    payload_path = git_sync.resolve_payload_path(get_config(), sync_root)
    repo.pull_rebase_if_remote()
    if not payload_path.is_file():
        exit_error(f"Payload file missing after pull: {payload_path}")
    return payload_path.read_bytes()


def _preview(content: str, width: int = 50) -> str:
    """First line of ``content``, collapsed and truncated for one-line display."""
    first = (content or "").splitlines()[0] if content else ""
    return first if len(first) <= width else first[: width - 1] + "…"


def _print_merge_plan(data: bytes) -> None:
    """Show what a pull would insert/update without writing (`--dry-run`)."""
    try:
        rows = git_sync.GitSyncPayload.load(data)
    except Exception as e:
        exit_error(f"Invalid sync payload: {e}")

    plan = git_sync.MemoMerger(get_db()).plan(rows)
    inserts = [p for p in plan if p["action"] == "insert"]
    updates = [p for p in plan if p["action"] == "update"]
    skips = [p for p in plan if p["action"] == "skip"]

    if not (inserts or updates):
        console.print("[green]Nothing to merge — local is up to date with the payload.[/green]")
    for p in inserts:
        console.print(
            f"[green]+ insert[/green] {p['uid'][:12]}  [{p['idx']}]  {_preview(p['content'])}"
        )
    for p in updates:
        console.print(
            f"[yellow]~ update[/yellow] {p['uid'][:12]}  [{p['idx']}]  {_preview(p['content'])}"
        )
    console.print(
        f"[dim]{len(inserts)} insert, {len(updates)} update, {len(skips)} skip "
        f"— dry run, no changes written.[/dim]"
    )


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
        f"[dim]{skp}[/dim] skipped (older, future-dated, or invalid entries){tail}."
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
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show the insert/update diff without modifying the local database.",
    ),
):
    """Pull memo JSONL from the Git clone (--file skips git); merge into local DB.

    Merge key is uid + modified_at. Merged entries are marked source=remote and
    prompt before `koda x` runs them. Use --dry-run to preview the diff first.
    Alias: `koda pull`.
    """
    init_db()
    data = _obtain_remote_payload(local_payload_path)
    if dry_run:
        _print_merge_plan(data)
        return
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


@app.command()
def diff(
    local_payload_path: Path | None = typer.Option(
        None, "--file", help="Diff against this JSONL file instead of the Git clone."
    ),
):
    """Show a uid-level diff between the local database and the remote payload.

    Reports entries that are local-only, remote-only, or present in both but
    changed (different content, tags, shortcut, or modified_at).
    """
    init_db()
    data = _obtain_remote_payload(local_payload_path)
    try:
        remote_rows = git_sync.GitSyncPayload.load(data)
    except Exception as e:
        exit_error(f"Invalid sync payload: {e}")

    remote = {r["uid"]: r for r in remote_rows}
    local = {r.uid: r for r in get_db().get_memos(limit=None)}

    local_only = sorted(set(local) - set(remote))
    remote_only = sorted(set(remote) - set(local))
    changed = []
    for uid in set(local) & set(remote):
        lrow, rrow = local[uid], remote[uid]
        if (
            (lrow.content or "") != (rrow.get("content") or "")
            or (lrow.tags or "") != (rrow.get("tags") or "")
            or (lrow.shortcut or None) != (rrow.get("shortcut") or None)
            or (lrow.modified_at or "") != (rrow.get("modified_at") or "")
        ):
            changed.append(uid)
    changed.sort()

    if not (local_only or remote_only or changed):
        console.print("[green]No differences — local and remote are in sync.[/green]")
        return

    for uid in local_only:
        console.print(f"[green]+ local-only[/green]  {uid}  [{local[uid].idx}]")
    for uid in remote_only:
        console.print(f"[red]- remote-only[/red] {uid}")
    for uid in changed:
        console.print(f"[yellow]~ changed[/yellow]     {uid}  [{local[uid].idx}]")
    console.print(
        f"[dim]{len(local_only)} local-only, {len(remote_only)} remote-only, "
        f"{len(changed)} changed[/dim]"
    )


@app.command()
def backup(
    out: Path = typer.Option(
        ..., "--out", "-o", help="Destination file for the SQLite snapshot (must not exist)."
    ),
):
    """Write a consistent single-file snapshot of the local database (VACUUM INTO)."""
    init_db()
    if get_config().db_backend != "local":
        exit_error("backup is only supported for the local sqlite backend.")
    if out.exists():
        exit_error(f"Destination already exists: {out}")
    with get_db().connection() as conn:
        conn.execute("VACUUM INTO ?", (str(out),))
    console.print(f"[green]Backup written to {out}.[/green]")
