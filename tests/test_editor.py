"""Tests for the shared editor resolution/launch helpers in runtime.

Covers the crash where an empty ``$EDITOR`` tried to exec ``""`` and raised a
raw ``PermissionError`` traceback instead of falling back to vim.
"""

import pytest
import typer

import koda.runtime as runtime


class TestResolveEditor:
    def test_unset_falls_back_to_vim(self, monkeypatch):
        monkeypatch.delenv("EDITOR", raising=False)
        assert runtime.resolve_editor() == ["vim"]

    @pytest.mark.parametrize("value", ["", "   ", "\t"])
    def test_empty_or_whitespace_falls_back_to_vim(self, monkeypatch, value):
        monkeypatch.setenv("EDITOR", value)
        assert runtime.resolve_editor() == ["vim"]

    def test_single_word(self, monkeypatch):
        monkeypatch.setenv("EDITOR", "nano")
        assert runtime.resolve_editor() == ["nano"]

    def test_multi_word_is_split(self, monkeypatch):
        monkeypatch.setenv("EDITOR", "code --wait")
        assert runtime.resolve_editor() == ["code", "--wait"]

    def test_unbalanced_quote_does_not_crash(self, monkeypatch):
        monkeypatch.setenv("EDITOR", 'vim "')
        # shlex.split raises on the unbalanced quote; we keep the raw string.
        assert runtime.resolve_editor() == ['vim "']


class TestLaunchEditor:
    def test_empty_editor_launches_vim_without_crash(self, monkeypatch):
        monkeypatch.setenv("EDITOR", "")
        captured = {}
        monkeypatch.setattr(
            "koda.runtime.subprocess.call", lambda cmd: captured.setdefault("cmd", cmd)
        )
        runtime.launch_editor("/tmp/x")
        assert captured["cmd"] == ["vim", "/tmp/x"]

    def test_multi_word_editor_is_passed_through(self, monkeypatch):
        monkeypatch.setenv("EDITOR", "code --wait")
        captured = {}
        monkeypatch.setattr(
            "koda.runtime.subprocess.call", lambda cmd: captured.setdefault("cmd", cmd)
        )
        runtime.launch_editor("/tmp/x")
        assert captured["cmd"] == ["code", "--wait", "/tmp/x"]

    def test_missing_editor_binary_exits_cleanly(self, monkeypatch):
        monkeypatch.setenv("EDITOR", "definitely-not-an-editor")

        def boom(cmd):
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr("koda.runtime.subprocess.call", boom)
        with pytest.raises(typer.Exit):
            runtime.launch_editor("/tmp/x")
