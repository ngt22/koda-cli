"""Importing the CLI must have no side effects (E3-5).

The runtime resolves its Config and MemoDatabase lazily so that importing
``koda.main`` (which pulls in ``koda.runtime`` and every command module) neither
reads configuration nor builds a DB handle. This keeps the package importable
without HOME/env and makes it testable.
"""

import subprocess
import sys

import pytest
import typer

import koda.runtime as runtime
from koda.config import Config
from koda.db import MemoDatabase


def test_import_has_no_side_effects():
    """A fresh interpreter importing koda.main leaves the caches unpopulated."""
    code = (
        "import koda.main; "
        "import koda.runtime as r; "
        "assert r._config is None, 'config loaded at import'; "
        "assert r._config_sources is None, 'config sources loaded at import'; "
        "assert r._config_manager is None, 'config manager built at import'; "
        "assert r._db is None, 'db built at import'; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_get_config_is_lazy_and_cached(monkeypatch):
    monkeypatch.setattr(runtime, "_config", None)
    monkeypatch.setattr(runtime, "_config_sources", None)

    cfg = runtime.get_config()
    assert isinstance(cfg, Config)
    # Second call returns the cached instance, not a fresh load.
    assert runtime.get_config() is cfg
    # Sources share the same cached load.
    assert isinstance(runtime.get_config_sources(), dict)


def test_get_db_is_lazy_and_cached(monkeypatch, tmp_path):
    cfg = Config()
    cfg.db_backend = "local"
    cfg.db_path = str(tmp_path / "lazy.db")
    cfg.turso_url = ""
    cfg.turso_token = ""

    # A temp path lives outside the koda data dir; the override env lets tests
    # use it (the same escape hatch CI relies on).
    monkeypatch.setenv("KODA_DB_PATH_OVERRIDE", "1")
    monkeypatch.setattr(runtime, "_db", None)
    monkeypatch.setattr(runtime, "get_config", lambda: cfg)

    db = runtime.get_db()
    assert isinstance(db, MemoDatabase)
    # Second call returns the cached handle.
    assert runtime.get_db() is db


def test_get_db_rejects_path_outside_data_dir(monkeypatch):
    """A local db.path outside the data dir (e.g. via KODA_DB_PATH injection)
    is refused before any file is created."""
    cfg = Config()
    cfg.db_backend = "local"
    cfg.db_path = "/tmp/evil.db"

    monkeypatch.delenv("KODA_DB_PATH_OVERRIDE", raising=False)
    monkeypatch.setattr(runtime, "_db", None)
    monkeypatch.setattr(runtime, "get_config", lambda: cfg)

    with pytest.raises(typer.Exit):
        runtime.get_db()
