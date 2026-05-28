"""Tests for ConfigManager.coerce and ConfigManager.validate."""

import shutil

import pytest

from koda.config import ConfigManager, ValidationError


class TestCoerce:
    @pytest.mark.parametrize("raw", ["true", "1", "yes", "TRUE", "Yes"])
    def test_bool_true(self, raw):
        assert ConfigManager.coerce("list.desc", raw) is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", "FALSE", "No"])
    def test_bool_false(self, raw):
        assert ConfigManager.coerce("list.desc", raw) is False

    def test_bool_invalid(self):
        with pytest.raises(ValidationError):
            ConfigManager.coerce("list.desc", "maybe")

    def test_int(self):
        assert ConfigManager.coerce("list.per_page", "20") == 20

    def test_int_invalid(self):
        with pytest.raises(ValidationError):
            ConfigManager.coerce("list.per_page", "abc")

    def test_list(self):
        assert ConfigManager.coerce("list.columns", '["idx", "content"]') == ["idx", "content"]

    def test_list_not_a_list(self):
        with pytest.raises(ValidationError):
            ConfigManager.coerce("list.columns", '"idx"')

    def test_list_invalid_json(self):
        with pytest.raises(ValidationError):
            ConfigManager.coerce("list.columns", "[idx,")

    def test_str_passthrough(self):
        assert ConfigManager.coerce("exec.shell", "bash") == "bash"

    def test_unknown_key_defaults_to_str(self):
        assert ConfigManager.coerce("unknown.key", "value") == "value"


class TestValidate:
    def test_defaults_cmd_valid(self):
        assert ConfigManager.validate("defaults.cmd", "list") == "list"

    def test_defaults_cmd_invalid(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("defaults.cmd", "nope")

    @pytest.mark.parametrize("value", [1, 20, 999])
    def test_per_page_valid(self, value):
        assert ConfigManager.validate("list.per_page", value) == value

    def test_per_page_below_min(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("list.per_page", 0)

    @pytest.mark.parametrize("value", [0, 5])
    def test_rows_valid(self, value):
        assert ConfigManager.validate("list.rows", value) == value

    def test_rows_negative(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("list.rows", -1)

    def test_truncate_negative(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("list.truncate", -1)

    def test_sort_by_valid(self):
        assert ConfigManager.validate("list.sort_by", "created_at") == "created_at"

    def test_sort_by_invalid(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("list.sort_by", "bogus")

    def test_columns_valid(self):
        cols = ["idx", "content", "tags"]
        assert ConfigManager.validate("list.columns", cols) == cols

    def test_columns_missing_idx(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("list.columns", ["content"])

    def test_columns_unknown_column(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("list.columns", ["idx", "nope"])

    def test_columns_empty(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("list.columns", [])

    def test_backend_valid(self):
        assert ConfigManager.validate("db.backend", "turso") == "turso"

    def test_backend_invalid(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("db.backend", "mysql")

    def test_payload_file_valid(self):
        assert ConfigManager.validate("git.payload_file", "koda-sync.jsonl") == "koda-sync.jsonl"

    @pytest.mark.parametrize("value", ["", "   ", "/abs/path.jsonl", "../escape.jsonl"])
    def test_payload_file_invalid(self, value):
        with pytest.raises(ValidationError):
            ConfigManager.validate("git.payload_file", value)

    def test_sync_format_valid(self):
        assert ConfigManager.validate("git.sync_format", "JSONL") == "JSONL"

    def test_sync_format_invalid(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("git.sync_format", "yaml")

    @pytest.mark.parametrize("key", ["db.path", "turso.url", "turso.token", "git.sync_path"])
    def test_no_validator_passthrough(self, key):
        assert ConfigManager.validate(key, "anything") == "anything"

    def test_unknown_key_passthrough(self):
        assert ConfigManager.validate("unknown.key", "x") == "x"


class TestExecShell:
    @pytest.mark.parametrize("shell", ["sh", "bash"])
    def test_allowlisted_resolvable_shell_valid(self, shell):
        if shutil.which(shell) is None:
            pytest.skip(f"{shell} not installed")
        assert ConfigManager.validate("exec.shell", shell) == shell

    def test_absolute_path_to_allowlisted_shell_valid(self):
        resolved = shutil.which("sh")
        if resolved is None:
            pytest.skip("sh not installed")
        assert ConfigManager.validate("exec.shell", resolved) == resolved

    def test_arbitrary_binary_rejected(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("exec.shell", "/tmp/evil")

    def test_non_allowlisted_name_rejected(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("exec.shell", "python")

    def test_nonexistent_allowlisted_shell_rejected(self, monkeypatch):
        monkeypatch.setattr("koda.config.shutil.which", lambda _: None)
        with pytest.raises(ValidationError):
            ConfigManager.validate("exec.shell", "bash")

    def test_empty_rejected(self):
        with pytest.raises(ValidationError):
            ConfigManager.validate("exec.shell", "")
