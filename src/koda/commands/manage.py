"""Store management: migrate (legacy SQLite → Markdown), reindex."""

import sqlite3
from pathlib import Path

import typer

from ..cli_utils import entries, exit_error, plural
from ..config import LEGACY_DB_PATH
from ..main import app
from ..md_store import MdEntry, write_entry
from ..models import compute_uid
from ..reconcile import reconcile_all
from ..runtime import console, get_db, get_entries_dir, init_db

_LEGACY_BASE_COLS = ["uid", "idx", "content", "tags", "shortcut", "created_at", "modified_at"]


@app.command(rich_help_panel="Data")
def migrate(
    db_path: Path = typer.Option(
        LEGACY_DB_PATH, "--from", help="Legacy koda.db to migrate (koda <= 1.x)."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Migrate even if the vault already has entries."
    ),
):
    """One-time migration: write one Markdown file per row of a legacy SQLite koda.db."""
    init_db()
    entries_dir = get_entries_dir()
    src = Path(db_path).expanduser()
    if not src.is_file():
        exit_error(f"Legacy database not found: {src}")
    if not force and any(entries_dir.glob("*.md")):
        exit_error(f"Vault already has entries at {entries_dir}. Re-run with -f to migrate anyway.")

    conn = sqlite3.connect(src)
    try:
        have = {row[1] for row in conn.execute("PRAGMA table_info(memos)").fetchall()}
        cols = [c for c in _LEGACY_BASE_COLS if c in have]
        if "title" in have:
            cols.append("title")
        rows = conn.execute(
            f"SELECT {', '.join(cols)} FROM memos ORDER BY idx ASC, id ASC"
        ).fetchall()
    finally:
        conn.close()

    existing_uids = set(get_db().manifest().keys())
    count = 0
    skipped: list[dict] = []
    for row in rows:
        d = dict(zip(cols, row))
        content = d.get("content") or ""
        created_at = d.get("created_at")
        uid = d.get("uid") or compute_uid(content, created_at or "")
        if uid in existing_uids:
            skipped.append(
                {
                    "uid": uid,
                    "idx": d.get("idx"),
                    "shortcut": d.get("shortcut"),
                    "title": d.get("title"),
                }
            )
            continue
        entry = MdEntry(
            content=content,
            uid=uid,
            idx=d.get("idx"),
            shortcut=d.get("shortcut"),
            tags=d.get("tags") or "",
            title=d.get("title"),
            created_at=created_at,
            modified_at=d.get("modified_at") or created_at,
        )
        write_entry(entries_dir, entry)
        count += 1

    if skipped:
        _record_skipped(skipped)

    reconcile_all(get_db(), entries_dir, force=True)
    console.print(f"[green]Migrated {entries(count)} to {entries_dir}.[/green]")
    for s in skipped:
        console.print(
            f"[yellow]Skipped {s['uid']}[/yellow] "
            f"(idx {s['idx'] if s['idx'] is not None else '-'}): "
            f"uid already exists in the vault — no file written."
        )
    if skipped:
        console.print(
            f"[dim]Skipped {plural(len(skipped), 'duplicate entry', 'duplicate entries')} "
            "recorded as a koda entry (tag: migrate).[/dim]"
        )
    console.print(f"[dim]Open {entries_dir.parent} in Obsidian, or `koda list` to verify.[/dim]")


def _record_skipped(skipped: list[dict]) -> None:
    from .memo import _write_new_entry

    lines = [
        "These koda.db rows were skipped by `koda migrate` because the vault",
        "already has an entry with the same uid. Nothing was overwritten or",
        "deleted.",
        "",
        "| uid | idx | shortcut | title |",
        "| --- | --- | --- | --- |",
    ]
    for s in skipped:
        lines.append(
            f"| {s['uid']} | {s['idx'] if s['idx'] is not None else ''} | "
            f"{s['shortcut'] or ''} | {s['title'] or ''} |"
        )
    title = f"migrate: skipped {plural(len(skipped), 'duplicate entry', 'duplicate entries')}"
    _write_new_entry("\n".join(lines), tags="migrate", shortcut=None, title=title)


@app.command(rich_help_panel="Data")
def reindex():
    """Rebuild the cache by re-parsing every ``.md`` (trust state is kept)."""
    get_db().init_db()
    reconcile_all(get_db(), get_entries_dir(), force=True)
    total = get_db().get_memo_stats()[0]
    console.print(f"[green]Reindexed {entries(total)}.[/green]")
