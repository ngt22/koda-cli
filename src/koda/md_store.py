"""Markdown file store: one entry = one ``.md`` (YAML frontmatter + raw body).

The ``.md`` files under ``<vault>/entries`` are koda's source of truth. This
module is the only place that knows the on-disk format; it is deliberately pure
(no database, no config) so it round-trips byte-for-byte and is easy to test.

Format::

    ---
    title: ...        # human-facing keys first (sort_keys=False)
    shortcut: ...
    tags: [a, b]
    description: ...
    <preserved user/Obsidian keys, e.g. type, aliases>
    created: 2026-07-04 10:30:15
    updated: 2026-07-04 10:30:15
    uid: a1b2c3d4e5f6a7b8   # machine keys last
    idx: 7
    ---
    <content, stored verbatim>

Rules:
- The **body is the content, stored raw** (only surrounding whitespace is
  stripped, matching koda's long-standing ``content.strip()`` behaviour). No
  fencing, no escaping — ``$1``, ``\\$(...)``, backticks and ``---`` all survive.
- Frontmatter is the FIRST ``---...---`` block only; ``---`` inside the body is
  safe.
- Empty/None keys are omitted. ``source`` (local/remote trust) is NEVER written.
- Unknown frontmatter keys are preserved on round-trip (``extra``), so users can
  add Obsidian properties freely.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .constants import TAG_SEPARATOR
from .db import compute_uid

# Known frontmatter keys koda manages, mapped to internal field names. Anything
# not in here is preserved verbatim in ``extra`` (e.g. ``type``, Obsidian props).
_FM_TO_FIELD = {
    "title": "title",
    "shortcut": "shortcut",
    "tags": "tags",
    "description": "description",
    "created": "created_at",
    "updated": "modified_at",
    "uid": "uid",
    "idx": "idx",
}
# Order human keys first, machine keys last; ``extra`` is inserted in between.
_HUMAN_FM_ORDER = ["title", "shortcut", "tags", "description"]
_MACHINE_FM_ORDER = ["created", "updated", "uid", "idx"]

# Filesystem-unsafe characters plus separators we collapse to '-' in filenames.
_UNSAFE_RE = re.compile(r'[/\\:*?"<>|,\s\x00-\x1f]+')


class _FrontmatterLoader(yaml.SafeLoader):
    """SafeLoader that does NOT auto-convert timestamps, so ``created``/``updated``
    stay plain strings (exact round-trip; koda controls the format)."""


# Drop the implicit timestamp resolver so `2026-07-04 10:30:15` loads as a str.
for _ch, _resolvers in list(_FrontmatterLoader.yaml_implicit_resolvers.items()):
    _FrontmatterLoader.yaml_implicit_resolvers[_ch] = [
        (tag, regexp) for tag, regexp in _resolvers if tag != "tag:yaml.org,2002:timestamp"
    ]


@dataclass
class MdEntry:
    """Parsed representation of one ``.md`` file (no cache id)."""

    content: str = ""
    uid: str | None = None
    idx: int | None = None
    shortcut: str | None = None
    tags: str = ""  # internal comma-joined form
    title: str | None = None
    description: str | None = None
    created_at: str | None = None
    modified_at: str | None = None
    extra: dict = field(default_factory=dict)

    @property
    def body_hash(self) -> str:
        """sha1 of the (stripped) content — the trust-ledger fingerprint."""
        return hashlib.sha1((self.content or "").encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Parse / render
# --------------------------------------------------------------------------- #
def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return ``(frontmatter_yaml, body)``. ``frontmatter_yaml`` is None when the
    file has no leading ``---...---`` block (the whole file is then the body)."""
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines(keepends=True)
    if lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return "".join(lines[1:i]), "".join(lines[i + 1 :])
    return None, text  # no closing fence → treat everything as body


def _coerce_str(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_tags(value) -> str:
    """Accept a YAML list or a scalar; return koda's internal comma form."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [str(t).strip() for t in value]
    else:
        parts = [t.strip() for t in str(value).split(TAG_SEPARATOR)]
    return TAG_SEPARATOR.join(p for p in parts if p)


def parse_md(text: str) -> MdEntry:
    """Parse ``.md`` text into an :class:`MdEntry` (frontmatter + raw body)."""
    fm_yaml, body = split_frontmatter(text)
    entry = MdEntry(content=body.strip())
    if not fm_yaml:
        return entry
    data = yaml.load(fm_yaml, Loader=_FrontmatterLoader) or {}
    if not isinstance(data, dict):
        return entry
    for key, value in data.items():
        target = _FM_TO_FIELD.get(key)
        if target == "tags":
            entry.tags = _normalize_tags(value)
        elif target == "idx":
            try:
                entry.idx = int(value)
            except (TypeError, ValueError):
                entry.idx = None
        elif target in {"uid", "shortcut", "title", "description", "created_at", "modified_at"}:
            setattr(entry, target, _coerce_str(value))
        else:
            entry.extra[key] = value  # preserved unknown key
    return entry


def render_md(entry: MdEntry) -> str:
    """Render an :class:`MdEntry` back to ``.md`` text (frontmatter + raw body)."""
    fm: dict = {}
    values = {
        "title": entry.title,
        "shortcut": entry.shortcut,
        "tags": [t for t in (entry.tags or "").split(TAG_SEPARATOR) if t],
        "description": entry.description,
        "created": entry.created_at,
        "updated": entry.modified_at,
        "uid": entry.uid,
        "idx": entry.idx,
    }
    # Human keys first.
    for key in _HUMAN_FM_ORDER:
        v = values[key]
        if v not in (None, "", []):
            fm[key] = v
    # Preserved unknown keys (e.g. type, Obsidian props) in their original order.
    for key, v in entry.extra.items():
        fm[key] = v
    # Machine keys last.
    for key in _MACHINE_FM_ORDER:
        v = values[key]
        if v not in (None, "", []):
            fm[key] = v

    body = (entry.content or "").strip()
    if not fm:
        return body + "\n" if body else ""
    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{fm_text}---\n{body}\n" if body else f"---\n{fm_text}---\n"


# --------------------------------------------------------------------------- #
# File IO
# --------------------------------------------------------------------------- #
def read_entry(path: Path) -> MdEntry:
    """Read and parse a ``.md`` file."""
    return parse_md(path.read_text(encoding="utf-8"))


def write_entry(entries_dir: Path, entry: MdEntry, path: Path | None = None) -> Path:
    """Write ``entry`` to disk. Reuse ``path`` when given (sticky filename for an
    existing entry); otherwise allocate a fresh unique filename. Returns the path.
    """
    entries_dir.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = unique_path(entries_dir, filename_base(entry), entry.uid or "")
    path.write_text(render_md(entry), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Filenames (sticky, Unicode-preserving, FS-safe)
# --------------------------------------------------------------------------- #
def slugify(text: str | None, max_len: int = 60) -> str:
    """FS-safe, Unicode-preserving slug: collapse unsafe chars/whitespace to '-',
    trim, cap length. Returns '' when nothing usable remains."""
    if not text:
        return ""
    slug = _UNSAFE_RE.sub("-", str(text)).strip("-. ")
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-. ")
    return slug


def _datetime_slug(created_at: str | None) -> str:
    """``2026-07-04 10:30:15`` → ``2026-07-04-103015``. Always yields something."""
    raw = (created_at or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2}):(\d{2})", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}{m.group(3)}{m.group(4)}"
    slug = slugify(raw)
    return slug or "entry"


def filename_base(entry: MdEntry) -> str:
    """Fallback chain for the filename stem:
    title → shortcut → tags → description → first content line → datetime."""
    first_line = ((entry.content or "").strip().splitlines() or [""])[0]
    for candidate in (
        entry.title,
        entry.shortcut,
        (entry.tags or "").replace(TAG_SEPARATOR, "-"),
        entry.description,
        first_line,
    ):
        slug = slugify(candidate)
        if slug:
            return slug
    return _datetime_slug(entry.created_at)


def unique_path(entries_dir: Path, base: str, uid: str) -> Path:
    """Return a non-colliding ``entries_dir/<base>.md``. On collision, append a
    growing uid fragment (uid is unique), then a counter as a last resort."""
    base = base or "entry"
    candidate = entries_dir / f"{base}.md"
    if not candidate.exists():
        return candidate
    for n in (4, 8, 16):
        if uid:
            candidate = entries_dir / f"{base}-{uid[:n]}.md"
            if not candidate.exists():
                return candidate
    i = 2
    while True:
        candidate = entries_dir / f"{base}-{uid[:16] or 'x'}-{i}.md"
        if not candidate.exists():
            return candidate
        i += 1


def ensure_uid(entry: MdEntry, now: str) -> str:
    """Return the entry's uid, computing and assigning a stable one if missing
    (a hand-created file with no uid). Uses ``created_at`` when present so a
    round-tripped file keeps a deterministic uid."""
    if entry.uid:
        return entry.uid
    entry.uid = compute_uid(entry.content or "", entry.created_at or now)
    return entry.uid
