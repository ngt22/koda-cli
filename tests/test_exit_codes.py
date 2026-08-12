"""Exit codes are distinct so scripts can branch on the failure kind."""

import pytest
import typer
from _helpers import put_entry

import koda.runtime as runtime
from koda.cli_utils import ExitCode
from koda.commands import memo


@pytest.fixture
def wired_db(db):
    return db


class TestResolveRefNotFound:
    def test_latest_on_empty_db(self, wired_db):
        with pytest.raises(typer.Exit) as exc:
            runtime.resolve_ref(None)
        assert exc.value.exit_code == ExitCode.NOT_FOUND

    def test_missing_index(self, wired_db):
        with pytest.raises(typer.Exit) as exc:
            runtime.resolve_ref("999")
        assert exc.value.exit_code == ExitCode.NOT_FOUND

    def test_missing_shortcut(self, wired_db):
        with pytest.raises(typer.Exit) as exc:
            runtime.resolve_ref("nope")
        assert exc.value.exit_code == ExitCode.NOT_FOUND


class TestCancelExitCode:
    def test_declined_remove_exits_cancelled(self, wired_db, monkeypatch):
        put_entry("keep me", idx=0, uid="uid0001")
        monkeypatch.setattr("koda.commands.memo.confirm", lambda *a, **k: False)
        with pytest.raises(typer.Exit) as exc:
            memo.rm(indices=["0"], tag=None, query=None, all_entries=False, force=False)
        assert exc.value.exit_code == ExitCode.CANCELLED
        # The entry survives a declined confirmation.
        assert wired_db.get_memo_by_idx(0) is not None
