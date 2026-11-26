"""Reconcile ``entries/*.md`` (truth) into the cache on every run, and detect idx conflicts.

Trust: a body that no longer matches koda's ``blessed_hash`` (or a file koda never wrote)
becomes ``source='remote'`` and gates ``koda x``. Only top-level ``entries/*.md`` is scanned.
"""

from pathlib import Path

from .cli_utils import stderr_console
from .db import MemoDatabase
from .md_store import MdEntry, ensure_uid, read_entry, write_entry
from .models import now_str


def _normalize(entry: MdEntry, now: str, next_idx: int) -> bool:
    """Fill machine fields a hand-written file may lack. True if the file must be rewritten."""
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
    prior = manifest.get(entry.uid)
    if prior and prior["blessed_hash"] == entry.body_hash:
        return "local", entry.body_hash  # unchanged since koda last blessed it
    if prior:
        return "remote", prior["blessed_hash"]  # existing entry edited externally
    return "remote", None  # never-seen file → untrusted until reviewed


def _upsert(
    db: MemoDatabase, entry: MdEntry, rel: str, mtime: float, source: str, blessed_hash: str | None
) -> None:
    """Upsert into the cache; a shortcut already owned by another uid is dropped."""
    assert entry.uid is not None
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
    """Scan ``entries_dir`` into the cache; mtime-gated unless ``force``. Trust state is kept."""
    manifest = db.manifest()
    by_path = {info["path"]: uid for uid, info in manifest.items()}
    now = now_str()
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
            seen_uids.add(prior_uid)
            continue

        entry = read_entry(path)
        if _normalize(entry, now, next_idx):
            write_entry(entries_dir, entry, path=path)
            mtime = path.stat().st_mtime
        assert entry.uid is not None
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
    """Reflect one file into the cache. ``blessed=True`` marks its current body as reviewed."""
    entry = read_entry(path)
    if _normalize(entry, now_str(), db.allocate_idx()):
        write_entry(entries_dir, entry, path=path)
    assert entry.uid is not None
    mtime = path.stat().st_mtime
    blessed_hash: str | None
    if blessed:
        source, blessed_hash = "local", entry.body_hash
    else:
        source, blessed_hash = _resolve_source(db, entry, db.manifest())
    _upsert(db, entry, path.name, mtime, source, blessed_hash)
    return entry


def snapshot_idx(db: MemoDatabase) -> dict[str, int]:
    return db.idx_by_uid()


def compute_sync_diff(before: dict[str, int], after: dict[str, int]) -> dict[str, list]:
    """``{"added": [uid], "removed": [uid], "moved": [(uid, old, new)]}``."""
    added = [uid for uid in after if uid not in before]
    removed = [uid for uid in before if uid not in after]
    moved = [
        (uid, before[uid], after[uid])
        for uid in before
        if uid in after and before[uid] != after[uid]
    ]
    return {"added": added, "removed": removed, "moved": moved}


def find_conflicts(db: MemoDatabase) -> list[dict]:
    """``[{idx, entries: [entry_dict]}]`` for every idx held by more than one entry."""
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
    """Mark each entry ``ours`` (held this idx in ``before``) or ``theirs``. Mutates ``groups``."""
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
