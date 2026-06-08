"""UX behaviors: empty-state onboarding, pagination hint, show timestamps,
empty-shortcut rejection."""

import pytest
import typer

import koda.runtime as runtime
from koda.cmd_helpers.display import print_memo
from koda.commands import memo
from koda.constants import TAG_SEPARATOR


@pytest.fixture
def wired_db(db, monkeypatch):
    monkeypatch.setattr(runtime, "_db", db)
    return db


def _seed(db, idx, content="body"):
    db.add_memo(
        uid=f"uid{idx:04d}",
        idx=idx,
        shortcut=None,
        content=content,
        tags=TAG_SEPARATOR.join(()),
        created_at="2026-01-01 00:00:00",
        modified_at="2026-01-01 00:00:00",
    )


def _list(db, query=None, tag=None, per_page=10, page=1):
    # Pass every config-derived arg explicitly so get_config() is never needed.
    memo._list_memos_impl(
        query, tag, None, False, per_page, page, "idx", False, "0", 0, ["idx", "content"]
    )


class TestEmptyState:
    def test_empty_db_shows_onboarding(self, wired_db, capsys):
        _list(wired_db)
        out = capsys.readouterr().out
        assert "No entries yet" in out
        assert "koda add" in out

    def test_filtered_no_match_shows_not_found(self, wired_db, capsys):
        _seed(wired_db, 0, "alpha")
        _list(wired_db, query="zzz-no-match")
        out = capsys.readouterr().out
        assert "No entries found" in out
        assert "koda add" not in out


class TestPaginationHint:
    def test_next_page_hint_when_more_pages(self, wired_db, capsys):
        for i in range(3):
            _seed(wired_db, i)
        _list(wired_db, per_page=2, page=1)
        out = capsys.readouterr().out
        assert "koda l -p 2" in out

    def test_no_hint_on_last_page(self, wired_db, capsys):
        for i in range(3):
            _seed(wired_db, i)
        _list(wired_db, per_page=2, page=2)
        out = capsys.readouterr().out
        assert "next: koda l" not in out


class TestShowTimestamps:
    def test_labels_created_and_modified(self, capsys):
        print_memo(
            "uid0001", 0, None, "body", "", "2026-01-01 00:00:00", "2026-02-01 00:00:00", "local"
        )
        out = capsys.readouterr().out
        assert "created: 2026-01-01 00:00:00" in out
        assert "modified: 2026-02-01 00:00:00" in out

    def test_modified_omitted_when_same_as_created(self, capsys):
        print_memo("uid0001", 0, None, "body", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
        out = capsys.readouterr().out
        assert "created:" in out
        assert "modified:" not in out

    def test_remote_source_flagged(self, capsys):
        print_memo(
            "uid0001", 0, None, "body", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00", "remote"
        )
        assert "source: remote" in capsys.readouterr().out


class TestEmptyShortcut:
    def test_empty_shortcut_rejected(self, wired_db):
        with pytest.raises(typer.Exit):
            memo._add_impl(text=["x"], shortcut="")

    def test_whitespace_shortcut_rejected(self, wired_db):
        with pytest.raises(typer.Exit):
            memo._add_impl(text=["x"], shortcut="   ")
