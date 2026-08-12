"""Tests for reconciling the Markdown entry store into the cache.

The ``.md`` files are the truth; ``reconcile_all`` reflects external
add/edit/delete/rename into the derived cache and maintains the local-only
trust ledger (``source`` local/remote + ``blessed_hash``).
"""

import koda.runtime as runtime
from koda import md_store
from koda.md_store import MdEntry, read_entry, write_entry
from koda.reconcile import reconcile_all, remove_path, sync_path


def _entries_dir():
    return runtime.get_entries_dir()


# ── External new file (hand-written, no uid) ─────────────────────────────────


def test_external_new_file_gets_uid_and_idx_written_back(db):
    entries_dir = _entries_dir()
    path = entries_dir / "hand.md"
    path.write_text("echo hello\n", encoding="utf-8")  # no frontmatter at all

    reconcile_all(db, entries_dir)

    parsed = read_entry(path)
    assert parsed.uid  # a uid was assigned and written back to the file
    assert parsed.idx is not None
    row = db.get_memo_by_uid(parsed.uid)
    assert row is not None
    assert row.content == "echo hello\n"
    # A never-seen file is untrusted until reviewed.
    assert row.source == "remote"


def test_external_edit_flips_local_to_remote(db, seed):
    """A koda-authored (local) entry edited outside koda becomes remote."""
    row = seed("original body")
    assert row.source == "local"
    path = _entries_dir() / db.path_for(row.uid)

    # Simulate an external editor rewriting the body (uid/idx kept, mtime bumped).
    entry = read_entry(path)
    entry.content = "tampered body"
    write_entry(_entries_dir(), entry, path=path)
    import os

    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))

    reconcile_all(db, _entries_dir())
    after = db.get_memo_by_uid(row.uid)
    assert after.content == "tampered body"
    assert after.source == "remote"


def test_sync_path_blessed_sets_local(db, write_md):
    """koda's own write path (blessed=True) trusts an entry as local."""
    row = write_md("remote body", idx=0, source="remote")
    assert row.source == "remote"
    path = _entries_dir() / db.path_for(row.uid)

    sync_path(db, _entries_dir(), path, blessed=True)
    assert db.get_memo_by_uid(row.uid).source == "local"


def test_unchanged_local_entry_stays_local(db, seed):
    """Re-running reconcile on an untouched local entry keeps it trusted."""
    row = seed("stable body")
    reconcile_all(db, _entries_dir())
    assert db.get_memo_by_uid(row.uid).source == "local"


# ── Deletion ─────────────────────────────────────────────────────────────────


def test_file_deletion_removes_cache_row(db, seed):
    row = seed("to be deleted")
    path = _entries_dir() / db.path_for(row.uid)
    path.unlink()

    reconcile_all(db, _entries_dir())
    assert db.get_memo_by_uid(row.uid) is None


def test_remove_path_drops_cache_row(db, seed):
    row = seed("gone")
    path = _entries_dir() / db.path_for(row.uid)
    path.unlink()
    remove_path(db, path)
    assert db.get_memo_by_uid(row.uid) is None


# ── Rename (same uid, new filename) ──────────────────────────────────────────


def test_rename_updates_path_without_duplicating(db, seed):
    row = seed("renamed body")
    old_rel = db.path_for(row.uid)
    old_path = _entries_dir() / old_rel
    new_path = _entries_dir() / "moved-name.md"
    old_path.rename(new_path)

    reconcile_all(db, _entries_dir())

    # Exactly one cache row, now pointing at the new filename.
    assert len(db.get_memos(limit=None)) == 1
    assert db.path_for(row.uid) == "moved-name.md"
    assert db.get_memo_by_uid(row.uid).content == "renamed body"


def test_force_rescan_preserves_trust(db, seed, write_md):
    """force=True re-parses every file but keeps each entry's trust state."""
    local = seed("local one")
    remote = write_md("remote one", idx=5, source="remote")
    reconcile_all(db, _entries_dir(), force=True)
    assert db.get_memo_by_uid(local.uid).source == "local"
    assert db.get_memo_by_uid(remote.uid).source == "remote"


def test_shortcut_conflict_dropped_on_upsert(db, seed):
    """A second file claiming a shortcut already owned by another uid loses it."""
    first = seed("first", shortcut="dup")
    entries_dir = _entries_dir()
    path = entries_dir / "second.md"
    entry = MdEntry(content="second", uid="seconduid0000000", idx=9, shortcut="dup")
    write_entry(entries_dir, entry, path=path)

    reconcile_all(db, entries_dir)
    # The original keeps the shortcut; the newcomer's is cleared.
    assert db.get_memo_by_shortcut("dup").uid == first.uid
    assert db.get_memo_by_uid("seconduid0000000").shortcut is None


def test_reconcile_reads_files_written_directly(db):
    """A fully-formed .md dropped into entries/ (e.g. via git pull) is picked up."""
    entries_dir = _entries_dir()
    entry = MdEntry(
        content="pulled body",
        uid="pulleduid0000000",
        idx=2,
        tags="synced",
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )
    md_store.write_entry(entries_dir, entry)
    reconcile_all(db, entries_dir)
    row = db.get_memo_by_uid("pulleduid0000000")
    assert row.content == "pulled body"
    assert row.tags == "synced"
    assert row.source == "remote"  # not authored locally


def test_duplicate_uid_last_file_wins_deterministically(db, seed):
    """Two entries/*.md files sharing a uid must resolve to the LAST file in
    filename order, stably across consecutive runs (no mtime-gate flip-flop)."""
    row = seed("original body")
    uid = row.uid
    entries_dir = _entries_dir()
    dup_path = entries_dir / "zzz-duplicate.md"
    dup = MdEntry(
        content="duplicate body",
        uid=uid,
        idx=row.idx,
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )
    md_store.write_entry(entries_dir, dup, path=dup_path)

    reconcile_all(db, entries_dir)  # run 1
    reconcile_all(db, entries_dir)  # run 2 — must not flip back to "original body"

    assert db.get_memo_by_uid(uid).content == "duplicate body"
    assert db.path_for(uid) == "zzz-duplicate.md"
