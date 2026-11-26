from _helpers import put_entry

from koda.commands import memo


def test_tag_keeps_remote_entry_untrusted(db):
    put_entry("echo evil", idx=0, uid="rem00010000", source="remote")
    memo.tag(["0"], tags=["x"], untag=None, dry_run=False, quiet=True)
    row = db.get_memo_by_idx(0)
    assert row.tags == "x"
    assert row.source == "remote"


def test_tag_keeps_local_entry_trusted(db):
    put_entry("echo ok", idx=0, uid="loc00010000")
    memo.tag(["0"], tags=["x"], untag=None, dry_run=False, quiet=True)
    assert db.get_memo_by_idx(0).source == "local"


def test_copy_of_remote_entry_stays_untrusted(db):
    put_entry("echo evil", idx=0, uid="rem00010000", source="remote")
    memo.copy("0")
    copied = db.get_memo_by_idx(1)
    assert copied.content == "echo evil"
    assert copied.source == "remote"


def test_copy_of_local_entry_is_trusted(db):
    put_entry("echo ok", idx=0, uid="loc00010000")
    memo.copy("0")
    assert db.get_memo_by_idx(1).source == "local"
