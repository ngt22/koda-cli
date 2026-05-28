"""Tests that config and DB files/dirs are created with restrictive modes."""

import stat

from koda.config import ConfigManager
from koda.db import MemoDatabase


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_config_file_and_dir_modes(tmp_path):
    cfg_path = tmp_path / "cfgdir" / "config.toml"
    ConfigManager(config_path=cfg_path).write_raw({"exec": {"shell": "sh"}})
    assert _mode(cfg_path) == 0o600
    assert _mode(cfg_path.parent) == 0o700


def test_db_file_and_dir_modes(tmp_path):
    db_path = tmp_path / "dbdir" / "koda.db"
    MemoDatabase(backend="local", path=db_path).init_db()
    assert _mode(db_path) == 0o600
    assert _mode(db_path.parent) == 0o700
