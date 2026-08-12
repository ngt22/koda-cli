"""Tests for MemoDatabase.get_memos over the cache (unified limit/offset query).

The cache is now populated from ``.md`` files via reconcile, so these seed
entries through the vault (``_helpers.put_entry``) and exercise the read/query
path exactly as production does.
"""

from _helpers import put_entry

from koda.constants import TAG_SEPARATOR


def _seed(idx, content, tags=()):
    put_entry(content, idx=idx, tags=TAG_SEPARATOR.join(tags), uid=f"uid{idx:04d}00000000")


def _seed_many(n):
    for i in range(1, n + 1):
        _seed(i, f"entry {i}")


class TestGetMemos:
    def test_limit_none_returns_all(self, db):
        _seed_many(25)
        rows = db.get_memos(limit=None)
        assert [r.idx for r in rows] == list(range(1, 26))

    def test_default_returns_all(self, db):
        """The default (no limit kwarg) returns every match, not a 20-row page."""
        _seed_many(25)
        assert len(db.get_memos()) == 25

    def test_limit_paginates(self, db):
        _seed_many(25)
        assert [r.idx for r in db.get_memos(limit=10)] == list(range(1, 11))

    def test_offset(self, db):
        _seed_many(25)
        assert [r.idx for r in db.get_memos(limit=10, offset=10)] == list(range(11, 21))

    def test_offset_without_limit_is_ignored(self, db):
        """offset only applies when limit is set (no LIMIT clause -> no OFFSET)."""
        _seed_many(5)
        assert len(db.get_memos(offset=3)) == 5

    def test_desc_ordering(self, db):
        _seed_many(3)
        assert [r.idx for r in db.get_memos(desc=True)] == [3, 2, 1]

    def test_query_filter(self, db):
        _seed(1, "alpha")
        _seed(2, "beta")
        rows = db.get_memos(query="alph")
        assert [r.content for r in rows] == ["alpha"]

    def test_tag_filter(self, db):
        _seed(1, "a", tags=["work"])
        _seed(2, "b", tags=["home"])
        rows = db.get_memos(tag="work")
        assert [r.idx for r in rows] == [1]

    def test_limited_matches_all_prefix(self, db):
        """A limited query is exactly the head of the unlimited query."""
        _seed_many(30)
        all_rows = db.get_memos(limit=None)
        page = db.get_memos(limit=7, offset=0)
        assert [r.uid for r in page] == [r.uid for r in all_rows[:7]]


class TestCacheMutation:
    def test_upsert_then_read_by_uid(self, db):
        row = put_entry("hello", idx=0, shortcut="hw", tags="a,b", title="Hi")
        assert row.content == "hello"
        assert db.get_memo_by_shortcut("hw").uid == row.uid
        assert db.get_memo_by_idx(0).title == "Hi"

    def test_delete_by_uid_removes_row(self, db):
        row = put_entry("bye", idx=0)
        db.delete_by_uid(row.uid)
        assert db.get_memo_by_uid(row.uid) is None

    def test_allocate_idx_advances(self, db):
        assert db.allocate_idx() == 0
        put_entry("x", idx=0)
        assert db.allocate_idx() == 1

    def test_shortcut_owner_and_stats(self, db):
        a = put_entry("a", idx=0, shortcut="sc")
        assert db.shortcut_owner("sc") == a.uid
        assert db.shortcut_owner("sc", exclude_uid=a.uid) is None
        put_entry("b", idx=1)
        count, max_idx = db.get_memo_stats()
        assert (count, max_idx) == (2, 1)
