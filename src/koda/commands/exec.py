"""Execution and interactive-selection commands: exec, pick."""

import os
import shlex
import sys

import typer

from ..cli_utils import ExitCode, confirm, exit_error
from ..cmd_helpers.display import print_memo as _print_memo
from ..cmd_helpers.interactive import (
    pick_candidates,
    pick_with_fzf,
    pick_with_fzf_multi,
    resolve_pick_action,
)
from ..config import ConfigManager, ValidationError
from ..main import app
from ..runtime import (
    _apply_vars,
    _read_stdin_refs,
    emit_raw,
    get_config,
    get_db,
    init_db,
    resolve_ref,
)
from . import memo  # bound as a module to avoid a circular import at load time


def _run_pick_action(action: str, ref: str) -> None:
    if action == "raw":
        emit_raw(ref)
        return
    if action == "show":
        init_db()
        row = resolve_ref(ref)
        _print_memo(row.uid, row.idx, row.shortcut, row.content, row.tags, row.created_at)
        return
    if action == "edit":
        memo.edit(ref)
        return
    if action == "exec":
        exec_memo(ref, None)
        return
    exit_error(f"Unsupported pick action: {action}")


@app.command(name="exec")
def exec_memo(
    ref: str | None = typer.Argument(
        None, help="Entry index or shortcut to execute (default: latest)."
    ),
    vars: list[str] | None = typer.Option(
        None,
        "--var",
        "-V",
        help=(
            "Variable substitution. Named: KEY=VALUE → replaces ${KEY}. "
            "Positional: VALUE → replaces $1,$2,... in order. "
            'Comma-separate multiple values; use "..." to include spaces or commas. '
            "Examples: -V 'localhost,5432' -V 'name=prod' -V '\"hello world\",\"foo,bar\"'"
        ),
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
        stdin_refs = _read_stdin_refs()
        if len(stdin_refs) > 1:
            exit_error("ex accepts one ref from stdin. Got multiple values.")
        if stdin_refs:
            ref = stdin_refs[0]

    init_db()
    row = resolve_ref(ref)
    content = _apply_vars(row.content.strip() if row.content else "", vars)
    shell = get_config().exec_shell
    if dry_run:
        # Preview only: skip remote confirmation and shell validation since
        # nothing is executed. Quote both the shell and the body so the output
        # is a faithful, copy-pasteable rendering of the real `<shell> -c <body>`
        # invocation even when the shell name (validation skipped here) or the
        # body contains characters the shell would otherwise re-interpret.
        sys.stdout.write(f"{shlex.quote(shell)} -c {shlex.quote(content)}\n")
        return
    if row.source == "remote" and not force and get_config().exec_confirm_remote:
        label = ref if ref is not None else f"[{row.idx}]"
        review_hint = f"Review it with `koda edit {label}` to trust it (clears the flag)"
        if not sys.stdin.isatty():
            # No TTY to prompt on (cron, pipes, scripts). Steer toward review
            # rather than habitual -f, which would defeat the safety check.
            exit_error(
                f"Refusing to exec {label}: synced from a remote (source=remote) and not "
                f"reviewed locally. {review_hint}, or re-run with -f to execute now.",
                style="yellow",
            )
        if not confirm(
            f"Entry {label} was synced from a remote and not reviewed locally. "
            f"{review_hint}. Execute now anyway?"
        ):
            exit_error("Aborted.", code=ExitCode.CANCELLED, style="yellow")
    try:
        ConfigManager.validate("exec.shell", shell)
    except ValidationError:
        exit_error(
            f"Refusing to exec: exec.shell {shell!r} is not allowed "
            f"({ConfigManager.error_message('exec.shell')})."
        )
    os.execvp(shell, [shell, "-c", content])


@app.command()
def pick(
    query: str | None = typer.Option(None, "--query", "-q", help="Substring match on memo body."),
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
        help=(
            "Sort column: id, idx, uid, tags, content, created_at, modified_at, shortcut. "
            "[config: list.sort_by]"
        ),
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
    candidates = pick_candidates(
        get_db(), get_config(), query, tag, exclude_tag, shortcuts_only, sort_by, desc
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
