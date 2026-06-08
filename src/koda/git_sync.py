"""JSONL-backed Git sync for koda memos: payload I/O, git CLI, merge logic."""

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console

from .cli_utils import exit_error
from .config import GIT_SYNC_FORMAT_JSONL, Config
from .constants import DATETIME_FMT
from .db import UID_LENGTH, MemoDatabase

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
    if rel_path.is_absolute() or ".." in rel_path.parts or ".git" in rel_path.parts:
        # The config validator already rejects these, but values loaded from a
        # hand-edited config.toml or KODA_GIT_PAYLOAD_FILE bypass validate(),
        # so re-check here before we ever write into the repo. Blocking '.git'
        # stops the payload from overwriting e.g. .git/hooks/post-merge.
        exit_error(
            "git.payload_file must be a relative path inside git.sync_path "
            "without '..' or '.git' components."
        )
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


def parse_memo_datetime(s: str | None) -> datetime:
    if not s or not str(s).strip():
        return datetime.min.replace(microsecond=0)
    try:
        return datetime.strptime(str(s).strip(), DATETIME_FMT)
    except ValueError:
        return datetime.min.replace(microsecond=0)


# A remote modified_at this far beyond "now" is treated as untrusted. The merge
# resolves conflicts last-writer-wins on modified_at, so a tampered payload can
# post-date an entry far into the future to always win and silently overwrite a
# local entry. Such entries are never allowed to overwrite local data (genuine
# clock skew between machines stays well within this window).
FUTURE_SKEW_ALLOWANCE = timedelta(hours=24)


def is_future_dated(ts: datetime) -> bool:
    """True if ``ts`` is implausibly far in the future (untrusted timestamp)."""
    return ts > datetime.now() + FUTURE_SKEW_ALLOWANCE


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
    def load(data: bytes) -> list[dict]:
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
        memos: list[dict] = []
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

    def preferred_remote(self) -> str | None:
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

    def current_branch(self) -> str | None:
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
                "Cannot git pull in detached HEAD in the sync clone. "
                "Check out a branch, then retry."
            )
        if self.has_upstream():
            cmd: list[str] = ["git", "-C", str(self.sync_root), "pull", "--rebase"]
        else:
            cmd = ["git", "-C", str(self.sync_root), "pull", "--rebase", remote, branch]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            console.print(
                "[red]git pull --rebase failed in the sync clone. "
                "Resolve conflicts there, then retry.[/red]"
            )
            if e.stderr:
                console.print(f"[dim]{e.stderr.strip()}[/dim]")
            exit_error("git pull --rebase failed.")

    def push_if_remote(self) -> None:
        if not self.has_remote():
            console.print(
                "[yellow]No Git remotes configured; "
                "payload committed locally only (skipping push).[/yellow]"
            )
            return
        remote = self.preferred_remote()
        if not remote:
            exit_error("No Git remote resolved for push in the sync clone.")
        branch = self.current_branch()
        if not branch:
            exit_error(
                "Cannot git push in detached HEAD in the sync clone. "
                "Check out a branch, then retry."
            )
        if self.has_upstream():
            cmd: list[str] = ["git", "-C", str(self.sync_root), "push"]
        else:
            cmd = ["git", "-C", str(self.sync_root), "push", "-u", remote, branch]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            console.print(
                "[red]git push failed. "
                "Configure upstream/remotes or run push from that clone manually.[/red]"
            )
            if e.stderr:
                console.print(f"[dim]{e.stderr.strip()}[/dim]")
            exit_error("git push failed.")


# ── Merge into local DB ─────────────────────────────────────────────────────


def pick_idx(conn, preferred: int) -> int:
    """Return an idx free for a brand-new row: ``preferred`` if unoccupied,
    otherwise the next free idx. The memos.idx UNIQUE constraint is therefore
    satisfied without needing an INSERT retry."""
    if conn.execute("SELECT 1 FROM memos WHERE idx = ?", (preferred,)).fetchone() is None:
        return preferred
    return MemoDatabase.next_idx(conn)


def pick_shortcut(conn, uid: str, shortcut: str | None) -> str | None:
    """Return ``shortcut`` if it is usable for ``uid`` (empty/None, unclaimed,
    or already owned by this uid), else None. Keeps the partial-unique shortcut
    index satisfied without needing an INSERT retry."""
    if shortcut is None or shortcut == "":
        return shortcut
    existing = conn.execute("SELECT uid FROM memos WHERE shortcut = ?", (shortcut,)).fetchone()
    if existing is None:
        return shortcut
    (existing_uid,) = existing
    if existing_uid == uid:
        return shortcut
    return None


class MemoMerger:
    """Merge remote JSONL entries into the local memos table by uid + modified_at."""

    def __init__(self, db: MemoDatabase) -> None:
        self.db = db

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

    @staticmethod
    def _remote_overwrites_local(r_ts: datetime, l_ts: datetime) -> bool:
        """Whether a remote entry should overwrite the local one it conflicts with.

        Last-writer-wins on modified_at, except an implausibly future-dated
        remote timestamp is rejected so a tampered payload cannot post-date an
        entry to force an overwrite.
        """
        if r_ts <= l_ts:
            return False
        return not is_future_dated(r_ts)

    @staticmethod
    def _sort_key(m: dict):
        return (
            parse_memo_datetime(m.get("modified_at")) or parse_memo_datetime(m.get("created_at")),
            str(m.get("uid") or ""),
        )

    @staticmethod
    def _normalize(rm: dict) -> dict | None:
        """Coerce a raw remote record into the fields merge/plan need, or return
        None when uid/idx are missing or unparseable (the entry is skipped)."""
        uid = rm.get("uid")
        if not uid or not isinstance(uid, str):
            return None
        try:
            want_idx = int(rm["idx"])
        except (KeyError, TypeError, ValueError):
            return None
        content = rm.get("content") if rm.get("content") is not None else ""
        tags = rm.get("tags") if rm.get("tags") is not None else ""
        created_at = str(rm.get("created_at") or "").strip() or datetime.now().strftime(
            DATETIME_FMT
        )
        modified_at = str(rm.get("modified_at") or "").strip() or created_at
        raw_sc = rm.get("shortcut")
        sc = raw_sc if raw_sc is None or raw_sc == "" else str(raw_sc)
        return {
            "uid": uid,
            "want_idx": want_idx,
            "content": content,
            "tags": tags,
            "created_at": created_at,
            "modified_at": modified_at,
            "sc": sc,
            "r_ts": parse_memo_datetime(modified_at) or parse_memo_datetime(created_at),
        }

    @staticmethod
    def _find_local(conn, uid: str):
        """Locate the local row for a remote uid: exact match, or—for a legacy
        short uid—an unambiguous uid-prefix match against widened local uids."""
        local_row = conn.execute(
            "SELECT id, uid, idx, content, tags, shortcut, created_at, modified_at "
            "FROM memos WHERE uid = ?",
            (uid,),
        ).fetchone()
        if local_row is None and len(uid) < UID_LENGTH:
            # Legacy payload: a pre-widening peer still emits 7-char uids. Match
            # them to the widened local uid by prefix so the entry updates in
            # place instead of duplicating. Only an unambiguous match is used.
            candidates = conn.execute(
                "SELECT id, uid, idx, content, tags, shortcut, created_at, modified_at "
                "FROM memos WHERE uid LIKE ? ESCAPE '\\' LIMIT 2",
                (MemoDatabase._uid_prefix_like(uid),),
            ).fetchall()
            if len(candidates) == 1:
                local_row = candidates[0]
        return local_row

    def merge(self, entries: list[dict]) -> tuple[int, int, int, int]:
        """Return (inserted, updated, skipped, shortcut_dropped). Inserted and
        updated rows are marked source='remote' — untrusted until reviewed
        locally, so `koda x` prompts before executing them."""
        inserted = updated = skipped = shortcut_dropped = 0
        with self.db.connection() as conn:
            for rm in sorted(entries, key=self._sort_key):
                rec = self._normalize(rm)
                if rec is None:
                    skipped += 1
                    continue
                uid = rec["uid"]
                local_row = self._find_local(conn, uid)
                if local_row is None:
                    # uid is absent, idx and shortcut are pre-resolved to free
                    # values, so this INSERT cannot violate any UNIQUE
                    # constraint — no retry needed.
                    new_idx = pick_idx(conn, rec["want_idx"])
                    use_sc = pick_shortcut(conn, uid, rec["sc"])
                    if use_sc is None and rec["sc"]:
                        shortcut_dropped += 1
                    conn.execute(
                        "INSERT INTO memos "
                        "(uid, idx, shortcut, content, tags, created_at, modified_at, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, 'remote')",
                        (
                            uid,
                            new_idx,
                            use_sc,
                            rec["content"],
                            rec["tags"],
                            rec["created_at"],
                            rec["modified_at"],
                        ),
                    )
                    inserted += 1
                    continue

                memo_id = local_row[0]
                l_ts = parse_memo_datetime(local_row[7]) or parse_memo_datetime(local_row[6])
                if not self._remote_overwrites_local(rec["r_ts"], l_ts):
                    skipped += 1
                    continue

                use_sc = pick_shortcut(conn, uid, rec["sc"])
                if use_sc is None and rec["sc"]:
                    shortcut_dropped += 1
                self._apply_idx_for_row(conn, memo_id, rec["want_idx"])
                conn.execute(
                    "UPDATE memos SET shortcut = ?, content = ?, tags = ?, "
                    "created_at = ?, modified_at = ?, source = 'remote' WHERE id = ?",
                    (
                        use_sc,
                        rec["content"],
                        rec["tags"],
                        rec["created_at"],
                        rec["modified_at"],
                        memo_id,
                    ),
                )
                updated += 1
        return inserted, updated, skipped, shortcut_dropped

    def plan(self, entries: list[dict]) -> list[dict]:
        """Read-only classification of a payload for `pull --dry-run`: a list of
        {action, uid, idx, content} where action is insert/update/skip, without
        mutating the database. Mirrors merge's insert/update/skip rule; idx
        shown is the remote's preferred value (conflict resolution is applied
        only by the real merge)."""
        out: list[dict] = []
        with self.db.connection() as conn:
            for rm in sorted(entries, key=self._sort_key):
                rec = self._normalize(rm)
                if rec is None:
                    out.append(
                        {
                            "action": "skip",
                            "uid": str(rm.get("uid") or "?"),
                            "idx": None,
                            "content": "",
                        }
                    )
                    continue
                local_row = self._find_local(conn, rec["uid"])
                if local_row is None:
                    action = "insert"
                else:
                    l_ts = parse_memo_datetime(local_row[7]) or parse_memo_datetime(local_row[6])
                    action = (
                        "update" if self._remote_overwrites_local(rec["r_ts"], l_ts) else "skip"
                    )
                out.append(
                    {
                        "action": action,
                        "uid": rec["uid"],
                        "idx": rec["want_idx"],
                        "content": rec["content"] or "",
                    }
                )
        return out
