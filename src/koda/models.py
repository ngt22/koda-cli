"""Data models for koda memos."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MemoRow:
    """A row materialized from the memos table.

    Field order matches the canonical SELECT projection used across the
    database layer: id, uid, idx, content, tags, shortcut, created_at,
    modified_at.
    """

    id: int
    uid: str
    idx: int
    content: str | None
    tags: str | None
    shortcut: str | None
    created_at: str | None
    modified_at: str | None = ""

    @classmethod
    def from_row(cls, row) -> Optional["MemoRow"]:
        if row is None:
            return None
        assert len(row) == 8, f"expected 8 columns, got {len(row)}"
        return cls(*row)
