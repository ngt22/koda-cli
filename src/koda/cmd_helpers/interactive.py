"""Interactive entry pickers (fzf integration and action resolution)."""

import shutil
import subprocess
import sys
from typing import List, Optional

from rich.console import Console

from ..cli_utils import exit_error
from ..config import Config, VALID_SORT_COLUMNS
from ..db import MemoDatabase
from ..models import MemoRow


console = Console()


def pick_candidates(
    db: MemoDatabase,
    config: Config,
    query: Optional[str],
    tag: Optional[str],
    exclude_tag: Optional[str],
    shortcuts_only: bool,
    sort_by: Optional[str],
    desc: Optional[bool],
) -> List[MemoRow]:
    effective_sort = (sort_by or config.list_sort_by).lower()
    if effective_sort not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        exit_error(f"Invalid --sort-by '{sort_by}'. Use one of: {valid}.")
    effective_desc = config.list_desc if desc is None else desc
    return db.get_memos_all(
        query=query,
        tag=tag,
        exclude_tag=exclude_tag,
        shortcuts_only=shortcuts_only,
        sort_by=effective_sort,
        desc=effective_desc,
    )


def pick_with_fzf(candidates: List[MemoRow]) -> Optional[str]:
    if shutil.which("fzf") is None:
        exit_error("fzf is not installed. Install fzf to use `koda pick`.")

    if not sys.stdin.isatty():
        exit_error("`koda pick` requires an interactive TTY.")

    lines: List[str] = []
    for row in candidates:
        first_line = (row.content or "").splitlines()[0] if row.content else ""
        display = (
            f"{row.idx}\t{row.uid}\t{row.shortcut or '-'}\t{row.tags or '-'}\t{row.created_at}\t{first_line}"
        )
        lines.append(display)

    term_cols = shutil.get_terminal_size(fallback=(120, 40)).columns
    # Keep list area readable on narrower terminals by switching to bottom preview.
    preview_window = "right:55%:wrap" if term_cols >= 170 else "down:55%:wrap"

    proc = subprocess.run(
        [
            "fzf",
            "--delimiter", "\t",
            "--with-nth", "1,3,4,6",
            "--prompt", "koda> ",
            "--preview",
            "printf 'IDX: %s\\nUID: %s\\nSC: %s\\nTags: %s\\nCreated: %s\\n\\n%s\\n' {1} {2} {3} {4} {5} {6}",
            "--preview-window", preview_window,
        ],
        input="\n".join(lines),
        text=True,
        stdout=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return None

    selected = proc.stdout.strip()
    if not selected:
        return None
    return selected.split("\t", 1)[0].strip()


def resolve_pick_action(
    config: Config,
    edit_mode: bool,
    exec_mode: bool,
    raw_mode: bool,
    show_mode: bool,
    print_id: bool,
) -> str:
    selected = [
        name
        for enabled, name in (
            (edit_mode, "edit"),
            (exec_mode, "exec"),
            (raw_mode, "raw"),
            (show_mode, "show"),
        )
        if enabled
    ]
    if len(selected) > 1:
        exit_error("Use only one of --edit/-e, --exec/-x, --raw/-r, or --show/-s.")
    if print_id and selected:
        exit_error("--print-id/-p cannot be combined with action flags.")
    if selected:
        return selected[0]
    default_cmd = config.defaults_cmd
    if default_cmd in ("raw", "show"):
        return default_cmd
    console.print("[dim]Hint: use --exec/-x, --edit/-e, --raw/-r, or --show/-s.[/dim]")
    exit_error("defaults.cmd must be 'raw' or 'show' for `koda pick` without action flags.")
