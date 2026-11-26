"""fzf integration and pick-action resolution."""

import os
import shlex
import shutil
import subprocess
import sys

from ..cli_utils import console, exit_error
from ..config import Config
from ..models import MemoRow


def run_fzf(lines: list[str], args: list[str]) -> str | None:
    """Run fzf (plus ``KODA_FZF_OPTS``) over ``lines``; return stdout, or None if cancelled."""
    cmd = ["fzf", *args]
    extra = os.environ.get("KODA_FZF_OPTS", "").strip()
    if extra:
        cmd.extend(shlex.split(extra))
    proc = subprocess.run(cmd, input="\n".join(lines), text=True, stdout=subprocess.PIPE)
    return proc.stdout if proc.returncode == 0 else None


def _fzf_line(row: MemoRow) -> str:
    """Tab-separated: idx, uid, sc, tags, created_at, label, first_line."""
    first_line = (row.content or "").splitlines()[0] if row.content else ""
    first_line = first_line.replace("\t", " ")
    label = (row.title or first_line).replace("\t", " ")
    return (
        f"{row.idx}\t{row.uid}\t{row.shortcut or '-'}\t"
        f"{row.tags or '-'}\t{row.created_at}\t{label}\t{first_line}"
    )


def _run_fzf(candidates: list[MemoRow], multi: bool) -> list[str]:
    """Selected idx strings."""
    if shutil.which("fzf") is None:
        exit_error("fzf is not installed. Install fzf to use `koda pick`.")

    if not sys.stdin.isatty():
        exit_error("`koda pick` requires an interactive TTY.")

    term_cols = shutil.get_terminal_size(fallback=(120, 40)).columns
    preview_window = "right:55%:wrap" if term_cols >= 170 else "down:55%:wrap"

    args = [
        "--delimiter",
        "\t",
        "--with-nth",
        "1,3,4,6",
        "--prompt",
        "koda> ",
        "--preview",
        (
            "printf 'IDX: %s\\nUID: %s\\nSC: %s\\nTags: %s\\nCreated: %s\\nTitle: %s\\n\\n%s\\n' "
            "{1} {2} {3} {4} {5} {6} {7}"
        ),
        "--preview-window",
        preview_window,
    ]
    if multi:
        args.append("--multi")
    out = run_fzf([_fzf_line(row) for row in candidates], args)
    if out is None:
        return []
    return [line.split("\t", 1)[0].strip() for line in out.splitlines() if line.strip()]


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
