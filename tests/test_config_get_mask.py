"""`config get` must not leak turso.token in clear text unless --reveal.

Keeps the secret out of shell history, CI logs, and terminal scrollback,
matching the masking already applied by `config show`.
"""

import pytest

from koda.commands import config as config_cmd
from koda.config import Config


@pytest.fixture
def wired_config(monkeypatch):
    cfg = Config()
    cfg.turso_token = "super-secret-token"
    cfg.defaults_cmd = "raw"
    monkeypatch.setattr(config_cmd, "get_config", lambda: cfg)
    return cfg


def test_token_masked_by_default(wired_config, capsys):
    config_cmd.config_get("turso.token", reveal=False)
    out = capsys.readouterr().out
    assert out.strip() == "****"
    assert "super-secret-token" not in out


def test_token_revealed_with_flag(wired_config, capsys):
    config_cmd.config_get("turso.token", reveal=True)
    assert capsys.readouterr().out.strip() == "super-secret-token"


def test_empty_token_not_masked(monkeypatch, capsys):
    cfg = Config()  # turso_token defaults to ""
    monkeypatch.setattr(config_cmd, "get_config", lambda: cfg)
    config_cmd.config_get("turso.token", reveal=False)
    # An unset token prints as empty, not as "****".
    assert capsys.readouterr().out.strip() == ""


def test_non_secret_key_unaffected(wired_config, capsys):
    config_cmd.config_get("defaults.cmd", reveal=False)
    assert capsys.readouterr().out.strip() == "raw"
