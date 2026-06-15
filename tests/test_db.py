"""Tests for MemoDatabase.get_memos (unified limit/offset query, #83)."""

from koda.constants import TAG_SEPARATOR


def _seed(db, idx, content, tags=()):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=content,
        tags=TAG_SEPARATOR.join(tags),
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def _seed_many(db, n):
    for i in range(1, n + 1):
        _seed(db, i, f"entry {i}")


class TestGetMemos:
    def test_limit_none_returns_all(self, db):
        """limit=None replaces the old get_memos_all behavior."""
        _seed_many(db, 25)
        rows = db.get_memos(limit=None)
        assert [r.idx for r in rows] == list(range(1, 26))

    def test_default_returns_all(self, db):
        """The default (no limit kwarg) returns every match, not a 20-row page."""
        _seed_many(db, 25)
        assert len(db.get_memos()) == 25

    def test_limit_paginates(self, db):
        _seed_many(db, 25)
        assert [r.idx for r in db.get_memos(limit=10)] == list(range(1, 11))

    def test_offset(self, db):
        _seed_many(db, 25)
        assert [r.idx for r in db.get_memos(limit=10, offset=10)] == list(range(11, 21))

    def test_offset_without_limit_is_ignored(self, db):
        """offset only applies when limit is set (no LIMIT clause -> no OFFSET)."""
        _seed_many(db, 5)
        assert len(db.get_memos(offset=3)) == 5

    def test_desc_ordering(self, db):
        _seed_many(db, 3)
        assert [r.idx for r in db.get_memos(desc=True)] == [3, 2, 1]

    def test_query_filter(self, db):
        _seed(db, 1, "alpha")
        _seed(db, 2, "beta")
        rows = db.get_memos(query="alph")
        assert [r.content for r in rows] == ["alpha"]

    def test_tag_filter(self, db):
        _seed(db, 1, "a", tags=["work"])
        _seed(db, 2, "b", tags=["home"])
        rows = db.get_memos(tag="work")
        assert [r.idx for r in rows] == [1]

    def test_limited_matches_all_prefix(self, db):
        """A limited query is exactly the head of the unlimited query."""
        _seed_many(db, 30)
        all_rows = db.get_memos(limit=None)
        page = db.get_memos(limit=7, offset=0)
        assert [r.uid for r in page] == [r.uid for r in all_rows[:7]]


class TestSource:
    def test_add_memo_auto_idx_defaults_to_local(self, db):
        db.add_memo_auto_idx("u1", None, "body", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
        assert db.get_latest_entry().source == "local"

    def test_add_memo_auto_idx_accepts_remote(self, db):
        db.add_memo_auto_idx(
            "u2", None, "body", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00", source="remote"
        )
        assert db.get_latest_entry().source == "remote"

    def test_add_memo_accepts_remote(self, db):
        db.add_memo(
            uid="u3",
            idx=5,
            shortcut=None,
            content="body",
            tags="",
            created_at="2026-01-01 00:00:00",
            modified_at="2026-01-01 00:00:00",
            source="remote",
        )
        assert db.get_latest_entry().source == "remote"
