"""Tests for koda data models."""

import pytest

from koda.models import MemoRow

# Canonical 11-column row matching _MEMO_COLUMNS in db.py:
# id, uid, idx, content, tags, shortcut, created_at, modified_at, source,
# title, description
_ROW = (
    1,
    "abc1234",
    5,
    "hello",
    "work,home",
    "hi",
    "2026-01-01 00:00:00",
    "2026-01-02 00:00:00",
    "local",
    "My Title",
    "A summary",
)


class TestFromRow:
    def test_none_returns_none(self):
        assert MemoRow.from_row(None) is None

    def test_eleven_columns_materializes(self):
        memo = MemoRow.from_row(_ROW)
        assert memo is not None
        assert memo.id == 1
        assert memo.uid == "abc1234"
        assert memo.idx == 5
        assert memo.content == "hello"
        assert memo.tags == "work,home"
        assert memo.shortcut == "hi"
        assert memo.created_at == "2026-01-01 00:00:00"
        assert memo.modified_at == "2026-01-02 00:00:00"
        assert memo.source == "local"
        assert memo.title == "My Title"
        assert memo.description == "A summary"

    def test_to_dict_includes_title_and_description(self):
        memo = MemoRow.from_row(_ROW)
        assert memo is not None
        d = memo.to_dict()
        assert d["title"] == "My Title"
        assert d["description"] == "A summary"

    def test_ten_columns_raises_assertion(self):
        with pytest.raises(AssertionError):
            MemoRow.from_row(_ROW[:10])

    def test_twelve_columns_raises_assertion(self):
        with pytest.raises(AssertionError):
            MemoRow.from_row((*_ROW, "extra"))
