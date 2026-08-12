"""Tests for ConfigManager.coerce and ConfigManager.validate."""

import shutil

import pytest

from koda.config import ConfigManager, ValidationError, toml_basic_string


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

    def test_no_validator_passthrough(self):
        # exec.confirm_remote and list.desc are plain bools with no validator.
        assert ConfigManager.validate("exec.confirm_remote", True) is True

    def test_unknown_key_passthrough(self):
        assert ConfigManager.validate("unknown.key", "x") == "x"


class TestVaultPath:
    def test_default_path_valid(self):
        from koda.config import DEFAULT_VAULT_DIR

        assert ConfigManager.validate("vault.path", str(DEFAULT_VAULT_DIR)) == str(
            DEFAULT_VAULT_DIR
        )

    def test_path_under_home_valid(self, monkeypatch):
        from pathlib import Path

        monkeypatch.delenv("KODA_DB_PATH_OVERRIDE", raising=False)
        path = str(Path.home() / "notes" / "vault")
        assert ConfigManager.validate("vault.path", path) == path

    @pytest.mark.parametrize(
        "path",
        ["/etc/koda", "/tmp/evil-vault"],
    )
    def test_outside_home_invalid(self, path, monkeypatch):
        monkeypatch.delenv("KODA_DB_PATH_OVERRIDE", raising=False)
        with pytest.raises(ValidationError):
            ConfigManager.validate("vault.path", path)

    def test_override_env_allows_arbitrary_path(self, monkeypatch):
        monkeypatch.setenv("KODA_DB_PATH_OVERRIDE", "1")
        assert ConfigManager.validate("vault.path", "/tmp/evil-vault") == "/tmp/evil-vault"


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


class TestTomlBasicString:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("plain", '"plain"'),
            ('he said "hi"', '"he said \\"hi\\""'),
            ("a\\b", '"a\\\\b"'),
            ("line1\nline2", '"line1\\nline2"'),
            ("tab\there", '"tab\\there"'),
        ],
    )
    def test_escapes(self, value, expected):
        assert toml_basic_string(value) == expected

    def test_control_char_uses_unicode_escape(self):
        assert toml_basic_string("\x00") == '"\\u0000"'


class TestWriteRawInjection:
    """write_raw must not let a value break out of its string and inject keys."""

    def _roundtrip(self, tmp_path, data):
        import sys

        if sys.version_info >= (3, 11):
            import tomllib
        else:
            import tomli as tomllib

        mgr = ConfigManager(config_path=tmp_path / "config.toml")
        mgr.write_raw(data)
        with open(mgr.config_path, "rb") as f:
            return tomllib.load(f)

    def test_token_with_quotes_and_newlines_roundtrips(self, tmp_path):
        # A token crafted to inject `[exec] confirm_remote = false`.
        evil = 'foo"\n\n[exec]\nconfirm_remote = false\n'
        parsed = self._roundtrip(tmp_path, {"turso": {"token": evil}})
        # The value survives verbatim and no injected table appears.
        assert parsed == {"turso": {"token": evil}}
        assert "exec" not in parsed

    def test_backslash_value_roundtrips(self, tmp_path):
        parsed = self._roundtrip(tmp_path, {"git": {"sync_path": "C:\\Users\\me"}})
        assert parsed["git"]["sync_path"] == "C:\\Users\\me"

    def test_list_items_with_quotes_roundtrip(self, tmp_path):
        parsed = self._roundtrip(tmp_path, {"list": {"columns": ['id"x', "content"]}})
        assert parsed["list"]["columns"] == ['id"x', "content"]

    def test_bool_and_int_still_written_unquoted(self, tmp_path):
        parsed = self._roundtrip(tmp_path, {"list": {"desc": True, "per_page": 20}})
        assert parsed["list"] == {"desc": True, "per_page": 20}


class TestExecConfirmRemote:
    def test_default_is_true(self):
        from koda.config import Config

        assert Config().exec_confirm_remote is True

    @pytest.mark.parametrize("raw,expected", [("false", False), ("0", False), ("true", True)])
    def test_coerce_bool(self, raw, expected):
        assert ConfigManager.coerce("exec.confirm_remote", raw) is expected

    def test_coerce_invalid(self):
        with pytest.raises(ValidationError):
            ConfigManager.coerce("exec.confirm_remote", "maybe")

    def test_present_in_config_example_template(self):
        from koda.config import EXAMPLE_TEMPLATE

        assert "confirm_remote" in EXAMPLE_TEMPLATE
        # The security caveat is spelled out for users about to flip it.
        assert "DISABLES" in EXAMPLE_TEMPLATE
