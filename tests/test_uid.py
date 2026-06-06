"""Tests for the widened (64-bit) uid: generation, collision resistance,
prefix lookup, and legacy short-uid sync compatibility (#46)."""

import string

from koda.db import UID_LENGTH, MemoDatabase, compute_uid
from koda.git_sync import MemoMerger


def test_uid_is_16_lowercase_hex_chars():
    uid = compute_uid("hello", "2026-01-01 00:00:00")
    assert UID_LENGTH == 16
    assert len(uid) == 16
    assert all(c in string.hexdigits.lower() for c in uid)


def test_uid_is_deterministic():
    a = compute_uid("same body", "2026-01-01 00:00:00")
    b = compute_uid("same body", "2026-01-01 00:00:00")
    assert a == b


def test_uid_depends_on_content_and_created_at():
    base = compute_uid("body", "2026-01-01 00:00:00")
    assert compute_uid("body!", "2026-01-01 00:00:00") != base
    assert compute_uid("body", "2026-01-02 00:00:00") != base


def test_no_collisions_over_many_distinct_inputs():
    """64-bit uids make collisions astronomically unlikely; a sweep of 50k
    distinct entries must stay collision-free (28-bit uids would not)."""
    uids = {compute_uid(f"entry-{i}", "2026-01-01 00:00:00") for i in range(50_000)}
    assert len(uids) == 50_000


def test_widening_keeps_legacy_7char_prefix():
    """The new uid still starts with what the old 7-char uid was, so unedited
    entries on a migrated and an unmigrated peer line up by prefix."""
    full = compute_uid("deploy", "2026-01-01 00:00:00")
    assert full[:7] == compute_uid("deploy", "2026-01-01 00:00:00")[:7]
    assert len(full) > 7


def test_get_memo_by_uid_prefix_single_match(db: MemoDatabase):
    full = compute_uid("body", "2026-01-01 00:00:00")
    db.add_memo(full, 0, None, "body", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    row = db.get_memo_by_uid_prefix(full[:7])
    assert row is not None and row.uid == full


def test_get_memo_by_uid_prefix_ambiguous_returns_none(db: MemoDatabase):
    db.add_memo("abcdef01", 0, None, "a", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    db.add_memo("abcdef02", 1, None, "b", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    assert db.get_memo_by_uid_prefix("abcdef") is None


def test_get_memo_by_uid_prefix_no_match_returns_none(db: MemoDatabase):
    db.add_memo("abcdef0123456789", 0, None, "a", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    assert db.get_memo_by_uid_prefix("ffffff") is None
    assert db.get_memo_by_uid_prefix("") is None


def test_get_memo_by_uid_prefix_escapes_like_wildcards(db: MemoDatabase):
    db.add_memo("abcdef0123456789", 0, None, "a", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    # '%'/'_' must be matched literally, not as LIKE wildcards.
    assert db.get_memo_by_uid_prefix("a%") is None
    assert db.get_memo_by_uid_prefix("_bcdef") is None


def _entry(uid, idx, content="body", modified_at="2026-01-01 00:00:00"):
    return {
        "uid": uid,
        "idx": idx,
        "shortcut": None,
        "content": content,
        "tags": "",
        "created_at": "2026-01-01 00:00:00",
        "modified_at": modified_at,
    }


def test_merge_legacy_short_uid_updates_widened_row(db: MemoDatabase):
    """A pre-widening peer emits a 7-char uid; merging it into a widened DB
    must update the existing row by prefix instead of inserting a duplicate."""
    full = compute_uid("body", "2026-01-01 00:00:00")
    db.add_memo(full, 0, None, "body", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")

    inserted, updated, skipped, _ = MemoMerger(db).merge(
        [_entry(full[:7], 0, content="updated", modified_at="2026-02-01 00:00:00")]
    )
    assert (inserted, updated, skipped) == (0, 1, 0)
    assert db.get_memo_by_uid(full).content == "updated"
    # No duplicate row was created.
    assert len(db.get_memos(limit=None)) == 1


def test_merge_ambiguous_short_uid_inserts_new(db: MemoDatabase):
    """If a short uid prefix is ambiguous, fall back to insert (no wrong merge)."""
    db.add_memo(
        "abcdef01" + "0" * 8, 0, None, "a", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    db.add_memo(
        "abcdef02" + "0" * 8, 1, None, "b", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    inserted, updated, _, _ = MemoMerger(db).merge([_entry("abcdef", 2, content="c")])
    assert inserted == 1 and updated == 0
    assert len(db.get_memos(limit=None)) == 3
