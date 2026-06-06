"""Database layer for koda: connection, schema, CRUD over the memos table."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import MemoRow

try:
    import libsql_experimental as _libsql

    LIBSQL_AVAILABLE = True
    try:
        IntegrityErrors: tuple = (sqlite3.IntegrityError, _libsql.IntegrityError)
    except AttributeError:
        IntegrityErrors = (sqlite3.IntegrityError,)
except ImportError:
    _libsql = None
    LIBSQL_AVAILABLE = False
    IntegrityErrors = (sqlite3.IntegrityError,)


VALID_SORT_COLUMNS = {
    "id",
    "idx",
    "uid",
    "tags",
    "content",
    "created_at",
    "modified_at",
    "shortcut",
}


class DatabaseError(RuntimeError):
    """Backend configuration error (missing driver, missing URL, etc.)."""


class MemoDatabase:
    """SQLite / libsql backed CRUD wrapper for the memos table."""

    def __init__(
        self,
        backend: str = "local",
        path: Path | None = None,
        turso_url: str = "",
        turso_token: str = "",
    ) -> None:
        self.backend = backend
        self.path = Path(path) if path else None
        self.turso_url = turso_url
        self.turso_token = turso_token

    @contextmanager
    def connection(self) -> Iterator:
        if self.backend == "turso":
            if not LIBSQL_AVAILABLE:
                raise DatabaseError(
                    "libsql-experimental is not installed. Run: pip install libsql-experimental"
                )
            if not self.turso_url:
                raise DatabaseError(
                    "Turso URL is not configured. "
                    "Set turso.url in config or KODA_TURSO_URL env var."
                )
            conn = _libsql.connect(self.turso_url, auth_token=self.turso_token or None)
            try:
                yield conn
            except Exception:
                raise
            else:
                conn.commit()
            finally:
                conn.close()
        else:
            if self.path is None:
                raise DatabaseError("Local DB path is not configured.")
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
        """Create the memos table and required indexes if missing."""
        if self.backend == "local" and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
        with self.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memos (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    uid         TEXT UNIQUE,
                    idx         INTEGER UNIQUE,
                    shortcut    TEXT,
                    content     TEXT,
                    tags        TEXT,
                    created_at  TIMESTAMP,
                    modified_at TIMESTAMP
                )
            """)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(memos)").fetchall()}
            if "shortcut" not in cols:
                conn.execute("ALTER TABLE memos ADD COLUMN shortcut TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_memos_shortcut "
                "ON memos(shortcut) WHERE shortcut IS NOT NULL"
            )
        if self.backend == "local" and self.path is not None:
            os.chmod(self.path, 0o600)

    @staticmethod
    def next_idx(conn) -> int:
        row = conn.execute("SELECT MAX(idx) FROM memos").fetchone()
        return (row[0] + 1) if row[0] is not None else 0

    @staticmethod
    def _filters(query=None, tag=None, exclude_tag=None, shortcuts_only=False):
        sql = " WHERE 1=1"
        params: list = []
        if query:
            sql += " AND content LIKE ?"
            params.append(f"%{query}%")
        if tag:
            sql += " AND tags LIKE ?"
            params.append(f"%{tag}%")
        if exclude_tag:
            sql += " AND (tags IS NULL OR tags = '' OR tags NOT LIKE ?)"
            params.append(f"%{exclude_tag}%")
        if shortcuts_only:
            sql += " AND shortcut IS NOT NULL AND shortcut != ''"
        return sql, tuple(params)

    _MEMO_COLUMNS = "id, uid, idx, content, tags, shortcut, created_at, modified_at"

    def get_memos(
        self,
        query=None,
        tag=None,
        exclude_tag=None,
        shortcuts_only=False,
        limit=20,
        offset=0,
        sort_by="idx",
        desc=False,
    ) -> list[MemoRow]:
        order_column = sort_by if sort_by in VALID_SORT_COLUMNS else "idx"
        order_direction = "DESC" if desc else "ASC"
        where_sql, params = self._filters(query, tag, exclude_tag, shortcuts_only)
        sql = (
            f"SELECT {self._MEMO_COLUMNS} FROM memos"
            f"{where_sql} ORDER BY {order_column} {order_direction}, id ASC LIMIT ? OFFSET ?"
        )
        params = params + (limit, offset)
        with self.connection() as conn:
            return [MemoRow.from_row(r) for r in conn.execute(sql, params).fetchall()]

    def get_memo_stats(self, query=None, tag=None, exclude_tag=None, shortcuts_only=False):
        where_sql, params = self._filters(query, tag, exclude_tag, shortcuts_only)
        sql = f"SELECT COUNT(*), MAX(idx) FROM memos{where_sql}"
        with self.connection() as conn:
            return conn.execute(sql, params).fetchone()

    def get_memos_all(
        self,
        query=None,
        tag=None,
        exclude_tag=None,
        shortcuts_only=False,
        sort_by="idx",
        desc=False,
    ) -> list[MemoRow]:
        order_column = sort_by if sort_by in VALID_SORT_COLUMNS else "idx"
        order_direction = "DESC" if desc else "ASC"
        where_sql, params = self._filters(query, tag, exclude_tag, shortcuts_only)
        sql = (
            f"SELECT {self._MEMO_COLUMNS} FROM memos"
            f"{where_sql} ORDER BY {order_column} {order_direction}, id ASC"
        )
        with self.connection() as conn:
            return [MemoRow.from_row(r) for r in conn.execute(sql, params).fetchall()]

    def get_latest_entry(self) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {self._MEMO_COLUMNS} FROM memos ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return MemoRow.from_row(row)

    def get_memo_by_idx(self, idx: int) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {self._MEMO_COLUMNS} FROM memos WHERE idx = ?",
                (idx,),
            ).fetchone()
        return MemoRow.from_row(row)

    def get_memo_by_shortcut(self, shortcut: str) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {self._MEMO_COLUMNS} FROM memos WHERE shortcut = ?",
                (shortcut,),
            ).fetchone()
        return MemoRow.from_row(row)

    def get_memo_by_uid(self, uid: str) -> MemoRow | None:
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT {self._MEMO_COLUMNS} FROM memos WHERE uid = ?",
                (uid,),
            ).fetchone()
        return MemoRow.from_row(row)

    def add_memo(
        self,
        uid: str,
        idx: int,
        shortcut: str | None,
        content: str,
        tags: str,
        created_at: str,
        modified_at: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, idx, shortcut or None, content, tags, created_at, modified_at),
            )

    def update_memo(
        self,
        memo_id: int,
        content: str,
        tags: str,
        shortcut: str | None,
        created_at: str,
        modified_at: str,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE memos SET content = ?, tags = ?, shortcut = ?, "
                "created_at = ?, modified_at = ? WHERE id = ?",
                (content.strip(), tags, shortcut or None, created_at, modified_at, memo_id),
            )

    def delete_memo(self, memo_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))
