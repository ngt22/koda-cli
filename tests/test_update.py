"""Self-update tests: ``detect_install_method``, ``run_update``, CLI flags."""

import subprocess
import sys

import pytest
import typer

import koda.runtime as runtime
from koda.commands import update as _update  # noqa: F401  -- register subcommand
from koda.main import app

# -- helpers -------------------------------------------------------------------


def _which_uv(name):
    return "/usr/bin/uv" if name == "uv" else None


def _which_pipx(name):
    return "/usr/bin/pipx" if name == "pipx" else None


def _which_python3(name):
    return "/usr/bin/python3" if name == "python3" else None


# -- detect_install_method ----------------------------------------------------


class TestDetectInstallMethod:
    def test_detects_uv(self, monkeypatch):
        monkeypatch.setattr(runtime.shutil, "which", _which_uv)

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["/usr/bin/uv", "tool"]:
                stdout = "koda-cli v1.0\nother-tool"
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        assert runtime.detect_install_method() == "uv"

    def test_detects_pipx(self, monkeypatch):
        monkeypatch.setattr(runtime.shutil, "which", _which_pipx)

        def fake_run(cmd, **kwargs):
            if cmd[0] == "/usr/bin/pipx":
                stdout = "koda-cli 1.0\nother-pkg"
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        assert runtime.detect_install_method() == "pipx"

    def test_detects_venv(self, monkeypatch):
        monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
        monkeypatch.setattr(runtime.sys, "prefix", "/opt/venv")
        monkeypatch.setattr(runtime.sys, "base_prefix", "/usr")
        assert runtime.detect_install_method() == "venv-pip"

    def test_detects_pip(self, monkeypatch):
        monkeypatch.setattr(runtime.shutil, "which", _which_python3)
        monkeypatch.setattr(runtime.sys, "prefix", runtime.sys.base_prefix)
        assert runtime.detect_install_method() == "pip"

    def test_detects_none(self, monkeypatch):
        monkeypatch.setattr(runtime.shutil, "which", lambda _: None)
        monkeypatch.setattr(runtime.sys, "prefix", runtime.sys.base_prefix)
        assert runtime.detect_install_method() is None


# -- run_update ---------------------------------------------------------------


class TestRunUpdate:
    def test_runs_uv_upgrade(self, monkeypatch, capsys):
        monkeypatch.setattr(runtime, "detect_install_method", lambda: "uv")
        captured = None

        def fake_run(cmd):
            nonlocal captured
            captured = cmd
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        with pytest.raises(typer.Exit) as exc:
            runtime.run_update()
        assert exc.value.exit_code == 0
        assert captured == ["uv", "tool", "upgrade", "koda-cli"]

    def test_unknown_method_exits_1(self, monkeypatch, capsys):
        monkeypatch.setattr(runtime, "detect_install_method", lambda: None)
        with pytest.raises(typer.Exit) as exc:
            runtime.run_update()
        assert exc.value.exit_code == 1
        out = capsys.readouterr().out
        assert "Could not detect" in out

    def test_pipx_upgrade(self, monkeypatch):
        monkeypatch.setattr(runtime, "detect_install_method", lambda: "pipx")
        captured = None

        def fake_run(cmd):
            nonlocal captured
            captured = cmd
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        with pytest.raises(typer.Exit):
            runtime.run_update()
        assert captured == ["pipx", "upgrade", "koda-cli"]

    def test_venv_pip_upgrade(self, monkeypatch):
        monkeypatch.setattr(runtime, "detect_install_method", lambda: "venv-pip")
        captured = None

        def fake_run(cmd):
            nonlocal captured
            captured = cmd
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        with pytest.raises(typer.Exit):
            runtime.run_update()
        assert captured[:3] == [sys.executable, "-m", "pip"]
        assert "--upgrade" in captured
        assert "koda-cli" in captured

    def test_subprocess_oserror_handled(self, monkeypatch, capsys):
        monkeypatch.setattr(runtime, "detect_install_method", lambda: "uv")

        def fake_run(_cmd):
            raise OSError("no such binary")

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        with pytest.raises(typer.Exit) as exc:
            runtime.run_update()
        assert exc.value.exit_code == 1
        out = capsys.readouterr().out
        assert "Failed to run update" in out


# -- CLI level: --update flag ------------------------------------------------


class TestUpdateFlag:
    def test_update_flag_triggers(self, monkeypatch, capsys):
        monkeypatch.setattr(runtime, "detect_install_method", lambda: "uv")
        called = False

        def fake_run(_cmd):
            nonlocal called
            called = True
            return subprocess.CompletedProcess([], 0)

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        # app() in standalone mode converts typer.Exit -> SystemExit
        with pytest.raises(SystemExit):
            app(["--update"])
        assert called
        out = capsys.readouterr().out
        assert "uv" in out


# -- CLI level: update subcommand --------------------------------------------


def test_update_subcommand_registered_without_side_effect_import():
    """main.py の末尾 import だけで update が登録されること(回帰テスト)."""
    import koda.main as main_mod

    callbacks = {c.callback.__name__ for c in main_mod.app.registered_commands if c.callback}
    assert "update" in callbacks


class TestUpdateSubcommand:
    def test_update_subcommand_triggers(self, monkeypatch, capsys):
        monkeypatch.setattr(runtime, "detect_install_method", lambda: "uv")
        called = False

        def fake_run(_cmd):
            nonlocal called
            called = True
            return subprocess.CompletedProcess([], 0)

        monkeypatch.setattr(runtime.subprocess, "run", fake_run)
        with pytest.raises(SystemExit):
            app(["update"])
        assert called
        out = capsys.readouterr().out
        assert "uv" in out
