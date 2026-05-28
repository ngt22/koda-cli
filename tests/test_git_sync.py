"""Tests for GitSyncPayload load/dump round-trips and parsing."""

import json

import pytest

from koda.git_sync import GitSyncPayload


def test_dump_empty_db(db):
    assert GitSyncPayload.dump(db) == b""


def test_load_empty_bytes():
    assert GitSyncPayload.load(b"") == []
    assert GitSyncPayload.load(b"   \n  ") == []


def test_dump_load_round_trip(db):
    db.add_memo("uid0002", 1, "gd", "second", "work", "2026-01-02 00:00:00", "2026-01-02 00:00:00")
    db.add_memo("uid0001", 0, None, "first", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")

    payload = GitSyncPayload.dump(db)
    loaded = GitSyncPayload.load(payload)

    assert loaded == [
        {
            "uid": "uid0001",
            "idx": 0,
            "shortcut": None,
            "content": "first",
            "tags": "",
            "created_at": "2026-01-01 00:00:00",
            "modified_at": "2026-01-01 00:00:00",
        },
        {
            "uid": "uid0002",
            "idx": 1,
            "shortcut": "gd",
            "content": "second",
            "tags": "work",
            "created_at": "2026-01-02 00:00:00",
            "modified_at": "2026-01-02 00:00:00",
        },
    ]


def test_dump_is_sorted_by_uid(db):
    db.add_memo("uidzzzz", 0, None, "z", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    db.add_memo("uidaaaa", 1, None, "a", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    lines = GitSyncPayload.dump(db).decode().strip().splitlines()
    uids = [json.loads(line)["uid"] for line in lines]
    assert uids == sorted(uids)


def test_load_dedup_keeps_last_line():
    data = (
        b'{"uid":"uid0001","idx":0,"content":"old"}\n'
        b'{"uid":"uid0001","idx":0,"content":"new"}\n'
    )
    loaded = GitSyncPayload.load(data)
    assert len(loaded) == 1
    assert loaded[0]["content"] == "new"


def test_load_skips_blank_lines():
    data = b'{"uid":"uid0001","idx":0}\n\n   \n{"uid":"uid0002","idx":1}\n'
    assert len(GitSyncPayload.load(data)) == 2


def test_load_invalid_json_raises():
    with pytest.raises(ValueError):
        GitSyncPayload.load(b"{not json}\n")


def test_load_missing_uid_raises():
    with pytest.raises(ValueError):
        GitSyncPayload.load(b'{"idx":0,"content":"x"}\n')


def test_load_missing_idx_raises():
    with pytest.raises(ValueError):
        GitSyncPayload.load(b'{"uid":"uid0001","content":"x"}\n')


def test_load_non_object_line_raises():
    with pytest.raises(ValueError):
        GitSyncPayload.load(b'["not", "an", "object"]\n')


def test_load_invalid_utf8_raises():
    with pytest.raises(ValueError):
        GitSyncPayload.load(b'\xff\xfe{"uid":"x","idx":0}')


class TestParseRecordDefaults:
    def test_null_content_and_tags_become_empty(self):
        rec = GitSyncPayload.parse_record(
            {"uid": "uid0001", "idx": 0, "content": None, "tags": None}, 1
        )
        assert rec["content"] == ""
        assert rec["tags"] == ""

    def test_empty_shortcut_becomes_none(self):
        rec = GitSyncPayload.parse_record({"uid": "uid0001", "idx": 0, "shortcut": ""}, 1)
        assert rec["shortcut"] is None

    def test_modified_at_defaults_to_created_at(self):
        rec = GitSyncPayload.parse_record(
            {"uid": "uid0001", "idx": 0, "created_at": "2026-01-01 00:00:00"}, 1
        )
        assert rec["modified_at"] == "2026-01-01 00:00:00"

    def test_idx_coerced_from_numeric_string(self):
        rec = GitSyncPayload.parse_record({"uid": "uid0001", "idx": "5"}, 1)
        assert rec["idx"] == 5
