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


# --------------------------------------------------------------------------- #
# idx-conflict detection and sync diff (used by push/pull/resolve)
# --------------------------------------------------------------------------- #
def snapshot_idx(db: MemoDatabase) -> dict[str, int]:
    """Return ``{uid: idx}`` for every cached entry — the sync-diff baseline."""
    with db.connection() as conn:
        rows = conn.execute("SELECT uid, idx FROM memos").fetchall()
    return {uid: idx for uid, idx in rows if idx is not None}


def compute_sync_diff(before: dict[str, int], after: dict[str, int]) -> dict[str, list]:
    """Classify a uid→idx change between two snapshots.

    Returns ``{"added": [uid], "removed": [uid], "moved": [(uid, old, new)]}``.
    """
    added = [uid for uid in after if uid not in before]
    removed = [uid for uid in before if uid not in after]
    moved = [
        (uid, before[uid], after[uid])
        for uid in before
        if uid in after and before[uid] != after[uid]
    ]
    return {"added": added, "removed": removed, "moved": moved}


def find_conflicts(db: MemoDatabase) -> list[dict]:
    """Return ``[{idx, entries: [entry_dict]}]`` for every idx held by >1 entry.

    Each ``entry_dict`` is JSON-serializable and carries ``side``/``prev_idx``
    placeholders (filled by :func:`annotate_groups`).
    """
    by_idx: dict[int, list] = {}
    for row in db.get_memos(sort_by="idx"):
        by_idx.setdefault(row.idx, []).append(row)
    groups = []
    for idx, rows in sorted(by_idx.items()):
        if len(rows) <= 1:
            continue
        entries = [
            {
                "uid": row.uid,
                "path": db.path_for(row.uid) or "",
                "idx": idx,
                "shortcut": row.shortcut or "",
                "tags": row.tags or "",
                "title": row.title or "",
                "content": row.content or "",
                "created_at": row.created_at or "",
                "side": None,
                "prev_idx": None,
            }
            for row in rows
        ]
        groups.append({"idx": idx, "entries": entries})
    return groups


def annotate_groups(groups: list[dict], before: dict[str, int]) -> list[dict]:
    """Attach ``side``/``prev_idx`` to each conflict entry given the pre-pull
    ``{uid: idx}`` snapshot. ``ours`` = held this idx before; ``theirs`` =
    incoming (brand-new, or moved from a different idx). Mutates and returns
    ``groups``."""
    for group in groups:
        idx = group["idx"]
        for e in group["entries"]:
            uid = e["uid"]
            if before.get(uid) == idx:
                e["side"] = "ours"
                e["prev_idx"] = idx
            else:
                e["side"] = "theirs"
                e["prev_idx"] = before.get(uid)
    return groups
