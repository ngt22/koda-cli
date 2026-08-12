"""Tests for the Markdown file store (parse/render round-trip, slugs, trust).

The ``.md`` files are koda's source of truth, so the body must survive
byte-for-byte (only surrounding whitespace stripped) through a
render→parse cycle, unknown frontmatter keys must be preserved, and the
local-only ``source`` flag must never be written.
"""

import pytest

from koda import md_store
from koda.md_store import (
    MdEntry,
    filename_base,
    parse_md,
    render_md,
    slugify,
    split_frontmatter,
    unique_path,
)

# ── Body round-trip: nasty content must survive ──────────────────────────────

NASTY_BODIES = [
    "echo $1",
    r"echo \$(date)",
    "run `backticks` here",
    "before\n---\nafter",  # a literal --- line inside the body
    "line one\nline two\nline three",
    "日本語のメモ と emoji 🎉🔥",
    'printf "[%s]\\n" "$@"',
    "trailing spaces   \nand tabs\t\there",
    "${KEY} and ${1:-default}",
]


@pytest.mark.parametrize("body", NASTY_BODIES)
def test_body_roundtrip_byte_identical(body):
    """render→parse preserves the (stripped) body exactly."""
    entry = MdEntry(
        content=body,
        uid="abc1234567890def",
        idx=3,
        created_at="2026-07-04 10:30:15",
        modified_at="2026-07-04 10:30:15",
    )
    parsed = parse_md(render_md(entry))
    assert parsed.content == body.strip()


@pytest.mark.parametrize("body", NASTY_BODIES)
def test_body_roundtrip_full_field_equivalence(body):
    """A full entry round-trips through the koda-managed fields."""
    entry = MdEntry(
        content=body,
        uid="abc1234567890def",
        idx=7,
        shortcut="sc",
        tags="work,home",
        title="A Title",
        description="a summary",
        created_at="2026-07-04 10:30:15",
        modified_at="2026-07-05 11:00:00",
    )
    p = parse_md(render_md(entry))
    assert (p.content, p.uid, p.idx, p.shortcut, p.tags, p.title, p.description) == (
        body.strip(),
        "abc1234567890def",
        7,
        "sc",
        "work,home",
        "A Title",
        "a summary",
    )
    assert p.created_at == "2026-07-04 10:30:15"
    assert p.modified_at == "2026-07-05 11:00:00"


def test_literal_frontmatter_fence_in_body_is_safe():
    """Only the FIRST ---...--- block is frontmatter; --- in the body stays."""
    text = render_md(MdEntry(content="a\n---\nb", uid="u1", idx=0))
    parsed = parse_md(text)
    assert parsed.content == "a\n---\nb"


# ── Unknown frontmatter keys are preserved ───────────────────────────────────


def test_unknown_frontmatter_keys_preserved():
    entry = MdEntry(
        content="body",
        uid="u1",
        idx=0,
        extra={"type": "note", "aliases": ["x", "y"]},
    )
    parsed = parse_md(render_md(entry))
    assert parsed.extra == {"type": "note", "aliases": ["x", "y"]}


def test_title_with_colon_stays_valid():
    entry = MdEntry(content="body", uid="u1", idx=0, title="Deploy: prod")
    parsed = parse_md(render_md(entry))
    assert parsed.title == "Deploy: prod"


def test_empty_and_none_keys_omitted():
    """None/empty koda keys are not emitted into the frontmatter."""
    text = render_md(MdEntry(content="body", uid="u1", idx=0))
    assert "shortcut:" not in text
    assert "title:" not in text
    assert "description:" not in text
    assert "tags:" not in text


def test_source_is_never_written():
    """The local trust flag must never leak into the .md (it is not even a field
    of MdEntry, so rendering can never include it)."""
    entry = MdEntry(content="body", uid="u1", idx=0, title="t")
    text = render_md(entry)
    assert "source" not in text


def test_split_frontmatter_no_fence_is_all_body():
    fm, body = split_frontmatter("just a body\nno frontmatter")
    assert fm is None
    assert body == "just a body\nno frontmatter"


def test_parse_md_no_frontmatter():
    entry = parse_md("plain body only")
    assert entry.content == "plain body only"
    assert entry.uid is None
    assert entry.idx is None


def test_tags_scalar_and_list_normalize_to_comma_form():
    assert parse_md("---\ntags: [a, b, c]\n---\nx").tags == "a,b,c"
    assert parse_md("---\ntags: a,b\n---\nx").tags == "a,b"


# ── Slug fallback chain + collision handling ─────────────────────────────────


def test_slug_prefers_title():
    assert filename_base(MdEntry(content="body", title="My Title", shortcut="sc")) == "My-Title"


def test_slug_falls_back_to_shortcut():
    assert filename_base(MdEntry(content="body", shortcut="deploy", tags="t")) == "deploy"


def test_slug_falls_back_to_tags():
    assert filename_base(MdEntry(content="body", tags="work,home")) == "work-home"


def test_slug_falls_back_to_description():
    assert filename_base(MdEntry(content="body", description="a summary here")) == "a-summary-here"


def test_slug_falls_back_to_first_content_line():
    assert filename_base(MdEntry(content="first line\nsecond line")) == "first-line"


def test_slug_falls_back_to_datetime_when_nothing_usable():
    base = filename_base(MdEntry(content="   ", created_at="2026-07-04 10:30:15"))
    assert base == "2026-07-04-103015"


def test_slug_datetime_default_when_no_created_at():
    assert filename_base(MdEntry(content="")) == "entry"


def test_slug_preserves_unicode():
    assert slugify("日本語 メモ") == "日本語-メモ"


def test_unique_path_collision_appends_uid_fragment(tmp_path):
    (tmp_path / "note.md").write_text("existing")
    p = unique_path(tmp_path, "note", "deadbeefcafef00d")
    assert p.name == "note-dead.md"


def test_unique_path_datetime_collision_suffix(tmp_path):
    """Two entries whose only slug source is an identical datetime get distinct
    filenames via the uid fragment."""
    e1 = MdEntry(content="", uid="a" * 16, created_at="2026-07-04 10:30:15")
    e2 = MdEntry(content="", uid="b" * 16, created_at="2026-07-04 10:30:15")
    p1 = md_store.write_entry(tmp_path, e1)
    p2 = md_store.write_entry(tmp_path, e2)
    assert p1 != p2
    assert p1.name == "2026-07-04-103015.md"
    assert p2.name.startswith("2026-07-04-103015-")


def test_write_entry_reuses_given_path(tmp_path):
    entry = MdEntry(content="body", uid="u1", idx=0, title="Named")
    path = tmp_path / "sticky.md"
    returned = md_store.write_entry(tmp_path, entry, path=path)
    assert returned == path
    assert md_store.read_entry(path).content == "body"
