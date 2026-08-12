"""Reconcile the Markdown entry store into the local cache.

The ``.md`` files under ``<vault>/entries`` are the truth; the SQLite cache is
derived. ``reconcile_all`` runs on every CLI invocation (mtime-gated, so the
steady state is just ``stat`` calls) to reflect any external add/edit/delete/
rename made by Obsidian, an editor, an AI agent, or ``git pull``:

- new/changed file  → upsert into cache, keyed by frontmatter ``uid``;
- vanished file     → delete from cache;
- same uid, new path → treated as a rename (path updated, not delete+add).

Trust: a file whose body no longer matches the ``blessed_hash`` koda recorded
(or a file koda has never authored) is external → ``source='remote'`` and will
prompt before ``koda x``. koda's own writes go through ``sync_path(blessed=True)``
which records the new blessed hash → ``source='local'``.

Scope: only top-level ``entries/*.md`` files are scanned — subfolders are not
recursed into. Organize with tags/frontmatter, not folders; a ``.md`` you file
into an Obsidian subfolder won't be picked up until it's moved back to
``entries/``.
"""

from datetime import datetime
from pathlib import Path

from .cli_utils import stderr_console
from .constants import DATETIME_FMT
from .db import MemoDatabase
from .md_store import MdEntry, ensure_uid, read_entry, write_entry


def _now() -> str:
    return datetime.now().strftime(DATETIME_FMT)


def _normalize(entry: MdEntry, entries_dir: Path, path: Path, now: str, next_idx: int) -> bool:
    """Fill in machine fields (created/modified/uid/idx) that a hand-created or
    AI-written file may lack. Returns True if the file must be written back."""
    changed = False
    if not entry.created_at:
        entry.created_at = now
        changed = True
    if not entry.modified_at:
        entry.modified_at = entry.created_at
        changed = True
    if not entry.uid:
        ensure_uid(entry, now)
        changed = True
    if entry.idx is None:
        entry.idx = next_idx
        changed = True
    return changed


def _resolve_source(db: MemoDatabase, entry: MdEntry, manifest: dict) -> tuple[str, str | None]:
    """Decide (source, blessed_hash) for a discovered/changed file."""
    prior = manifest.get(entry.uid)
    if prior and prior["blessed_hash"] == entry.body_hash:
        return "local", entry.body_hash  # unchanged since koda last blessed it
    if prior:
        return "remote", prior["blessed_hash"]  # existing entry edited externally
    return "remote", None  # never-seen file → untrusted until reviewed


def _upsert(
    db: MemoDatabase, entry: MdEntry, rel: str, mtime: float, source: str, blessed_hash: str | None
) -> None:
    """Upsert one entry into the cache, dropping a shortcut already owned by a
    different uid (mirrors the old merge's shortcut-conflict handling)."""
    assert entry.uid is not None  # callers normalize the entry before upserting
    shortcut = entry.shortcut
    if shortcut and db.shortcut_owner(shortcut, exclude_uid=entry.uid):
        shortcut = None
    db.upsert(
        uid=entry.uid,
        idx=entry.idx if entry.idx is not None else 0,
        content=entry.content or "",
        tags=entry.tags or "",
        shortcut=shortcut,
        created_at=entry.created_at or "",
        modified_at=entry.modified_at or entry.created_at or "",
        source=source,
        title=entry.title,
        description=entry.description,
        path=rel,
        mtime=mtime,
        body_hash=entry.body_hash,
        blessed_hash=blessed_hash,
    )


def reconcile_all(db: MemoDatabase, entries_dir: Path, *, force: bool = False) -> None:
    """Scan ``entries_dir`` → cache. mtime-gated unless ``force`` (which re-parses
    every file, e.g. ``koda reindex``). ``force`` preserves each entry's trust
    state, since the blessed hashes stay in the manifest — only a schema-version
    rebuild (which drops the whole cache) loses them."""
    manifest = db.manifest()
    by_path = {info["path"]: uid for uid, info in manifest.items()}
    now = _now()
    next_idx = db.allocate_idx()

    seen_uids: set[str] = set()
    present: set[str] = set()

    if entries_dir.exists():
        files = sorted(p for p in entries_dir.glob("*.md") if p.is_file())
    else:
        files = []

    for path in files:
        rel = path.name
        present.add(rel)
        mtime = path.stat().st_mtime
        prior_uid = by_path.get(rel)
        if (
            not force
            and prior_uid
            and manifest.get(prior_uid, {}).get("mtime") == mtime
            and prior_uid not in seen_uids
        ):
            seen_uids.add(prior_uid)  # unchanged — but if a same-uid file already
            continue  # won this scan, fall through so the LAST file wins

        entry = read_entry(path)
        if _normalize(entry, entries_dir, path, now, next_idx):
            write_entry(entries_dir, entry, path=path)
            mtime = path.stat().st_mtime
        assert entry.uid is not None  # _normalize always assigns one
        if entry.idx is not None and entry.idx >= next_idx:
            next_idx = entry.idx + 1

        if entry.uid in seen_uids:
            stderr_console.print(
                f"[yellow]Warning: {rel!r} has the same uid ({entry.uid}) as another "
                f"entries/*.md file already scanned this run — only the last one "
                f"(by filename order) wins in the cache. "
                f"Give one of them a fresh uid.[/yellow]"
            )
        source, blessed = _resolve_source(db, entry, manifest)
        _upsert(db, entry, rel, mtime, source, blessed)
        seen_uids.add(entry.uid)

    for uid, info in manifest.items():
        if uid not in seen_uids and info["path"] not in present:
            db.delete_by_uid(uid)


def sync_path(db: MemoDatabase, entries_dir: Path, path: Path, *, blessed: bool) -> MdEntry:
    """Reflect a single ``.md`` file into the cache. ``blessed=True`` marks it as
    koda-authored/reviewed (``source='local'``, blessed hash updated); otherwise
    the normal trust logic applies. Returns the parsed entry."""
    now = _now()
    entry = read_entry(path)
    if _normalize(entry, entries_dir, path, now, db.allocate_idx()):
        write_entry(entries_dir, entry, path=path)
    assert entry.uid is not None  # _normalize always assigns one
    mtime = path.stat().st_mtime
    blessed_hash: str | None
    if blessed:
        source, blessed_hash = "local", entry.body_hash
    else:
        source, blessed_hash = _resolve_source(db, entry, db.manifest())
    _upsert(db, entry, path.name, mtime, source, blessed_hash)
    return entry


def remove_path(db: MemoDatabase, path: Path) -> None:
    """Drop the cache row for a file koda just deleted."""
    db.delete_by_path(path.name)
