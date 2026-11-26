"""Execution and interactive-selection commands: exec, pick."""

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

import typer

from ..cli_utils import ExitCode, confirm, exit_error, stderr_console
from ..cmd_helpers.display import print_memo
from ..cmd_helpers.interactive import (
    pick_with_fzf,
    pick_with_fzf_multi,
    resolve_pick_action,
)
from ..config import ConfigManager, ValidationError
from ..main import SORT_HELP, VAR_HELP, app
from ..models import MemoRow
from ..runtime import (
    _apply_vars,
    _strip_inline_comment,
    emit_raw,
    get_config,
    get_db,
    init_db,
    resolve_ref,
    resolve_sort,
    single_stdin_ref,
)
from . import memo

# `@<ref> [args...]` line in a group body.
_GROUP_REF_RE = re.compile(r"^@\S+")
_MAX_GROUP_DEPTH = 10

# $1, $@, ${1:-x} ... but not koda's own ${KEY} placeholders.
_POSITIONAL_REF_RE = re.compile(r"\$(?:\d|[@*#]|\{\s*[\d@*#])")


def _references_positionals(body: str) -> bool:
    return bool(_POSITIONAL_REF_RE.search(body))


def _build_argv(shell: str, content: str, args: list[str]) -> list[str]:
    """Trailing args become positional params; appended as ``"$@"`` if the body ignores them."""
    if args and not _references_positionals(content):
        content = f'{content} "$@"'
    return [shell, "-c", content, shell, *args] if args else [shell, "-c", content]


@dataclass(frozen=True)
class _GroupChild:
    row: MemoRow
    args: list[str]
    argv: list[str]


def _group_ref_lines(content: str) -> list[str] | None:
    """The ``@ref`` lines if every non-blank line is one (a group), None if none are."""
    lines = [
        ln for ln in (_strip_inline_comment(raw).strip() for raw in content.splitlines()) if ln
    ]
    ref_lines = [line for line in lines if _GROUP_REF_RE.match(line)]
    if not ref_lines:
        return None
    if len(ref_lines) != len(lines):
        exit_error("Mixed body: '@' reference lines cannot be combined with plain script lines.")
    return ref_lines


def _expand_group(
    ref_lines: list[str], shell: str, uid_stack: list[str], depth: int
) -> list[_GroupChild]:
    """Flatten a group into its run plan up front, so a bad ref fails before anything runs."""
    if depth > _MAX_GROUP_DEPTH:
        exit_error(f"Group nesting too deep (max {_MAX_GROUP_DEPTH}).")
    plan: list[_GroupChild] = []
    for line in ref_lines:
        try:
            parts = shlex.split(line[1:])
        except ValueError:
            exit_error(f"Malformed reference line: {line!r}.")
        child_ref, child_args = parts[0], parts[1:]
        row = resolve_ref(child_ref)
        child_content = row.content.strip() if row.content else ""
        nested = _group_ref_lines(child_content)
        if nested is not None:
            if row.uid in uid_stack:
                cycle = " -> ".join([*uid_stack, row.uid])
                exit_error(f"Group cycle detected: {cycle}.")
            plan.extend(_expand_group(nested, shell, [*uid_stack, row.uid], depth + 1))
        else:
            argv = _build_argv(shell, child_content, child_args)
            plan.append(_GroupChild(row=row, args=child_args, argv=argv))
    return plan


def _first_line(content: str | None, limit: int = 60) -> str:
    first = (content or "").strip().splitlines()
    text = first[0] if first else ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _confirm_remote(rows: list[MemoRow], force: bool, what: str, hint_ref: str) -> None:
    """Gate unreviewed (source=remote) entries: refuse on a non-TTY, else prompt once."""
    if force or not get_config().exec_confirm_remote:
        return
    remotes = [row for row in rows if row.source == "remote"]
    if not remotes:
        return
    listing = [f"  [{row.idx}] {_first_line(row.content)}" for row in remotes[:10]]
    if len(remotes) > 10:
        listing.append(f"  ... and {len(remotes) - 10} more")
    detail = (
        f"{what} synced from a remote (source=remote) and not reviewed locally:\n"
        + "\n".join(listing)
        + f"\nReview with `koda edit {hint_ref}` to trust it (clears the flag)"
    )
    if not sys.stdin.isatty():
        exit_error(f"Refusing to exec: {detail}, or re-run with -f to execute now.", style="yellow")
    if not confirm(f"{detail}. Execute now anyway?"):
        exit_error("Aborted.", code=ExitCode.CANCELLED, style="yellow")


def _validate_shell(shell: str) -> None:
    try:
        ConfigManager.validate("exec.shell", shell)
    except ValidationError:
        exit_error(
            f"Refusing to exec: exec.shell {shell!r} is not allowed "
            f"({ConfigManager.error_message('exec.shell')})."
        )


def _print_argv(argv: list[str]) -> None:
    sys.stdout.write(" ".join(shlex.quote(part) for part in argv) + "\n")


def _run_group(plan: list[_GroupChild], shell: str) -> None:
    """Run children in order; stop on the first non-zero exit. Progress goes to stderr."""
    total = len(plan)
    for i, child in enumerate(plan, start=1):
        label = child.row.shortcut or str(child.row.idx)
        stderr_console.print(f"→ [{child.row.idx}] {label} ({i}/{total})", style="dim")
        result = subprocess.run(child.argv)
        if result.returncode != 0:
            stderr_console.print(
                f"Group stopped: [{child.row.idx}] {label} exited {result.returncode}.",
                style="red",
            )
            raise typer.Exit(code=result.returncode)


def _run_pick_action(action: str, ref: str) -> None:
    if action == "raw":
        emit_raw(ref)
        return
    if action == "show":
        print_memo(resolve_ref(ref))
        return
    if action == "edit":
        memo.edit(ref)
        return
    if action == "exec":
        exec_memo(ref, None)
        return
    exit_error(f"Unsupported pick action: {action}")


@app.command(
    name="exec",
    rich_help_panel="Core",
    context_settings={"ignore_unknown_options": True, "help_option_names": ["-h", "--help"]},
)
def exec_memo(
    ref: str | None = typer.Argument(
        None, help="Entry index or shortcut to execute (default: latest)."
    ),
    extra: list[str] | None = typer.Argument(
        None,
        help=(
            "Extra args passed to the command: appended at the end (like a shell "
            'alias) or used to fill $1, $2, "$@" if the body references them. '
            "Put -- before args that look like options."
        ),
    ),
    vars: list[str] | None = typer.Option(
        None,
        "--var",
        "-V",
        help=VAR_HELP,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip the confirmation prompt for entries synced from a remote (source=remote).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Print the resolved command (after variable substitution) without running it.",
    ),
):
    """Execute the memo body as a shell command. Alias: `koda x`.

    When no argument is given, this command also accepts one ref from stdin.

    Extra arguments after the ref are passed to the command. If the body does not
    use them they are appended at the end (e.g. `koda x dcu llama-server` runs
    `docker compose up -d llama-server`); if the body references `$1`/`"$@"` they
    fill those, and `${1:-default}` supplies a fallback when none are passed. Put
    `--` before args that look like options (`koda x dcu -- --build`).

    Entries brought in by `koda pull` are marked source=remote and prompt for
    confirmation before running, since a compromised sync remote could rewrite
    their body. Review with `koda edit <ref>` to trust an entry permanently
    (this clears the flag); -f runs it once without prompting. Set
    exec.confirm_remote=false to disable the prompt entirely (not recommended).

    Use --dry-run/-n to preview the exact command that would run (variables
    already substituted) without executing it, prompting, or validating the
    shell — useful for checking an unreviewed remote entry before you trust it.
    The body is printed verbatim, including any terminal escape sequences it may
    contain, so to inspect a fully untrusted entry redirect the output to a file
    (`koda x <ref> -n > preview.txt`) rather than rendering it in the terminal.
    """
    if ref is None:
        ref = single_stdin_ref("exec")

    init_db()
    row = resolve_ref(ref)
    content = _apply_vars(row.content.strip() if row.content else "", vars)
    shell = get_config().exec_shell

    # Only the group body gets -V substitution; children run verbatim.
    ref_lines = _group_ref_lines(content)
    if ref_lines is not None:
        if extra:
            exit_error("Group entries do not take trailing args; parameterize with -V instead.")
        plan = _expand_group(ref_lines, shell, [row.uid], depth=1)
        if dry_run:
            for child in plan:
                _print_argv(child.argv)
            return
        involved = list({r.uid: r for r in [row, *(c.row for c in plan)]}.values())
        _confirm_remote(involved, force, "This group involves entries", "<ref>")
        _validate_shell(shell)
        _run_group(plan, shell)
        return

    argv = _build_argv(shell, content, list(extra or []))
    if dry_run:
        _print_argv(argv)
        return
    _confirm_remote([row], force, "This entry was", ref if ref is not None else str(row.idx))
    _validate_shell(shell)
    os.execvp(shell, argv)


@app.command(rich_help_panel="Core")
def pick(
    query: str | None = typer.Option(
        None, "--query", "-q", help="Substring match on memo body or title."
    ),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Substring match on tags."),
    exclude_tag: str | None = typer.Option(
        None, "--exclude-tag", "-T", help="Exclude entries whose tags include this substring."
    ),
    shortcuts_only: bool = typer.Option(
        False, "--shortcuts", "-S", help="Show only entries that have a shortcut."
    ),
    sort_by: str | None = typer.Option(
        None,
        "--sort-by",
        case_sensitive=False,
        help=SORT_HELP,
    ),
    desc: bool | None = typer.Option(
        None,
        "--desc/--asc",
        help="Sort order. [config: list.desc]",
    ),
    print_id: bool = typer.Option(
        False, "--print-id", "-p", help="Print selected IDX and exit without running a command."
    ),
    edit_mode: bool = typer.Option(False, "--edit", "-e", help="Open selected entry in editor."),
    exec_mode: bool = typer.Option(False, "--exec", "-x", help="Execute selected entry."),
    raw_mode: bool = typer.Option(False, "--raw", "-r", help="Print selected entry body."),
    show_mode: bool = typer.Option(
        False, "--show", "-s", help="Show selected entry with metadata."
    ),
    multi: bool = typer.Option(
        False,
        "--multi",
        "-m",
        help="Multi-select. Prints selected IDXs (pipe to remove/tag), or applies --raw/--show.",
    ),
):
    """Pick an entry with fzf, then run an action (or print IDX). Alias: `koda p`.

    With --multi, select several entries: by default their IDXs are printed
    (one per line) for piping, e.g. `koda pick -m | xargs koda remove -f`.
    --raw/--show apply the action to each selection; --edit/--exec are
    single-entry only. Extra fzf flags can be set via KODA_FZF_OPTS.
    """
    if print_id and (edit_mode or exec_mode or raw_mode or show_mode):
        exit_error("--print-id/-p cannot be combined with action flags.")

    init_db()
    sort_col, desc = resolve_sort(sort_by, desc)
    candidates = get_db().get_memos(
        query=query,
        tag=tag,
        exclude_tag=exclude_tag,
        shortcuts_only=shortcuts_only,
        sort_by=sort_col,
        desc=desc,
    )
    if not candidates:
        exit_error("No entries found.", style="yellow")

    if multi:
        if edit_mode or exec_mode:
            exit_error(
                "--multi cannot be combined with --edit/--exec; use --raw/--show, "
                "or pipe --print-id output to `koda remove`/`koda tag`."
            )
        refs = pick_with_fzf_multi(candidates)
        if not refs:
            raise typer.Exit(code=0)
        if not (raw_mode or show_mode):
            for ref in refs:
                sys.stdout.write(ref + "\n")
            return
        pick_action = "raw" if raw_mode else "show"
        for ref in refs:
            _run_pick_action(pick_action, ref)
        return

    if print_id:
        selected_ref = pick_with_fzf(candidates)
        if selected_ref is None:
            raise typer.Exit(code=0)
        sys.stdout.write(selected_ref + "\n")
        return

    action = resolve_pick_action(get_config(), edit_mode, exec_mode, raw_mode, show_mode, print_id)
    selected_ref = pick_with_fzf(candidates)
    if selected_ref is None:
        raise typer.Exit(code=0)
    _run_pick_action(action, selected_ref)
