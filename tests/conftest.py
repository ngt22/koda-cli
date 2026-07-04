"""Shared pytest fixtures for the vault-based (Markdown-native) storage.

The source of truth is one ``.md`` file per entry under ``<vault>/entries``; the
SQLite cache at ``<vault>/.koda/cache.db`` is derived and rebuilt by
``reconcile``. These fixtures wire ``koda.runtime`` to a throwaway vault under
``tmp_path`` and reset its lazy singletons so ``get_config()``/``get_db()``
resolve fresh against the temp environment.
"""

import pytest

import koda.runtime as runtime
from koda import md_store, reconcile
from koda.db import compute_uid


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A fresh vault at ``<tmp>/vault`` with ``entries/`` created.

    Sets ``KODA_VAULT_PATH`` at the temp vault, allows the out-of-$HOME path via
    ``KODA_DB_PATH_OVERRIDE``, points the config at a nonexistent file, and
    resets the four ``koda.runtime`` lazy singletons so nothing leaks between
    tests. Returns the vault ``Path``.
    """
    vault_dir = tmp_path / "vault"
    (vault_dir / "entries").mkdir(parents=True)
    monkeypatch.setenv("KODA_VAULT_PATH", str(vault_dir))
    monkeypatch.setenv("KODA_DB_PATH_OVERRIDE", "1")
    monkeypatch.setenv("KODA_CONFIG_PATH", str(tmp_path / "none.toml"))
    monkeypatch.setattr(runtime, "_config", None)
    monkeypatch.setattr(runtime, "_config_sources", None)
    monkeypatch.setattr(runtime, "_config_manager", None)
    monkeypatch.setattr(runtime, "_db", None)
    return vault_dir


@pytest.fixture
def db(vault):
    """The initialized cache, built from the vault (runs ``reconcile_all``)."""
    runtime.init_db()
    return runtime.get_db()


@pytest.fixture
def seed(db):
    """Create an entry exactly as ``koda add`` would and return its ``MemoRow``.

    Delegates to ``memo._write_new_entry`` (which uses the wired runtime), so the
    ``.md`` file is written and the cache is updated as koda-authored
    (``source='local'``). idx is auto-allocated sequentially from 0.
    """
    from koda.commands import memo

    def _seed(content, shortcut=None, tags="", title=None, description=None):
        entry = memo._write_new_entry(content, tags, shortcut, title, description)
        return db.get_memo_by_uid(entry.uid)

    return _seed


@pytest.fixture
def write_md(vault, db):
    """Write an entry ``.md`` with explicit fields (idx/uid/source/extra) and
    reconcile it into the cache. For tests that need a specific display index or
    a ``source='remote'`` (unreviewed) entry, which the plain ``seed`` fixture
    cannot express. Returns the resulting ``MemoRow``.
    """

    def _write(
        content,
        *,
        idx,
        uid=None,
        shortcut=None,
        tags="",
        title=None,
        description=None,
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
        source="local",
        extra=None,
    ):
        entries_dir = runtime.get_entries_dir()
        uid = uid or compute_uid(content, created_at)
        entry = md_store.MdEntry(
            content=content,
            uid=uid,
            idx=idx,
            shortcut=shortcut,
            tags=tags,
            title=title,
            description=description,
            created_at=created_at,
            modified_at=modified_at,
            extra=extra or {},
        )
        path = md_store.write_entry(entries_dir, entry)
        reconcile.sync_path(db, entries_dir, path, blessed=(source == "local"))
        return db.get_memo_by_uid(uid)

    return _write
