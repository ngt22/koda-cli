"""Database layer for koda: connection, schema, CRUD over the memos table."""

import hashlib
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import MemoRow

# Number of hex chars kept from the sha1 hash to form a uid. 16 hex = 64 bits,
# chosen so birthday/preimage attacks against the sync merge key are infeasible
# (the original 7 hex = 28 bits collided after ~16k entries). Widening is a
# schema migration; see _migration_0002_widen_uid.
UID_LENGTH = 16


def compute_uid(content: str, created_at: str) -> str:
    """Return the stable sync uid: first ``UID_LENGTH`` hex chars of
    ``sha1(content + created_at)``. Deterministic across machines, so two peers
    that hold the same entry derive the same uid (the merge key)."""
    raw = f"{content}{created_at}".encode()
    return hashlib.sha1(raw).hexdigest()[:UID_LENGTH]


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
    "title",
}


class DatabaseError(RuntimeError):
    """Backend configuration error (missing driver, missing URL, etc.)."""


def _migration_0001_initial_schema(conn) -> None:
    """Create the memos table, ensure the shortcut column and its index.

    This is the schema that predates versioning, so every statement is
    idempotent: applying it to an already-migrated database is a no-op,
    while a fresh database gets the full current schema.
    """
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


def _migration_0002_widen_uid(conn) -> None:
    """Widen uids from 7 to ``UID_LENGTH`` hex chars by recomputing each row's
    uid from its current content + created_at.

    uid = sha1(content + created_at)[:N], so for an unedited row the new uid
    keeps the old 7-char value as its prefix; synced peers therefore converge
    once both have migrated. A row whose content/created_at changed after
    creation (uid is not refreshed on edit) gets a uid consistent with its
    current text — its short prefix may change, which is why pull falls back to
    a uid-prefix match for legacy short uids (see MemoMerger)."""
    rows = conn.execute("SELECT id, content, created_at FROM memos").fetchall()
    for memo_id, content, created_at in rows:
        new_uid = compute_uid(content or "", created_at or "")
        conn.execute("UPDATE memos SET uid = ? WHERE id = ?", (new_uid, memo_id))


def _migration_0003_add_source(conn) -> None:
    """Add the ``source`` column: 'local' for entries authored or reviewed on
    this machine, 'remote' for entries brought in by a Git sync. Existing rows
    are the user's own work, so they default to 'local'. The column is local
    state only — it is never written to or read from the sync payload."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memos)").fetchall()}
    if "source" not in cols:
        conn.execute("ALTER TABLE memos ADD COLUMN source TEXT NOT NULL DEFAULT 'local'")


def _migration_0004_add_title(conn) -> None:
    """Add the nullable ``title`` column: a display-only, human-readable label.
    Existing rows have no title, so the column stays NULL until set. It is
    content (synced, unlike ``source``) but is never used to resolve a ref —
    ``shortcut`` remains the only callable name."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memos)").fetchall()}
    if "title" not in cols:
        conn.execute("ALTER TABLE memos ADD COLUMN title TEXT")


# Ordered list of schema migrations. Each entry N (0-based) advances the
# database from PRAGMA user_version N to N+1. Append new migrations to the
# end; never reorder or rewrite an existing one.
_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migration_0001_initial_schema,
    _migration_0002_widen_uid,
    _migration_0003_add_source,
    _migration_0004_add_title,
]

SCHEMA_VERSION = len(_MIGRATIONS)


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
        """Bring the schema up to date by applying pending migrations."""
        if self.backend == "local" and self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
        with self.connection() as conn:
            self._apply_migrations(conn)
        if self.backend == "local" and self.path is not None:
            os.chmod(self.path, 0o600)

    @staticmethod
    def _apply_migrations(conn) -> None:
        """Run any migrations newer than the DB's PRAGMA user_version.

        user_version starts at 0 (the default for databases created before
        versioning existed, and for brand-new ones). Migration N takes the
        schema from version N to N+1; we run them in order and bump
        user_version after each so a crash mid-upgrade resumes cleanly.
        """
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version in range(current, len(_MIGRATIONS)):
            _MIGRATIONS[version](conn)
            # PRAGMA does not accept bound parameters; version is a trusted int.
            conn.execute(f"PRAGMA user_version = {version + 1}")

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

    _MEMO_COLUMNS = "id, uid, idx, content, tags, shortcut, created_at, modified_at, source, title"

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
        """Return filtered, ordered memos. ``limit=None`` returns every match
        (formerly ``get_memos_all``); a non-None ``limit`` paginates via
        ``LIMIT/OFFSET``."""
        order_column = sort_by if sort_by in VALID_SORT_COLUMNS else "idx"
        order_direction = "DESC" if desc else "ASC"
        where_sql, params = self._filters(query, tag, exclude_tag, shortcuts_only)
        sql = (
            f"SELECT {self._MEMO_COLUMNS} FROM memos"
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

    @staticmethod
    def _uid_prefix_like(prefix: str) -> str:
        """Build a LIKE pattern matching uids that start with ``prefix``,
        escaping LIKE wildcards so a literal prefix is matched."""
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return escaped + "%"

    def get_memo_by_uid_prefix(self, prefix: str) -> MemoRow | None:
        """Resolve a memo by a uid prefix (e.g. a legacy 7-char uid against
        widened 16-char uids). Returns the single match, or None when there is
        no match or the prefix is ambiguous."""
        if not prefix:
            return None
        pattern = self._uid_prefix_like(prefix)
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT {self._MEMO_COLUMNS} FROM memos WHERE uid LIKE ? ESCAPE '\\' LIMIT 2",
                (pattern,),
            ).fetchall()
        if len(rows) != 1:
            return None
        return MemoRow.from_row(rows[0])

    def add_memo(
        self,
        uid: str,
        idx: int,
        shortcut: str | None,
        content: str,
        tags: str,
        created_at: str,
        modified_at: str,
        title: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO memos "
                "(uid, idx, shortcut, content, tags, created_at, modified_at, title) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, idx, shortcut or None, content, tags, created_at, modified_at, title),
            )

    def add_memo_auto_idx(
        self,
        uid: str,
        shortcut: str | None,
        content: str,
        tags: str,
        created_at: str,
        modified_at: str,
        title: str | None = None,
    ) -> int:
        """Insert a memo at the next free display index, allocated atomically in
        the same transaction, and return that idx. Raises ``IntegrityErrors`` on
        a shortcut clash so callers can report it. Centralizes the INSERT that
        ``add`` and ``copy`` previously inlined."""
        with self.connection() as conn:
            new_idx = self.next_idx(conn)
            conn.execute(
                "INSERT INTO memos "
                "(uid, idx, shortcut, content, tags, created_at, modified_at, title) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, new_idx, shortcut or None, content, tags, created_at, modified_at, title),
            )
        return new_idx

    def update_memo(
        self,
        memo_id: int,
        content: str,
        tags: str,
        shortcut: str | None,
        created_at: str,
        modified_at: str,
        title: str | None = None,
    ) -> None:
        with self.connection() as conn:
            # Editing an entry counts as reviewing it: reset source to 'local'
            # so a previously remote-synced entry no longer warns on exec.
            conn.execute(
                "UPDATE memos SET content = ?, tags = ?, shortcut = ?, "
                "created_at = ?, modified_at = ?, title = ?, source = 'local' WHERE id = ?",
                (content.strip(), tags, shortcut or None, created_at, modified_at, title, memo_id),
            )

    def delete_memo(self, memo_id: int) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))
