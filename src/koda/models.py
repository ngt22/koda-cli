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
    content: Optional[str]
    tags: Optional[str]
    shortcut: Optional[str]
    created_at: Optional[str]
    modified_at: Optional[str] = ""

    @classmethod
    def from_row(cls, row) -> Optional["MemoRow"]:
        if row is None:
            return None
        if len(row) == 7:
            return cls(*row, modified_at="")
        return cls(*row)
