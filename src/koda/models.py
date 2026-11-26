"""Data models and identity helpers shared by every layer."""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from .constants import DATETIME_FMT, TAG_SEPARATOR

# 16 hex chars = 64 bits: keeps the sync merge key collision-resistant.
UID_LENGTH = 16

VALID_SORT_COLUMNS = frozenset(
    {
        "idx",
        "uid",
        "tags",
        "content",
        "created_at",
        "modified_at",
        "shortcut",
        "title",
        "description",
        "id",
    }
)


def compute_uid(content: str, created_at: str) -> str:
    """Stable entry identity: sha1(content + created_at), truncated."""
    return hashlib.sha1(f"{content}{created_at}".encode()).hexdigest()[:UID_LENGTH]


def now_str() -> str:
    return datetime.now().strftime(DATETIME_FMT)


@dataclass(frozen=True)
class MemoRow:
    """A cache row. ``source`` is local-only trust state and is never synced."""

    id: int
    uid: str
    idx: int
    content: str | None
    tags: str | None
    shortcut: str | None
    created_at: str | None
    modified_at: str | None = ""
    source: str = "local"
    title: str | None = None
    description: str | None = None

    @classmethod
    def from_row(cls, row) -> Optional["MemoRow"]:
        if row is None:
            return None
        assert len(row) == 11, f"expected 11 columns, got {len(row)}"
        return cls(*row)

    @classmethod
    def from_rows(cls, rows) -> list["MemoRow"]:
        return [cls(*row) for row in rows]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "uid": self.uid,
            "idx": self.idx,
            "content": self.content,
            "tags": [t for t in (self.tags or "").split(TAG_SEPARATOR) if t],
            "shortcut": self.shortcut,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "source": self.source,
            "title": self.title,
            "description": self.description,
        }
