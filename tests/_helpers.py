"""Importable test helpers for seeding the Markdown vault.

Module-level ``_seed`` helpers in the test files can't take pytest fixtures, so
this exposes a plain function that writes an entry ``.md`` (with an explicit idx
and optional trust state) and reconciles it into the cache using the runtime
singletons the ``vault`` fixture already wired.
"""

import koda.runtime as runtime
from koda import md_store, reconcile
from koda.db import compute_uid


def put_entry(
    content,
    *,
    idx,
    uid=None,
    shortcut=None,
    tags="",
    title=None,
    description=None,
    created_at="2026-01-01 00:00:00",
    modified_at="2026-01-01 00:00:00",
    source="local",
    extra=None,
):
    """Write ``content`` as an entry ``.md`` and reconcile it into the cache.

    Returns the resulting ``MemoRow``. ``source='remote'`` seeds an unreviewed
    (untrusted) entry; the default ``'local'`` blesses it as koda-authored.
    """
    db = runtime.get_db()
    entries_dir = runtime.get_entries_dir()
    uid = uid or compute_uid(content, created_at)
    entry = md_store.MdEntry(
        content=content,
        uid=uid,
        idx=idx,
        shortcut=shortcut,
        tags=tags,
        title=title,
        description=description,
        created_at=created_at,
        modified_at=modified_at,
        extra=extra or {},
    )
    path = md_store.write_entry(entries_dir, entry)
    reconcile.sync_path(db, entries_dir, path, blessed=(source == "local"))
    return db.get_memo_by_uid(uid)


def mark_remote(uid):
    """Flip an existing cache row to ``source='remote'`` (unreviewed)."""
    db = runtime.get_db()
    with db.connection() as conn:
        conn.execute("UPDATE memos SET source = 'remote' WHERE uid = ?", (uid,))


def build_vault(vault, specs):
    """Build an on-disk vault (``entries/*.md`` + ``.koda/cache.db``) for
    subprocess tests, seeding it exactly as reconcile would.

    ``specs`` is a list of dicts with keys: ``content``, ``idx`` and optional
    ``uid``, ``shortcut``, ``tags``, ``source`` ('local' default / 'remote').
    A subsequent ``koda`` subprocess pointed at ``vault`` reuses this cache; the
    mtime-gated reconcile leaves the seeded trust state intact. Returns the db.
    """
    from pathlib import Path

    from koda.db import MemoDatabase

    vault = Path(vault)
    entries_dir = vault / "entries"
    entries_dir.mkdir(parents=True, exist_ok=True)
    db = MemoDatabase(path=vault / ".koda" / "cache.db")
    db.init_db()
    for s in specs:
        content = s["content"]
        uid = s.get("uid") or compute_uid(content, "2026-01-01 00:00:00")
        entry = md_store.MdEntry(
            content=content,
            uid=uid,
            idx=s["idx"],
            shortcut=s.get("shortcut"),
            tags=s.get("tags", ""),
            created_at="2026-01-01 00:00:00",
            modified_at="2026-01-01 00:00:00",
        )
        path = md_store.write_entry(entries_dir, entry)
        reconcile.sync_path(db, entries_dir, path, blessed=(s.get("source", "local") == "local"))
    return db
