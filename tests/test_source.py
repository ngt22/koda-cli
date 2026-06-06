"""Sync source tracking (#45): merged entries are 'remote' and untrusted,
editing reviews them back to 'local', and the flag never crosses the wire.
Also covers MemoMerger.plan (the read-only `pull --dry-run` engine)."""

from koda.db import MemoDatabase
from koda.git_sync import GitSyncPayload, MemoMerger


def _entry(uid, idx, content="body", modified_at="2026-01-01 00:00:00", **extra):
    rec = {
        "uid": uid,
        "idx": idx,
        "shortcut": None,
        "content": content,
        "tags": "",
        "created_at": "2026-01-01 00:00:00",
        "modified_at": modified_at,
    }
    rec.update(extra)
    return rec


def test_add_memo_is_local(db: MemoDatabase):
    db.add_memo("uid00001", 0, None, "x", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00")
    assert db.get_memo_by_uid("uid00001").source == "local"


def test_merge_marks_inserted_remote(db: MemoDatabase):
    MemoMerger(db).merge([_entry("aaaaaaaa11111111", 0)])
    assert db.get_memo_by_uid("aaaaaaaa11111111").source == "remote"


def test_merge_marks_updated_remote(db: MemoDatabase):
    db.add_memo(
        "aaaaaaaa11111111", 0, None, "old", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    MemoMerger(db).merge(
        [_entry("aaaaaaaa11111111", 0, content="new", modified_at="2026-02-01 00:00:00")]
    )
    row = db.get_memo_by_uid("aaaaaaaa11111111")
    assert row.content == "new" and row.source == "remote"


def test_edit_resets_source_to_local(db: MemoDatabase):
    MemoMerger(db).merge([_entry("aaaaaaaa11111111", 0)])
    row = db.get_memo_by_uid("aaaaaaaa11111111")
    assert row.source == "remote"
    db.update_memo(row.id, "reviewed", "", None, row.created_at, "2026-03-01 00:00:00")
    assert db.get_memo_by_uid("aaaaaaaa11111111").source == "local"


def test_source_is_not_exported_in_payload(db: MemoDatabase):
    MemoMerger(db).merge([_entry("aaaaaaaa11111111", 0)])
    payload = GitSyncPayload.dump(db)
    assert b'"source"' not in payload


def test_payload_source_field_is_ignored(db: MemoDatabase):
    """A malicious payload cannot self-label an entry 'local' to dodge the
    exec confirmation — merged entries are always 'remote'."""
    MemoMerger(db).merge([_entry("aaaaaaaa11111111", 0, source="local")])
    assert db.get_memo_by_uid("aaaaaaaa11111111").source == "remote"


def test_loaded_payload_record_has_no_source_key():
    line = b'{"uid":"u1","idx":0,"content":"c","source":"local"}\n'
    (rec,) = GitSyncPayload.load(line)
    assert "source" not in rec


# ── pull --dry-run (MemoMerger.plan) ────────────────────────────────────────


def test_plan_classifies_insert_update_skip(db: MemoDatabase):
    db.add_memo(
        "existing0to0up00", 0, None, "old", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    db.add_memo(
        "existing0to0skip", 1, None, "cur", "", "2026-03-01 00:00:00", "2026-03-01 00:00:00"
    )

    entries = [
        _entry("brandnew00000000", 5, content="new entry"),
        _entry("existing0to0up00", 0, content="newer", modified_at="2026-02-01 00:00:00"),
        _entry("existing0to0skip", 1, content="older", modified_at="2026-01-01 00:00:00"),
    ]
    plan = MemoMerger(db).plan(entries)
    by_uid = {p["uid"]: p["action"] for p in plan}
    assert by_uid["brandnew00000000"] == "insert"
    assert by_uid["existing0to0up00"] == "update"
    assert by_uid["existing0to0skip"] == "skip"


def test_plan_does_not_mutate_database(db: MemoDatabase):
    db.add_memo(
        "existing00000000", 0, None, "cur", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    before = [(r.uid, r.content, r.source) for r in db.get_memos(limit=None)]

    MemoMerger(db).plan(
        [
            _entry("brandnew00000000", 5),
            _entry("existing00000000", 0, content="changed", modified_at="2026-09-01 00:00:00"),
        ]
    )
    after = [(r.uid, r.content, r.source) for r in db.get_memos(limit=None)]
    assert before == after


def test_plan_counts_match_merge(db: MemoDatabase):
    db.add_memo(
        "existing0to0up00", 0, None, "old", "", "2026-01-01 00:00:00", "2026-01-01 00:00:00"
    )
    entries = [
        _entry("brandnew00000000", 5),
        _entry("existing0to0up00", 0, content="newer", modified_at="2026-02-01 00:00:00"),
    ]
    plan = MemoMerger(db).plan(entries)
    inserts = sum(1 for p in plan if p["action"] == "insert")
    updates = sum(1 for p in plan if p["action"] == "update")

    ins, upd, _, _ = MemoMerger(db).merge(entries)
    assert (ins, upd) == (inserts, updates)
