"""Tests for koda data models."""

import pytest

from koda.models import MemoRow

# Canonical 9-column row matching _MEMO_COLUMNS in db.py:
# id, uid, idx, content, tags, shortcut, created_at, modified_at, source
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
)


class TestFromRow:
    def test_none_returns_none(self):
        assert MemoRow.from_row(None) is None

    def test_nine_columns_materializes(self):
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

    def test_eight_columns_raises_assertion(self):
        with pytest.raises(AssertionError):
            MemoRow.from_row(_ROW[:8])

    def test_ten_columns_raises_assertion(self):
        with pytest.raises(AssertionError):
            MemoRow.from_row((*_ROW, "extra"))
