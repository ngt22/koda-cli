"""Local cache over the Markdown entry store.

The source of truth is the ``.md`` files under ``<vault>/entries`` (see
``md_store``). This module is a **disposable, rebuildable SQLite cache** that
exists only for fast queries and for the local-only trust ledger:

- fast ``list`` / search / sort without parsing every file each run;
- the ``source`` (local/remote) trust state, which must NOT be synced;
- ``blessed_hash`` — the content koda last authored/reviewed, so an external
  edit (Obsidian / AI / git pull) is detected and marked ``source='remote'``.

The cache lives at ``<vault>/.koda/cache.db`` and can be dropped and rebuilt
from the ``.md`` files at any time (``koda reindex``). ``reconcile`` keeps it in
sync on every run; nothing here is authoritative.
"""

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import MemoRow

# Number of hex chars kept from the sha1 hash to form a uid. 16 hex = 64 bits,
# chosen so birthday/preimage attacks against the sync merge key are infeasible.
UID_LENGTH = 16


def compute_uid(content: str, created_at: str) -> str:
    """Return the stable uid: first ``UID_LENGTH`` hex chars of
    ``sha1(content + created_at)``. Deterministic across machines, so the same
    entry derives the same uid everywhere. Stored in each ``.md``'s frontmatter
    as the entry's identity; never recomputed on edit."""
    raw = f"{content}{created_at}".encode()
    return hashlib.sha1(raw).hexdigest()[:UID_LENGTH]


# Kept as a tuple for a stable ``except IntegrityErrors:`` idiom across the code
# base (previously spanned sqlite3 + libsql; now sqlite3 only).
IntegrityErrors: tuple = (sqlite3.IntegrityError,)


VALID_SORT_COLUMNS = {
    "id",
    "idx",
    "uid",
    "tags",
    "content",
    "created_at",
    "modified_at",
    "shortcut",
    "title",
    "description",
}


class DatabaseError(RuntimeError):
    """Cache configuration error (missing path, etc.)."""


# Bump when the cache schema changes; a mismatch drops and rebuilds the cache
# (it is derived data, so this is always safe).
CACHE_SCHEMA_VERSION = 1

# Projection materialised into a MemoRow (order must match MemoRow fields).
_MEMO_COLUMNS = (
    "id, uid, idx, content, tags, shortcut, created_at, modified_at, source, title, description"
)

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS memos (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        uid          TEXT UNIQUE,
        idx          INTEGER,
        content      TEXT,
        tags         TEXT,
        shortcut     TEXT,
        created_at   TIMESTAMP,
        modified_at  TIMESTAMP,
        source       TEXT NOT NULL DEFAULT 'local',
        title        TEXT,
        description  TEXT,
        path         TEXT,
        mtime        REAL,
        body_hash    TEXT,
        blessed_hash TEXT
    )
"""


class MemoDatabase:
    """SQLite-backed cache/query layer over the Markdown entry store."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None

    @contextmanager
    def connection(self) -> Iterator:
        if self.path is None:
            raise DatabaseError("Cache path is not configured.")
        conn = sqlite3.connect(self.path)
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        """Ensure the cache exists at the current schema version, rebuilding it
        from scratch on a version mismatch (cache is derived, so dropping is
        safe; ``reconcile`` repopulates it)."""
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != CACHE_SCHEMA_VERSION:
                conn.execute("DROP TABLE IF EXISTS memos")
                conn.execute(_CREATE_TABLE)
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_memos_shortcut "
                    "ON memos(shortcut) WHERE shortcut IS NOT NULL AND shortcut != ''"
                )
                conn.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")
            else:
                conn.execute(_CREATE_TABLE)
        if self.path is not None:
            os.chmod(self.path, 0o600)

    def clear(self) -> None:
        """Drop all cached rows (used by ``reindex`` before a full rescan)."""
        with self.connection() as conn:
            conn.execute("DELETE FROM memos")

    # ------------------------------------------------------------------ #
    # Reads (return MemoRow)
    # ------------------------------------------------------------------ #
    @staticmethod
    def next_idx(conn) -> int:
        row = conn.execute("SELECT MAX(idx) FROM memos").fetchone()
        return (row[0] + 1) if row[0] is not None else 0

    def allocate_idx(self) -> int:
        """Return the next free display index."""
        with self.connection() as conn:
            return self.next_idx(conn)

    @staticmethod
    def _filters(query=None, tag=None, exclude_tag=None, shortcuts_only=False):
        sql = " WHERE 1=1"
        params: list = []
        if query:
            sql += " AND (content LIKE ? OR title LIKE ? OR description LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%", f"%{query}%"])
        if tag:
            sql += " AND tags LIKE ?"
            params.append(f"%{tag}%")
        if exclude_tag:
            sql += " AND (tags IS NULL OR tags = '' OR tags NOT LIKE ?)"
            params.append(f"%{exclude_tag}%")
        if shortcuts_only:
            sql += " AND shortcut IS NOT NULL AND shortcut != ''"
        return sql, tuple(params)

    def get_memos(
        self,
        query=None,
        tag=None,
        exclude_tag=None,
        shortcuts_only=False,
        limit: int | None = None,
        offset: int = 0,
        sort_by="idx",
        desc=False,
    ) -> list[MemoRow]:
        order_column = sort_by if sort_by in VALID_SORT_COLUMNS else "idx"
        order_direction = "DESC" if desc else "ASC"
        where_sql, params = self._filters(query, tag, exclude_tag, shortcuts_only)
        sql = (
            f"SELECT {_MEMO_COLUMNS} FROM memos"
            f"{where_sql} ORDER BY {order_column} {order_direction}, id ASC"
        )
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = params + (limit, offset)
        with self.connection() as conn:
            return MemoRow.from_rows(conn.execute(sql, params).fetchall())

    def get_memo_stats(self, query=None, tag=None, exclude_tag=None, shortcuts_only=False):
        where_sql, params = self._filters(query, tag, exclude_tag, shortcuts_only)
        sql = f"SELECT COUNT(*), MAX(idx) FROM memos{where_sql}"
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def get_latest_entry(self) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {_MEMO_COLUMNS} FROM memos ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return MemoRow.from_row(row)

    def get_memo_by_idx(self, idx: int) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {_MEMO_COLUMNS} FROM memos WHERE idx = ? ORDER BY id ASC",
                (idx,),
            ).fetchone()
        return MemoRow.from_row(row)

    def get_memo_by_shortcut(self, shortcut: str) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {_MEMO_COLUMNS} FROM memos WHERE shortcut = ?",
                (shortcut,),
            ).fetchone()
        return MemoRow.from_row(row)

    def get_memo_by_uid(self, uid: str) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {_MEMO_COLUMNS} FROM memos WHERE uid = ?",
                (uid,),
            ).fetchone()
        return MemoRow.from_row(row)

    @staticmethod
    def _uid_prefix_like(prefix: str) -> str:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return escaped + "%"

    def get_memo_by_uid_prefix(self, prefix: str) -> MemoRow | None:
        if not prefix:
            return None
        pattern = self._uid_prefix_like(prefix)
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT {_MEMO_COLUMNS} FROM memos WHERE uid LIKE ? ESCAPE '\\' LIMIT 2",
                (pattern,),
            ).fetchall()
        if len(rows) != 1:
            return None
        return MemoRow.from_row(rows[0])

    def path_for(self, uid: str) -> str | None:
        """Return the cached relative ``.md`` path for ``uid`` (or None)."""
        with self.connection() as conn:
            row = conn.execute("SELECT path FROM memos WHERE uid = ?", (uid,)).fetchone()
        return row[0] if row else None

    def shortcut_owner(self, shortcut: str, exclude_uid: str | None = None) -> str | None:
        """Return the uid that currently holds ``shortcut`` (excluding
        ``exclude_uid``), or None if free."""
        if not shortcut:
            return None
        with self.connection() as conn:
            rows = conn.execute("SELECT uid FROM memos WHERE shortcut = ?", (shortcut,)).fetchall()
        for (uid,) in rows:
            if uid != exclude_uid:
                return uid
        return None

    # ------------------------------------------------------------------ #
    # Cache mutation / manifest (used by reconcile)
    # ------------------------------------------------------------------ #
    def manifest(self) -> dict[str, dict]:
        """Return ``{uid: {id, path, mtime, body_hash, blessed_hash, source}}``
        for every cached entry — the reconcile baseline."""
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT uid, id, path, mtime, body_hash, blessed_hash, source FROM memos"
            ).fetchall()
        return {
            uid: {
                "id": rid,
                "path": path,
                "mtime": mtime,
                "body_hash": body_hash,
                "blessed_hash": blessed_hash,
                "source": source,
            }
            for uid, rid, path, mtime, body_hash, blessed_hash, source in rows
        }

    def upsert(
        self,
        *,
        uid: str,
        idx: int,
        content: str,
        tags: str,
        shortcut: str | None,
        created_at: str,
        modified_at: str,
        source: str,
        title: str | None,
        description: str | None,
        path: str,
        mtime: float,
        body_hash: str,
        blessed_hash: str | None,
    ) -> None:
        """Insert or update the cache row for ``uid`` (keyed by uid)."""
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO memos
                    (uid, idx, content, tags, shortcut, created_at, modified_at,
                     source, title, description, path, mtime, body_hash, blessed_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    idx=excluded.idx, content=excluded.content, tags=excluded.tags,
                    shortcut=excluded.shortcut, created_at=excluded.created_at,
                    modified_at=excluded.modified_at, source=excluded.source,
                    title=excluded.title, description=excluded.description,
                    path=excluded.path, mtime=excluded.mtime,
                    body_hash=excluded.body_hash, blessed_hash=excluded.blessed_hash
                """,
                (
                    uid,
                    idx,
                    content,
                    tags,
                    shortcut or None,
                    created_at,
                    modified_at,
                    source,
                    title,
                    description,
                    path,
                    mtime,
                    body_hash,
                    blessed_hash,
                ),
            )

    def update_location(self, uid: str, path: str, mtime: float) -> None:
        """Record a new path/mtime for ``uid`` (a rename detected by reconcile)."""
        with self.connection() as conn:
            conn.execute("UPDATE memos SET path = ?, mtime = ? WHERE uid = ?", (path, mtime, uid))

    def delete_by_uid(self, uid: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM memos WHERE uid = ?", (uid,))

    def delete_by_path(self, path: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM memos WHERE path = ?", (path,))
