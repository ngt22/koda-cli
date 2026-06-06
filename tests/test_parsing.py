"""Tests for CLI argument parsing helpers."""

import pytest
import typer

from koda.cmd_helpers.parsing import parse_indices, parse_tag_args, parse_var_items
from koda.main import _strip_inline_comment


class TestParseIndices:
    def test_single_indices(self):
        assert parse_indices(["5", "3"]) == [5, 3]

    def test_inclusive_range(self):
        assert parse_indices(["10-12"]) == [10, 11, 12]

    def test_mixed_single_and_range(self):
        assert parse_indices(["1", "3-5", "8"]) == [1, 3, 4, 5, 8]

    def test_empty(self):
        assert parse_indices([]) == []

    def test_single_element_range(self):
        assert parse_indices(["7-7"]) == [7]

    @pytest.mark.parametrize("spec", ["abc", "1-", "-3", "1-2-3", "1.5", ""])
    def test_invalid_spec_exits(self, spec):
        with pytest.raises(typer.Exit):
            parse_indices([spec])


class TestParseTagArgs:
    def test_none(self):
        assert parse_tag_args(None) == []

    def test_comma_split_and_flatten(self):
        assert parse_tag_args(["a,b", "c"]) == ["a", "b", "c"]

    def test_whitespace_stripped(self):
        assert parse_tag_args([" a , b "]) == ["a", "b"]

    def test_empty_segments_discarded(self):
        assert parse_tag_args(["a,,b", " , "]) == ["a", "b"]


class TestParseVarItems:
    def test_simple_csv(self):
        assert parse_var_items("a,b,c") == ["a", "b", "c"]

    def test_quoted_comma(self):
        assert parse_var_items('"a,b",c') == ["a,b", "c"]

    def test_skipinitialspace(self):
        assert parse_var_items("a, b, c") == ["a", "b", "c"]

    def test_empty_string(self):
        assert parse_var_items("") == []

    def test_empty_quotes(self):
        assert parse_var_items('""') == [""]


class TestStripInlineComment:
    def test_trailing_comment(self):
        assert _strip_inline_comment("echo hi  # greeting") == "echo hi"

    def test_comment_at_start(self):
        assert _strip_inline_comment("# whole line") == ""

    def test_hash_without_leading_space_kept(self):
        assert _strip_inline_comment("color=#fff") == "color=#fff"

    def test_hash_in_single_quotes_preserved(self):
        assert _strip_inline_comment("echo 'a # b'") == "echo 'a # b'"

    def test_hash_in_double_quotes_preserved(self):
        assert _strip_inline_comment('echo "a # b"') == 'echo "a # b"'

    def test_escaped_quote_handled(self):
        assert _strip_inline_comment(r"echo \# literal") == r"echo \# literal"

    def test_no_comment(self):
        assert _strip_inline_comment("plain text") == "plain text"
