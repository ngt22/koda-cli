"""Importing koda.main must have no side effects (E3-5).

The module resolves its Config and MemoDatabase lazily so that
``import koda.main`` neither reads configuration nor builds a DB handle.
This keeps the module importable without HOME/env and makes it testable.
"""

import subprocess
import sys

import koda.main as main
from koda.config import Config
from koda.db import MemoDatabase


def test_import_has_no_side_effects():
    """A fresh interpreter importing koda.main leaves the caches unpopulated."""
    code = (
        "import koda.main as m; "
        "assert m._config is None, 'config loaded at import'; "
        "assert m._config_sources is None, 'config sources loaded at import'; "
        "assert m._config_manager is None, 'config manager built at import'; "
        "assert m._db is None, 'db built at import'; "
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
    monkeypatch.setattr(main, "_config", None)
    monkeypatch.setattr(main, "_config_sources", None)

    cfg = main.get_config()
    assert isinstance(cfg, Config)
    # Second call returns the cached instance, not a fresh load.
    assert main.get_config() is cfg
    # Sources share the same cached load.
    assert isinstance(main.get_config_sources(), dict)


def test_get_db_is_lazy_and_cached(monkeypatch, tmp_path):
    cfg = Config()
    cfg.db_backend = "local"
    cfg.db_path = str(tmp_path / "lazy.db")
    cfg.turso_url = ""
    cfg.turso_token = ""

    monkeypatch.setattr(main, "_db", None)
    monkeypatch.setattr(main, "get_config", lambda: cfg)

    db = main.get_db()
    assert isinstance(db, MemoDatabase)
    # Second call returns the cached handle.
    assert main.get_db() is db
