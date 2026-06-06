"""Tests for MemoMerger.merge: insert / update / skip / conflict paths."""

from koda.git_sync import MemoMerger, pick_idx, pick_shortcut


def entry(
    uid,
    idx,
    content="body",
    tags="",
    shortcut=None,
    created_at="2026-01-01 00:00:00",
    modified_at=None,
):
    return {
        "uid": uid,
        "idx": idx,
        "shortcut": shortcut,
        "content": content,
        "tags": tags,
        "created_at": created_at,
        "modified_at": modified_at if modified_at is not None else created_at,
    }


def test_insert_new_entry(db):
    inserted, updated, skipped, dropped = MemoMerger(db).merge([entry("uid0001", 0)])
    assert (inserted, updated, skipped, dropped) == (1, 0, 0, 0)
    row = db.get_memo_by_uid("uid0001")
    assert row is not None
    assert row.content == "body"


def test_update_when_remote_is_newer(db):
    db.add_memo("uid0001", 0, None, "old", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    inserted, updated, skipped, dropped = MemoMerger(db).merge(
        [entry("uid0001", 0, content="new", modified_at="2026-02-01 00:00:00")]
    )
    assert (inserted, updated, skipped, dropped) == (0, 1, 0, 0)
    assert db.get_memo_by_uid("uid0001").content == "new"


def test_skip_when_remote_is_older(db):
    db.add_memo("uid0001", 0, None, "current", "", "2026-03-01 00:00:00", "2026-03-01 00:00:00")
    inserted, updated, skipped, dropped = MemoMerger(db).merge(
        [entry("uid0001", 0, content="stale", modified_at="2026-02-01 00:00:00")]
    )
    assert (inserted, updated, skipped, dropped) == (0, 0, 1, 0)
    assert db.get_memo_by_uid("uid0001").content == "current"


def test_skip_when_timestamps_equal(db):
    db.add_memo("uid0001", 0, None, "current", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    _, updated, skipped, _ = MemoMerger(db).merge(
        [entry("uid0001", 0, content="same-ts", modified_at="2026-01-01 00:00:00")]
    )
    assert (updated, skipped) == (0, 1)


def test_skip_entry_with_missing_uid(db):
    inserted, updated, skipped, dropped = MemoMerger(db).merge([{"idx": 0, "content": "x"}])
    assert (inserted, updated, skipped, dropped) == (0, 0, 1, 0)


def test_skip_entry_with_invalid_idx(db):
    inserted, _, skipped, _ = MemoMerger(db).merge(
        [{"uid": "uid0001", "idx": "not-int", "content": "x"}]
    )
    assert (inserted, skipped) == (0, 1)


def test_idx_conflict_resolved_to_next_idx(db):
    db.add_memo("uid0001", 0, None, "first", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    inserted, _, _, _ = MemoMerger(db).merge([entry("uid0002", 0, content="second")])
    assert inserted == 1
    new_row = db.get_memo_by_uid("uid0002")
    assert new_row.idx != 0


def test_shortcut_conflict_dropped_on_insert(db):
    db.add_memo("uid0001", 0, "ab", "owner", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    inserted, _, _, dropped = MemoMerger(db).merge(
        [entry("uid0002", 1, content="wants-ab", shortcut="ab")]
    )
    assert (inserted, dropped) == (1, 1)
    assert db.get_memo_by_uid("uid0002").shortcut is None


def test_shortcut_kept_when_owned_by_same_uid(db):
    db.add_memo("uid0001", 0, "ab", "old", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    _, updated, _, dropped = MemoMerger(db).merge(
        [entry("uid0001", 0, content="new", shortcut="ab", modified_at="2026-02-01 00:00:00")]
    )
    assert (updated, dropped) == (1, 0)
    assert db.get_memo_by_uid("uid0001").shortcut == "ab"


def test_batch_mixed_outcomes(db):
    db.add_memo("uid0001", 0, None, "existing", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    inserted, updated, skipped, _ = MemoMerger(db).merge(
        [
            entry("uid0002", 1, content="brand-new"),
            entry("uid0001", 0, content="updated", modified_at="2026-05-01 00:00:00"),
            entry("uid0003", 2, content="another-new"),
        ]
    )
    assert inserted == 2
    assert updated == 1
    assert skipped == 0


def test_two_new_entries_claim_same_idx(db):
    """Both remote entries want idx 0; the second must be relocated, not retried."""
    inserted, _, _, dropped = MemoMerger(db).merge(
        [entry("uid0001", 0, content="first"), entry("uid0002", 0, content="second")]
    )
    assert (inserted, dropped) == (2, 0)
    idxs = sorted(r.idx for r in db.get_memos(limit=None))
    assert len(set(idxs)) == 2  # no UNIQUE(idx) collision


def test_insert_with_both_idx_and_shortcut_conflict(db):
    """A new entry colliding on BOTH idx and shortcut: idx relocated, shortcut dropped."""
    db.add_memo("uid0001", 0, "ab", "owner", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    inserted, _, _, dropped = MemoMerger(db).merge(
        [entry("uid0002", 0, content="wants-both", shortcut="ab")]
    )
    assert (inserted, dropped) == (1, 1)
    new_row = db.get_memo_by_uid("uid0002")
    assert new_row.idx != 0
    assert new_row.shortcut is None


class TestPickIdx:
    def test_returns_preferred_when_free(self, db):
        with db.connection() as conn:
            assert pick_idx(conn, 5) == 5

    def test_returns_next_when_occupied(self, db):
        db.add_memo("uid0001", 5, None, "x", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
        with db.connection() as conn:
            assert pick_idx(conn, 5) == 6


class TestPickShortcut:
    def test_none_and_empty_pass_through(self, db):
        with db.connection() as conn:
            assert pick_shortcut(conn, "uid0001", None) is None
            assert pick_shortcut(conn, "uid0001", "") == ""

    def test_unclaimed_is_usable(self, db):
        with db.connection() as conn:
            assert pick_shortcut(conn, "uid0001", "ab") == "ab"

    def test_claimed_by_other_uid_is_dropped(self, db):
        db.add_memo("uid0001", 0, "ab", "owner", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
        with db.connection() as conn:
            assert pick_shortcut(conn, "uid0002", "ab") is None

    def test_owned_by_same_uid_is_kept(self, db):
        db.add_memo("uid0001", 0, "ab", "owner", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
        with db.connection() as conn:
            assert pick_shortcut(conn, "uid0001", "ab") == "ab"
