"""JSONL-backed Git sync for koda memos: payload I/O, git CLI, merge logic."""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from rich.console import Console

from .cli_utils import exit_error
from .config import Config, GIT_SYNC_FORMAT_JSONL
from .constants import DATETIME_FMT
from .db import IntegrityErrors, MemoDatabase


console = Console()


# ── Top-level helpers ────────────────────────────────────────────────────────

def require_git_cli() -> None:
    if shutil.which("git") is None:
        exit_error("git not found. Install Git and ensure it is on PATH.")


def require_jsonl_format(config: Config) -> None:
    fmt = (config.git_sync_format or "").strip().lower()
    if fmt != GIT_SYNC_FORMAT_JSONL:
        exit_error(
            f"git.sync_format must be {GIT_SYNC_FORMAT_JSONL!r} (JSON Lines). "
            f"Set git.sync_format or KODA_GIT_SYNC_FORMAT."
        )


def resolve_sync_root(config: Config) -> Path:
    raw = (config.git_sync_path or "").strip()
    if not raw:
        exit_error(
            "git.sync_path is empty. Set [git] sync_path in config or KODA_GIT_SYNC_PATH "
            "(path to your local clone of the sync repository)."
        )
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        exit_error(f"git.sync_path is not a directory: {root}")
    return root


def resolve_payload_path(config: Config, sync_root: Path) -> Path:
    rel = (config.git_payload_file or "").strip()
    if not rel:
        rel = "koda-sync.jsonl"
    rel_path = Path(rel)
    payload = (sync_root / rel_path).resolve()
    try:
        payload.relative_to(sync_root)
    except ValueError:
        exit_error("git.payload_file must stay inside git.sync_path.")
    return payload


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def parse_memo_datetime(s: Optional[str]) -> datetime:
    if not s or not str(s).strip():
        return datetime.min.replace(microsecond=0)
    try:
        return datetime.strptime(str(s).strip(), DATETIME_FMT)
    except ValueError:
        return datetime.min.replace(microsecond=0)


# ── JSONL payload (de)serialization ──────────────────────────────────────────

class GitSyncPayload:
    """Encode/decode the shared JSONL memo payload."""

    @staticmethod
    def parse_record(raw: object, lineno: int) -> dict:
        if not isinstance(raw, dict):
            raise ValueError(f"line {lineno}: each non-empty line must be a JSON object")
        uid = raw.get("uid")
        if not uid or not isinstance(uid, str):
            raise ValueError(f"line {lineno}: missing or invalid string field 'uid'")
        if "idx" not in raw:
            raise ValueError(f"line {lineno}: missing field 'idx'")
        try:
            idx = int(raw["idx"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"line {lineno}: 'idx' must be an integer") from e
        content = raw.get("content", "")
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        tags = raw.get("tags", "")
        if tags is None:
            tags = ""
        elif not isinstance(tags, str):
            tags = str(tags)
        created_at = raw.get("created_at", "")
        if created_at is None:
            created_at = ""
        else:
            created_at = str(created_at).strip()
        modified_at = raw.get("modified_at")
        if modified_at is None or (isinstance(modified_at, str) and not str(modified_at).strip()):
            modified_at = created_at
        else:
            modified_at = str(modified_at).strip()
        sc = raw.get("shortcut", None)
        if sc is not None and sc != "":
            sc = str(sc)
        else:
            sc = None
        return {
            "uid": uid,
            "idx": idx,
            "shortcut": sc,
            "content": content,
            "tags": tags,
            "created_at": created_at,
            "modified_at": modified_at,
        }

    @staticmethod
    def load(data: bytes) -> List[dict]:
        if not data.strip():
            return []
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ValueError(f"sync file is not valid UTF-8: {e}") from e
        by_uid: dict = {}
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as e:
                raise ValueError(f"line {lineno}: invalid JSON ({e})") from e
            rec = GitSyncPayload.parse_record(obj, lineno)
            by_uid[rec["uid"]] = rec
        return sorted(by_uid.values(), key=lambda m: m["uid"])

    @staticmethod
    def dump(db: MemoDatabase) -> bytes:
        """Export memos as UTF-8 JSON Lines, one object per line, sorted by uid."""
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT uid, idx, shortcut, content, tags, created_at, modified_at "
                "FROM memos ORDER BY uid ASC, id ASC"
            ).fetchall()
        memos: List[dict] = []
        for uid, idx, shortcut, content, tags, created_at, modified_at in rows:
            ca = created_at or ""
            ma = modified_at if modified_at else (ca or "")
            memos.append(
                {
                    "uid": uid,
                    "idx": idx,
                    "shortcut": shortcut,
                    "content": content if content is not None else "",
                    "tags": tags if tags is not None else "",
                    "created_at": ca,
                    "modified_at": ma,
                }
            )
        memos.sort(key=lambda m: m["uid"])
        lines = [
            json.dumps(m, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for m in memos
        ]
        body = "\n".join(lines)
        return (body + "\n").encode("utf-8") if body else b""


# ── Git CLI wrapper ──────────────────────────────────────────────────────────

class GitSyncRepo:
    """Thin wrapper around git operations in the sync clone."""

    def __init__(self, sync_root: Path) -> None:
        self.sync_root = sync_root

    def ensure_worktree(self) -> None:
        r = subprocess.run(
            ["git", "-C", str(self.sync_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0 or (r.stdout or "").strip() != "true":
            exit_error(f"Not a Git working tree: {self.sync_root}")

    def has_remote(self) -> bool:
        r = subprocess.run(
            ["git", "-C", str(self.sync_root), "remote"],
            capture_output=True,
            text=True,
        )
        return bool((r.stdout or "").strip())

    def preferred_remote(self) -> Optional[str]:
        r = subprocess.run(
            ["git", "-C", str(self.sync_root), "remote"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return None
        names = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if not names:
            return None
        if "origin" in names:
            return "origin"
        return names[0]

    def current_branch(self) -> Optional[str]:
        r = subprocess.run(
            ["git", "-C", str(self.sync_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return None
        b = (r.stdout or "").strip()
        if not b or b == "HEAD":
            return None
        return b

    def has_upstream(self) -> bool:
        r = subprocess.run(
            ["git", "-C", str(self.sync_root), "rev-parse", "--abbrev-ref", "@{u}"],
            capture_output=True,
            text=True,
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())

    def pull_rebase_if_remote(self) -> None:
        if not self.has_remote():
            return
        remote = self.preferred_remote()
        if not remote:
            exit_error("No Git remote resolved for pull in the sync clone.")
        branch = self.current_branch()
        if not branch:
            exit_error(
                "Cannot git pull in detached HEAD in the sync clone. Check out a branch, then retry."
            )
        if self.has_upstream():
            cmd: List[str] = ["git", "-C", str(self.sync_root), "pull", "--rebase"]
        else:
            cmd = ["git", "-C", str(self.sync_root), "pull", "--rebase", remote, branch]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            console.print(
                "[red]git pull --rebase failed in the sync clone. Resolve conflicts there, then retry.[/red]"
            )
            if e.stderr:
                console.print(f"[dim]{e.stderr.strip()}[/dim]")
            exit_error("git pull --rebase failed.")

    def push_if_remote(self) -> None:
        if not self.has_remote():
            console.print(
                "[yellow]No Git remotes configured; payload committed locally only (skipping push).[/yellow]"
            )
            return
        remote = self.preferred_remote()
        if not remote:
            exit_error("No Git remote resolved for push in the sync clone.")
        branch = self.current_branch()
        if not branch:
            exit_error(
                "Cannot git push in detached HEAD in the sync clone. Check out a branch, then retry."
            )
        if self.has_upstream():
            cmd: List[str] = ["git", "-C", str(self.sync_root), "push"]
        else:
            cmd = ["git", "-C", str(self.sync_root), "push", "-u", remote, branch]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            console.print(
                "[red]git push failed. Configure upstream/remotes or run push from that clone manually.[/red]"
            )
            if e.stderr:
                console.print(f"[dim]{e.stderr.strip()}[/dim]")
            exit_error("git push failed.")


# ── Merge into local DB ─────────────────────────────────────────────────────

class MemoMerger:
    """Merge remote JSONL entries into the local memos table by uid + modified_at."""

    def __init__(self, db: MemoDatabase) -> None:
        self.db = db

    @staticmethod
    def _shortcut_usable(conn, uid: str, shortcut: Optional[str]) -> Optional[str]:
        if shortcut is None or shortcut == "":
            return shortcut
        existing = conn.execute(
            "SELECT uid FROM memos WHERE shortcut = ?", (shortcut,)
        ).fetchone()
        if existing is None:
            return shortcut
        (existing_uid,) = existing
        if existing_uid == uid:
            return shortcut
        return None

    @staticmethod
    def _pick_idx(conn, preferred: int) -> int:
        if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (preferred,)).fetchone() is None:
            return preferred
        return MemoDatabase.next_idx(conn)

    @staticmethod
    def _apply_idx_for_row(conn, memo_id: int, preferred: int) -> None:
        occ = conn.execute(
            "SELECT id FROM memos WHERE idx = ? AND id != ?",
            (preferred, memo_id),
        ).fetchone()
        if occ is None:
            conn.execute("UPDATE memos SET idx = ? WHERE id = ?", (preferred, memo_id))
        else:
            conn.execute(
                "UPDATE memos SET idx = ? WHERE id = ?",
                (MemoDatabase.next_idx(conn), memo_id),
            )

    def merge(self, entries: List[dict]) -> Tuple[int, int, int, int]:
        """Return (inserted, updated, skipped, shortcut_dropped)."""
        inserted = updated = skipped = shortcut_dropped = 0
        with self.db.connection() as conn:
            for rm in sorted(
                entries,
                key=lambda m: (
                    parse_memo_datetime(m.get("modified_at"))
                    or parse_memo_datetime(m.get("created_at")),
                    str(m.get("uid") or ""),
                ),
            ):
                uid = rm.get("uid")
                if not uid or not isinstance(uid, str):
                    skipped += 1
                    continue
                try:
                    want_idx = int(rm["idx"])
                except (KeyError, TypeError, ValueError):
                    skipped += 1
                    continue
                content = rm.get("content") if rm.get("content") is not None else ""
                tags = rm.get("tags") if rm.get("tags") is not None else ""
                created_at = (
                    str(rm.get("created_at") or "").strip()
                    or datetime.now().strftime(DATETIME_FMT)
                )
                modified_at = str(rm.get("modified_at") or "").strip() or created_at
                raw_sc = rm.get("shortcut")
                sc = raw_sc if raw_sc is None or raw_sc == "" else str(raw_sc)

                r_ts = parse_memo_datetime(modified_at) or parse_memo_datetime(created_at)
                local_row = conn.execute(
                    "SELECT id, uid, idx, content, tags, shortcut, created_at, modified_at "
                    "FROM memos WHERE uid = ?",
                    (uid,),
                ).fetchone()
                if local_row is None:
                    pick_idx = self._pick_idx(conn, want_idx)
                    use_sc = self._shortcut_usable(conn, uid, sc)
                    if use_sc is None and sc:
                        shortcut_dropped += 1
                    try:
                        conn.execute(
                            "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (uid, pick_idx, use_sc, content, tags, created_at, modified_at),
                        )
                        inserted += 1
                    except IntegrityErrors:
                        pick_idx = MemoDatabase.next_idx(conn)
                        try:
                            conn.execute(
                                "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (uid, pick_idx, use_sc, content, tags, created_at, modified_at),
                            )
                            inserted += 1
                        except IntegrityErrors:
                            conn.execute(
                                "INSERT INTO memos (uid, idx, shortcut, content, tags, created_at, modified_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (uid, pick_idx, None, content, tags, created_at, modified_at),
                            )
                            inserted += 1
                            if use_sc:
                                shortcut_dropped += 1
                    continue

                memo_id = local_row[0]
                l_created = local_row[6]
                l_modified = local_row[7]
                l_ts = parse_memo_datetime(l_modified) or parse_memo_datetime(l_created)
                if r_ts <= l_ts:
                    skipped += 1
                    continue

                use_sc = self._shortcut_usable(conn, uid, sc)
                if use_sc is None and sc:
                    shortcut_dropped += 1
                self._apply_idx_for_row(conn, memo_id, want_idx)
                conn.execute(
                    "UPDATE memos SET shortcut = ?, content = ?, tags = ?, "
                    "created_at = ?, modified_at = ? WHERE id = ?",
                    (use_sc, content, tags, created_at, modified_at, memo_id),
                )
                updated += 1
        return inserted, updated, skipped, shortcut_dropped
