"""Shared pytest fixtures."""

import pytest

from koda.db import MemoDatabase


@pytest.fixture
def db(tmp_path):
    """A fresh, initialized local SQLite MemoDatabase backed by a temp file."""
    database = MemoDatabase(backend="local", path=tmp_path / "test.db")
    database.init_db()
    return database
