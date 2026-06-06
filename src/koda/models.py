"""Data models for koda memos."""

from dataclasses import dataclass
from typing import Any, Optional

from .constants import TAG_SEPARATOR


@dataclass(frozen=True)
class MemoRow:
    """A row materialized from the memos table.

    Field order matches the canonical SELECT projection used across the
    database layer: id, uid, idx, content, tags, shortcut, created_at,
    modified_at, source.

    ``source`` is a local trust annotation ('local' = authored or reviewed on
    this machine; 'remote' = brought in by a Git sync and not yet reviewed). It
    is intentionally NOT part of the sync payload, so a peer cannot label its
    own entries 'local' to dodge the exec confirmation.
    """

    id: int
    uid: str
    idx: int
    content: str | None
    tags: str | None
    shortcut: str | None
    created_at: str | None
    modified_at: str | None = ""
    source: str = "local"

    @classmethod
    def from_row(cls, row) -> Optional["MemoRow"]:
        if row is None:
            return None
        assert len(row) == 9, f"expected 9 columns, got {len(row)}"
        return cls(*row)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view of the row. ``tags`` is split into a list."""
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
        }
