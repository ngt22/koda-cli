"""Tests for list display modes (--display/-d) and list.display config (#142)."""

import pytest
import typer

import koda.runtime as runtime
from koda.cmd_helpers.interactive import _fzf_line
from koda.commands import memo
from koda.config import ALL_KEYS, ConfigManager, ValidationError
from koda.models import MemoRow

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def wired_db(db, monkeypatch):
    """Point the lazy DB cache at a fresh temp database."""
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, content="body text", title=None, shortcut=None, idx=0):
    db.add_memo(
        f"uid{idx:04d}",
        idx,
        shortcut,
        content,
        "",
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00",
        title=title,
    )
    return db.get_memo_by_idx(idx)


# ── display mode: body ────────────────────────────────────────────────────────


def test_body_mode_shows_preview(wired_db, capsys):
    _seed(wired_db, content="line one\nline two", title="My Title")
    memo._list_memos_impl(display="body")
    out = capsys.readouterr().out
    assert "line one" in out


def test_body_mode_respects_rows(wired_db, capsys):
    _seed(wired_db, content="line one\nline two\nline three", title="My Title")
    memo._list_memos_impl(display="body", rows="1")
    out = capsys.readouterr().out
    # Should show first line and ellipsis, not second line
    assert "line one" in out
    assert "line three" not in out


def test_body_mode_footer_shows_display(wired_db, capsys):
    _seed(wired_db, content="hello")
    memo._list_memos_impl(display="body")
    out = capsys.readouterr().out
    assert "Display: body" in out


# ── display mode: title ───────────────────────────────────────────────────────


def test_title_mode_shows_title_when_set(wired_db, capsys):
    _seed(wired_db, content="actual body", title="Deploy prod")
    memo._list_memos_impl(display="title")
    out = capsys.readouterr().out
    assert "Deploy prod" in out


def test_title_mode_falls_back_to_body_when_unset(wired_db, capsys):
    _seed(wired_db, content="body preview text", title=None)
    memo._list_memos_impl(display="title")
    out = capsys.readouterr().out
    assert "body preview text" in out


def test_title_mode_footer_shows_display(wired_db, capsys):
    _seed(wired_db, content="hello")
    memo._list_memos_impl(display="title")
    out = capsys.readouterr().out
    assert "Display: title" in out


# ── display mode: full ────────────────────────────────────────────────────────


def test_full_mode_shows_all_lines(wired_db, capsys):
    _seed(wired_db, content="line one\nline two\nline three", title=None)
    # Even with rows=1 and truncate=10, full mode should show all lines
    memo._list_memos_impl(display="full", rows="1", truncate=10)
    out = capsys.readouterr().out
    assert "line one" in out
    assert "line two" in out
    assert "line three" in out


def test_full_mode_footer_shows_rows_all_truncate_off(wired_db, capsys):
    _seed(wired_db, content="hello")
    memo._list_memos_impl(display="full")
    out = capsys.readouterr().out
    assert "Rows: all" in out
    assert "Truncate: off" in out
    assert "Display: full" in out


# ── display mode: both ────────────────────────────────────────────────────────


def test_both_mode_shows_title_and_body(wired_db, capsys):
    _seed(wired_db, content="body line", title="My Title")
    memo._list_memos_impl(display="both")
    out = capsys.readouterr().out
    assert "My Title" in out
    assert "body line" in out


def test_both_mode_without_title_shows_body_preview(wired_db, capsys):
    _seed(wired_db, content="just body", title=None)
    memo._list_memos_impl(display="both")
    out = capsys.readouterr().out
    assert "just body" in out


def test_both_mode_footer_shows_display(wired_db, capsys):
    _seed(wired_db, content="hello")
    memo._list_memos_impl(display="both")
    out = capsys.readouterr().out
    assert "Display: both" in out


# ── default display mode from config ─────────────────────────────────────────


def test_default_display_is_title_from_config(wired_db, monkeypatch, capsys):
    """Default config list_display="title" should apply when --display not passed."""
    # Default Config().list_display is "title", no need to override
    _seed(wired_db, content="body text", title="Config Title")
    memo._list_memos_impl()  # no display= argument
    out = capsys.readouterr().out
    assert "Display: title" in out
    assert "Config Title" in out


def test_cli_flag_overrides_config_default(wired_db, monkeypatch, capsys):
    """Passing --display body should override the config default of title."""
    _seed(wired_db, content="body only", title="Some Title")
    # Explicitly call with body mode — title should not appear as Display marker
    memo._list_memos_impl(display="body")
    out = capsys.readouterr().out
    assert "Display: body" in out


def test_invalid_display_flag_exits(wired_db, capsys):
    """Passing an invalid --display value should call exit_error."""
    with pytest.raises(typer.Exit):
        memo.list_memos(
            ref=None,
            query=None,
            tag=None,
            exclude_tag=None,
            shortcuts_only=False,
            per_page=None,
            page=1,
            sort_by=None,
            desc=None,
            rows=None,
            truncate=None,
            columns=None,
            display="nonsense",
            json_output=False,
        )
    assert "Invalid --display" in capsys.readouterr().err


def test_invalid_display_case_insensitive_accepted(wired_db, capsys):
    """Mixed-case display mode should be normalised and accepted."""
    _seed(wired_db, content="hello")
    memo.list_memos(
        ref=None,
        query=None,
        tag=None,
        exclude_tag=None,
        shortcuts_only=False,
        per_page=None,
        page=1,
        sort_by=None,
        desc=None,
        rows=None,
        truncate=None,
        columns=None,
        display="BODY",
        json_output=False,
    )
    out = capsys.readouterr().out
    assert "Display: body" in out


# ── config: list.display in ALL_KEYS / validate / coerce ─────────────────────


def test_list_display_in_all_keys():
    assert "list.display" in ALL_KEYS


def test_list_display_valid_values():
    for mode in ("title", "body", "full", "both"):
        assert ConfigManager.validate("list.display", mode) == mode


def test_list_display_invalid_value():
    with pytest.raises(ValidationError):
        ConfigManager.validate("list.display", "fancy")


def test_list_display_coerce():
    assert ConfigManager.coerce("list.display", "full") == "full"


def test_list_display_default_is_title():
    from koda.config import Config

    assert Config().list_display == "title"


def test_list_display_in_example_template():
    from koda.config import EXAMPLE_TEMPLATE

    assert "display" in EXAMPLE_TEMPLATE
    assert "title" in EXAMPLE_TEMPLATE


# ── --columns: title column ───────────────────────────────────────────────────


def test_title_in_valid_list_columns():
    from koda.config import VALID_LIST_COLUMNS

    assert "title" in VALID_LIST_COLUMNS


def test_columns_with_title_renders(wired_db, capsys):
    _seed(wired_db, content="body text", title="Col Title")
    # Use display="body" so the content column shows the body,
    # while the separate title column shows the title.
    memo._list_memos_impl(columns=["idx", "title", "content"], display="body")
    out = capsys.readouterr().out
    assert "Col Title" in out
    assert "body text" in out


def test_list_columns_config_accepts_title():
    assert ConfigManager.validate("list.columns", ["idx", "title"]) == ["idx", "title"]


# ── _fzf_line helper ─────────────────────────────────────────────────────────


def _make_row(idx=0, content="", title=None, shortcut=None, tags=""):
    return MemoRow(
        id=1,
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=shortcut,
        content=content,
        tags=tags,
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
        source="local",
        title=title,
    )


def test_fzf_line_titled_entry_uses_title_as_label():
    row = _make_row(content="first body line", title="My Label")
    line = _fzf_line(row)
    fields = line.split("\t")
    # field 6 (index 5) is label
    assert fields[5] == "My Label"
    # field 7 (index 6) is first body line
    assert fields[6] == "first body line"


def test_fzf_line_untitled_entry_uses_first_line_as_label():
    row = _make_row(content="first body line\nsecond line", title=None)
    line = _fzf_line(row)
    fields = line.split("\t")
    assert fields[5] == "first body line"
    assert fields[6] == "first body line"


def test_fzf_line_tab_in_content_sanitized():
    row = _make_row(content="col1\tcol2", title=None)
    line = _fzf_line(row)
    fields = line.split("\t")
    # first_line and label should have tab replaced by space
    assert fields[5] == "col1 col2"
    assert fields[6] == "col1 col2"


def test_fzf_line_tab_in_title_sanitized():
    row = _make_row(content="body", title="title\twith tab")
    line = _fzf_line(row)
    fields = line.split("\t")
    assert fields[5] == "title with tab"


def test_fzf_line_field_count():
    row = _make_row(content="body", title="t")
    line = _fzf_line(row)
    assert len(line.split("\t")) == 7


def test_fzf_line_empty_content_no_title():
    row = _make_row(content="", title=None)
    line = _fzf_line(row)
    fields = line.split("\t")
    assert fields[5] == ""
    assert fields[6] == ""
