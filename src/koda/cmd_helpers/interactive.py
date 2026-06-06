"""Interactive entry pickers (fzf integration and action resolution)."""

import os
import shlex
import shutil
import subprocess
import sys

from rich.console import Console

from ..cli_utils import exit_error
from ..config import VALID_SORT_COLUMNS, Config
from ..db import MemoDatabase
from ..models import MemoRow

console = Console()


def pick_candidates(
    db: MemoDatabase,
    config: Config,
    query: str | None,
    tag: str | None,
    exclude_tag: str | None,
    shortcuts_only: bool,
    sort_by: str | None,
    desc: bool | None,
) -> list[MemoRow]:
    effective_sort = (sort_by or config.list_sort_by).lower()
    if effective_sort not in VALID_SORT_COLUMNS:
        valid = ", ".join(sorted(VALID_SORT_COLUMNS))
        exit_error(f"Invalid --sort-by '{sort_by}'. Use one of: {valid}.")
    effective_desc = config.list_desc if desc is None else desc
    return db.get_memos(
        query=query,
        tag=tag,
        exclude_tag=exclude_tag,
        shortcuts_only=shortcuts_only,
        sort_by=effective_sort,
        desc=effective_desc,
    )


def _run_fzf(candidates: list[MemoRow], multi: bool) -> list[str]:
    """Run fzf over the candidates and return the selected entry refs (idx strings).

    Honors the ``KODA_FZF_OPTS`` environment variable for extra fzf arguments.
    """
    if shutil.which("fzf") is None:
        exit_error("fzf is not installed. Install fzf to use `koda pick`.")

    if not sys.stdin.isatty():
        exit_error("`koda pick` requires an interactive TTY.")

    lines: list[str] = []
    for row in candidates:
        first_line = (row.content or "").splitlines()[0] if row.content else ""
        display = (
            f"{row.idx}\t{row.uid}\t{row.shortcut or '-'}\t"
            f"{row.tags or '-'}\t{row.created_at}\t{first_line}"
        )
        lines.append(display)

    term_cols = shutil.get_terminal_size(fallback=(120, 40)).columns
    # Keep list area readable on narrower terminals by switching to bottom preview.
    preview_window = "right:55%:wrap" if term_cols >= 170 else "down:55%:wrap"

    cmd = [
        "fzf",
        "--delimiter",
        "\t",
        "--with-nth",
        "1,3,4,6",
        "--prompt",
        "koda> ",
        "--preview",
        (
            "printf 'IDX: %s\\nUID: %s\\nSC: %s\\nTags: %s\\nCreated: %s\\n\\n%s\\n' "
            "{1} {2} {3} {4} {5} {6}"
        ),
        "--preview-window",
        preview_window,
    ]
    if multi:
        cmd.append("--multi")
    extra = os.environ.get("KODA_FZF_OPTS", "").strip()
    if extra:
        cmd.extend(shlex.split(extra))

    proc = subprocess.run(
        cmd,
        input="\n".join(lines),
        text=True,
        stdout=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return []

    refs = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            refs.append(line.split("\t", 1)[0].strip())
    return refs


def pick_with_fzf(candidates: list[MemoRow]) -> str | None:
    refs = _run_fzf(candidates, multi=False)
    return refs[0] if refs else None


def pick_with_fzf_multi(candidates: list[MemoRow]) -> list[str]:
    return _run_fzf(candidates, multi=True)


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
