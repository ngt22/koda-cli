"""Tests for the widened (64-bit) uid: generation, collision resistance, and
prefix lookup (#46)."""

import string

from _helpers import put_entry

from koda.db import UID_LENGTH, MemoDatabase, compute_uid


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
    put_entry("body", idx=0, uid=full)
    row = db.get_memo_by_uid_prefix(full[:7])
    assert row is not None and row.uid == full


def test_get_memo_by_uid_prefix_ambiguous_returns_none(db: MemoDatabase):
    put_entry("a", idx=0, uid="abcdef01")
    put_entry("b", idx=1, uid="abcdef02")
    assert db.get_memo_by_uid_prefix("abcdef") is None


def test_get_memo_by_uid_prefix_no_match_returns_none(db: MemoDatabase):
    put_entry("a", idx=0, uid="abcdef0123456789")
    assert db.get_memo_by_uid_prefix("ffffff") is None
    assert db.get_memo_by_uid_prefix("") is None


def test_get_memo_by_uid_prefix_escapes_like_wildcards(db: MemoDatabase):
    put_entry("a", idx=0, uid="abcdef0123456789")
    # '%'/'_' must be matched literally, not as LIKE wildcards.
    assert db.get_memo_by_uid_prefix("a%") is None
    assert db.get_memo_by_uid_prefix("_bcdef") is None
