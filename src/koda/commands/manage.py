"""Store-management commands: migrate (legacy SQLite → Markdown), reindex."""

import sqlite3
from pathlib import Path

import typer

from ..cli_utils import exit_error
from ..config import LEGACY_DB_PATH
from ..db import compute_uid
from ..main import app
from ..md_store import MdEntry, write_entry
from ..reconcile import reconcile_all
from ..runtime import console, get_db, get_entries_dir, init_db

# Columns a legacy koda.db might have; older versions lack title/source.
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
    """One-time migration: write one Markdown file per row of a legacy koda.db.

    koda 2.x stores entries as ``entries/*.md``; this reads your old SQLite
    database and materialises each row as a file, then builds the cache.
    """
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

    reconcile_all(get_db(), entries_dir, force=True)
    console.print(
        f"[green]Migrated {count} entr{'y' if count == 1 else 'ies'} to {entries_dir}.[/green]\n"
        f"[dim]Open {get_entries_dir().parent} in Obsidian, or `koda list` to verify.[/dim]"
    )


@app.command(rich_help_panel="Data")
def reindex():
    """Rebuild the cache from the Markdown files (re-parses every entry).

    Use if the cache looks stale or inconsistent. Trust state (local/remote) is
    preserved. The ``.md`` files are the source of truth, so this never loses
    data.
    """
    init_db()
    entries_dir = get_entries_dir()
    reconcile_all(get_db(), entries_dir, force=True)
    total = get_db().get_memo_stats()[0]
    console.print(f"[green]Reindexed {total} entr{'y' if total == 1 else 'ies'}.[/green]")
