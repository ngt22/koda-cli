"""Tests for GitSyncPayload load/dump round-trips and parsing."""

import json

import pytest
import typer

from koda.config import Config
from koda.git_sync import GitSyncPayload, resolve_payload_path


def test_dump_empty_db(db):
    assert GitSyncPayload.dump(db) == b""


@pytest.mark.parametrize(
    "rel",
    [".git/hooks/post-merge", ".git/config", "sub/.git/hooks/post-merge", "../escape.jsonl"],
)
def test_resolve_payload_path_rejects_unsafe(tmp_path, rel):
    """Even when validate() is bypassed (file/env config), resolve_payload_path
    must refuse '.git' and traversal components before writing into the repo."""
    cfg = Config(git_payload_file=rel)
    with pytest.raises(typer.Exit):
        resolve_payload_path(cfg, tmp_path)


def test_resolve_payload_path_allows_normal(tmp_path):
    cfg = Config(git_payload_file="koda-sync.jsonl")
    assert resolve_payload_path(cfg, tmp_path) == (tmp_path / "koda-sync.jsonl")


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
            "title": None,
        },
        {
            "uid": "uid0002",
            "idx": 1,
            "shortcut": "gd",
            "content": "second",
            "tags": "work",
            "created_at": "2026-01-02 00:00:00",
            "modified_at": "2026-01-02 00:00:00",
            "title": None,
        },
    ]


def test_dump_is_sorted_by_uid(db):
    db.add_memo("uidzzzz", 0, None, "z", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    db.add_memo("uidaaaa", 1, None, "a", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    lines = GitSyncPayload.dump(db).decode().strip().splitlines()
    uids = [json.loads(line)["uid"] for line in lines]
    assert uids == sorted(uids)


def test_load_dedup_keeps_last_line():
    data = b'{"uid":"uid0001","idx":0,"content":"old"}\n{"uid":"uid0001","idx":0,"content":"new"}\n'
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

    def test_missing_title_becomes_none(self):
        # Legacy payload from an old peer: no 'title' field → None (backward compat).
        rec = GitSyncPayload.parse_record({"uid": "uid0001", "idx": 0}, 1)
        assert rec["title"] is None

    def test_empty_title_becomes_none(self):
        rec = GitSyncPayload.parse_record({"uid": "uid0001", "idx": 0, "title": ""}, 1)
        assert rec["title"] is None

    def test_title_preserved(self):
        rec = GitSyncPayload.parse_record({"uid": "uid0001", "idx": 0, "title": "Deploy"}, 1)
        assert rec["title"] == "Deploy"


def test_dump_emits_title(db):
    db.add_memo(
        "uid0001", 0, None, "first", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00", title="Hello"
    )
    line = GitSyncPayload.dump(db).decode().strip()
    assert json.loads(line)["title"] == "Hello"


def test_dump_emits_null_title_when_unset(db):
    db.add_memo("uid0001", 0, None, "first", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    line = GitSyncPayload.dump(db).decode().strip()
    assert json.loads(line)["title"] is None


def test_dump_load_round_trip_with_title(db):
    db.add_memo(
        "uid0001",
        0,
        None,
        "first",
        "",
        "2026-01-01 00:00:00",
        "2026-01-01 00:00:00",
        title="My Title",
    )
    loaded = GitSyncPayload.load(GitSyncPayload.dump(db))
    assert loaded[0]["title"] == "My Title"


def test_legacy_payload_line_without_title_parses():
    # A line emitted by a pre-title peer has no 'title' key at all.
    data = b'{"uid":"uid0001","idx":0,"content":"x"}\n'
    loaded = GitSyncPayload.load(data)
    assert loaded[0]["title"] is None


"""--- #158: read-only remote payload fetch ---"""
import subprocess


def _git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def sync_pair(tmp_path):
    """bare remote + clone のペアを作る。clone の初期ブランチ名は不問。"""
    remote = tmp_path / "remote.git"
    _git("init", "--bare", str(remote))
    clone = tmp_path / "clone"
    _git("clone", str(remote), str(clone))
    _git("-C", str(clone), "config", "user.name", "Test")
    _git("-C", str(clone), "config", "user.email", "test@example.com")
    return remote, clone


def test_fetch_remote_payload_reads_origin_blob(tmp_path, sync_pair):
    from koda.git_sync import GitSyncRepo

    _, clone = sync_pair
    (clone / "koda-sync.jsonl").write_bytes(b'{"uid":"uid0001","idx":0,"content":"x"}\n')
    _git("-C", str(clone), "add", "koda-sync.jsonl")
    _git("-C", str(clone), "commit", "-m", "seed")
    _git("-C", str(clone), "push", "-u", "origin", "HEAD")

    data = GitSyncRepo(clone).fetch_remote_payload("koda-sync.jsonl")
    assert data == b'{"uid":"uid0001","idx":0,"content":"x"}\n'


def test_fetch_remote_payload_missing_file_returns_none(tmp_path, sync_pair):
    from koda.git_sync import GitSyncRepo

    _, clone = sync_pair
    (clone / "other.txt").write_text("hello")
    _git("-C", str(clone), "add", "other.txt")
    _git("-C", str(clone), "commit", "-m", "seed")
    _git("-C", str(clone), "push", "-u", "origin", "HEAD")
    assert GitSyncRepo(clone).fetch_remote_payload("koda-sync.jsonl") is None


def test_fetch_remote_payload_does_not_touch_worktree(tmp_path, sync_pair):
    from koda.git_sync import GitSyncRepo

    _, clone = sync_pair

    marker = clone / "dirty.txt"
    marker.write_text("dirty")

    (clone / "koda-sync.jsonl").write_bytes(b'{"uid":"uid0001","idx":0,"content":"x"}\n')
    _git("-C", str(clone), "add", ".")
    _git("-C", str(clone), "commit", "-m", "seed")
    _git("-C", str(clone), "push", "-u", "origin", "HEAD")

    # Amend local commit and force-push to advance remote beyond clone's HEAD
    _git("-C", str(clone), "commit", "--allow-empty", "-m", "advance", "--amend")
    _git("-C", str(clone), "push", "-f", "origin", "HEAD")

    head_before = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()
    status_before = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"],
        capture_output=True, text=True
    ).stdout
    marker_before = marker.read_text()

    GitSyncRepo(clone).fetch_remote_payload("koda-sync.jsonl")

    assert marker.read_text() == marker_before

    head_after = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()
    assert head_after == head_before

    status_after = subprocess.run(
        ["git", "-C", str(clone), "status", "--porcelain"],
        capture_output=True, text=True
    ).stdout
    assert status_after == status_before
