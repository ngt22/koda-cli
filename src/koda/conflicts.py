"""Local-only ledger for unresolved idx conflicts.

A conflict is two (or more) distinct entries sharing the same display ``idx``.
They are detected during ``koda pull``/``koda push`` and recorded here so
``koda resolve`` can present an "ours vs theirs" choice. The file lives under
``<vault>/.koda/`` (gitignored, mode 700) — like the cache and the trust ledger,
it is never synced.

Schema::

    {
      "groups": [
        {
          "idx": 32,
          "entries": [
            {
              "uid": "…", "path": "z.md", "side": "ours",
              "shortcut": "z", "tags": "cmd", "title": "Z",
              "content": "…", "created_at": "…", "prev_idx": 32
            },
            …
          ]
        }
      ]
    }
"""

import json
import os
from pathlib import Path


def conflicts_path(vault: Path) -> Path:
    return vault / ".koda" / "conflicts.json"


def load_conflicts(vault: Path) -> list[dict]:
    """Return the recorded conflict groups (``[]`` when none or unreadable)."""
    p = conflicts_path(vault)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    groups = data.get("groups", [])
    return groups if isinstance(groups, list) else []


def save_conflicts(vault: Path, groups: list[dict]) -> None:
    p = conflicts_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"groups": groups}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(p, 0o600)


def clear_conflicts(vault: Path) -> None:
    conflicts_path(vault).unlink(missing_ok=True)
