"""Execution and interactive-selection commands: exec, pick."""

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass

import typer
from rich.console import Console

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
from ..models import MemoRow
from ..runtime import (
    _apply_vars,
    _read_stdin_refs,
    _strip_inline_comment,
    emit_raw,
    get_config,
    get_db,
    init_db,
    resolve_ref,
)
from . import memo  # bound as a module to avoid a circular import at load time

# A reference line in a group body: `@<ref> [args...]`. <ref> is an idx (digits)
# or shortcut resolved via resolve_ref; the rest is shlex.split into per-child args.
_GROUP_REF_RE = re.compile(r"^@\S+")
# Guard against pathologically/maliciously deep nesting and indirect cycles even
# before the uid stack catches a true loop.
_MAX_GROUP_DEPTH = 10

# Matches a shell positional-parameter reference: $1..$9, $0, $@, $*, $#, and the
# braced forms ${1...}, ${@}, ${*}, ${#}. Deliberately does NOT match koda's own
# ${KEY} named placeholders (a letter after the brace), so a body that only uses
# ${KEY} still gets trailing args appended rather than swallowed.
_POSITIONAL_REF_RE = re.compile(r"\$(?:\d|[@*#]|\{\s*[\d@*#])")


def _references_positionals(body: str) -> bool:
    """True if the shell body already consumes positional params ($1, $@, ...).

    Decides whether trailing CLI args are appended as `"$@"` (body ignores them)
    or left for the body to pick up itself (body uses $1/$@/${1:-default}/...).
    """
    return bool(_POSITIONAL_REF_RE.search(body))


def _build_argv(shell: str, content: str, args: list[str]) -> list[str]:
    """Build the `[shell, -c, content, shell, *args]` argv for one body.

    Mirrors the single-entry construction: trailing args become the shell's real
    positional params; if the body doesn't reference them, `"$@"` is appended so
    they land at the end like extra words after an alias. With no args the tail is
    omitted, keeping the invocation byte-for-byte identical to the no-args case.
    """
    if args and not _references_positionals(content):
        content = f'{content} "$@"'
    return [shell, "-c", content, shell, *args] if args else [shell, "-c", content]


@dataclass(frozen=True)
class _GroupChild:
    """One resolved reference line in an expanded group plan."""

    row: MemoRow
    args: list[str]
    argv: list[str]


def _group_ref_lines(content: str) -> list[str] | None:
    """Classify a body after _apply_vars: list of `@`-reference lines or None.

    Strips inline comments and drops blank lines first. Returns the surviving
    reference lines if EVERY remaining line is a `@<ref>` line (a group); None if
    none are (a normal single-entry body). A mixed body (some but not all `@`
    lines) is an error.
    """
    lines = []
    for raw in content.splitlines():
        line = _strip_inline_comment(raw).strip()
        if line:
            lines.append(line)
    if not lines:
        return None
    ref_lines = [line for line in lines if _GROUP_REF_RE.match(line)]
    if not ref_lines:
        return None
    if len(ref_lines) != len(lines):
        exit_error("Mixed body: '@' reference lines cannot be combined with plain script lines.")
    return ref_lines


def _expand_group(
    ref_lines: list[str], shell: str, uid_stack: list[str], depth: int
) -> list[_GroupChild]:
    """Resolve a group's reference lines into a flat, ordered child plan.

    Resolves the FULL plan upfront so an unknown ref aborts before anything runs
    (fail fast). Nested groups expand recursively; the uid_stack detects cycles
    and _MAX_GROUP_DEPTH bounds runaway nesting.
    """
    if depth > _MAX_GROUP_DEPTH:
        exit_error(f"Group nesting too deep (max {_MAX_GROUP_DEPTH}).")
    plan: list[_GroupChild] = []
    for line in ref_lines:
        ref = line[1:]  # drop the leading '@'
        try:
            parts = shlex.split(ref)
        except ValueError:
            exit_error(f"Malformed reference line: {line!r}.")
        # shlex.split never yields an empty list here: _GROUP_REF_RE guaranteed
        # at least one non-space char after '@'.
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
    """First body line, truncated for confirmation/progress display."""
    first = (content or "").strip().splitlines()
    text = first[0] if first else ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _confirm_remote_children(involved: list[MemoRow], force: bool) -> None:
    """Prompt once for the source=remote entries in a group plan, or refuse.

    `involved` is the deduped group+children set. Mirrors the single-entry remote
    gate: non-TTY refuses with the `koda edit` review hint; a TTY prompts once
    listing each remote entry (children never re-prompt individually).
    """
    if not get_config().exec_confirm_remote or force:
        return
    remotes = [row for row in involved if row.source == "remote"]
    if not remotes:
        return
    listing = []
    for row in remotes[:10]:
        listing.append(f"[{row.idx}] {_first_line(row.content)}")
    if len(remotes) > 10:
        listing.append(f"... and {len(remotes) - 10} more")
    bullet = "\n".join(f"  {item}" for item in listing)
    if not sys.stdin.isatty():
        exit_error(
            "Refusing to exec group: it involves entries synced from a remote "
            "(source=remote) and not reviewed locally:\n"
            f"{bullet}\n"
            "Review them with `koda edit <ref>` to trust them (clears the flag), "
            "or re-run with -f to execute now.",
            style="yellow",
        )
    if not confirm(
        "This group involves entries synced from a remote and not reviewed locally:\n"
        f"{bullet}\n"
        "Review with `koda edit <ref>` to trust them. Execute the group now anyway?"
    ):
        exit_error("Aborted.", code=ExitCode.CANCELLED, style="yellow")


def _run_group(plan: list[_GroupChild], shell: str) -> None:
    """Run each child sequentially; stop and exit on the first non-zero code.

    Progress goes to STDERR so stdout stays clean for the children's own output.
    """
    err = Console(stderr=True)
    total = len(plan)
    for i, child in enumerate(plan, start=1):
        label = child.row.shortcut or str(child.row.idx)
        err.print(f"→ [{child.row.idx}] {label} ({i}/{total})", style="dim")
        result = subprocess.run(child.argv)
        if result.returncode != 0:
            err.print(
                f"Group stopped: [{child.row.idx}] {label} exited {result.returncode}.",
                style="red",
            )
            raise typer.Exit(code=result.returncode)


def _run_pick_action(action: str, ref: str) -> None:
    if action == "raw":
        emit_raw(ref)
        return
    if action == "show":
        init_db()
        row = resolve_ref(ref)
        _print_memo(
            row.uid,
            row.idx,
            row.shortcut,
            row.content,
            row.tags,
            row.created_at,
            title=row.title,
        )
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
        stdin_refs = _read_stdin_refs()
        if len(stdin_refs) > 1:
            exit_error("ex accepts one ref from stdin. Got multiple values.")
        if stdin_refs:
            ref = stdin_refs[0]

    init_db()
    row = resolve_ref(ref)
    content = _apply_vars(row.content.strip() if row.content else "", vars)
    shell = get_config().exec_shell

    # Group entry: a body whose lines are all `@<ref>` references. Detected after
    # _apply_vars so `-V` can parameterize the group body (e.g. `@${SVC}`). Only
    # the GROUP body goes through _apply_vars; children execute verbatim, so the
    # backslash-escaped `\$(...)` storage contract is preserved (no second pass).
    ref_lines = _group_ref_lines(content)
    if ref_lines is not None:
        if extra:
            exit_error("Group entries do not take trailing args; parameterize with -V instead.")
        plan = _expand_group(ref_lines, shell, [row.uid], depth=1)
        if dry_run:
            # Preview only: one shlex-quoted argv line per child, in execution
            # order. No progress lines, confirmation, or shell validation — keep
            # it pipeable, matching the single-entry preview rendering.
            for child in plan:
                sys.stdout.write(" ".join(shlex.quote(part) for part in child.argv) + "\n")
            return
        # Dedup the group + children by id so an entry referenced twice prompts
        # once. The group entry itself can be source=remote and must also gate.
        involved = list({r.id: r for r in [row, *(c.row for c in plan)]}.values())
        _confirm_remote_children(involved, force)
        try:
            ConfigManager.validate("exec.shell", shell)
        except ValidationError:
            exit_error(
                f"Refusing to exec: exec.shell {shell!r} is not allowed "
                f"({ConfigManager.error_message('exec.shell')})."
            )
        _run_group(plan, shell)
        return

    # Trailing CLI args become the shell's real positional parameters ($1, $@,
    # ...), so bodies can use `$1`, `"$@"`, and `${1:-default}` natively. If the
    # body doesn't reference them, append `"$@"` so they land at the end of the
    # command, like extra words typed after a shell alias. With no extra args the
    # invocation is byte-for-byte what it was before (full backward compatibility).
    positionals = list(extra or [])
    if positionals and not _references_positionals(content):
        content = f'{content} "$@"'
    # argv[3] becomes $0 for the spawned shell; positionals[*] become $1, $2, ...
    argv = [shell, "-c", content, shell, *positionals] if positionals else [shell, "-c", content]

    if dry_run:
        # Preview only: skip remote confirmation and shell validation since
        # nothing is executed. shlex.quote every part so the output is a
        # faithful, copy-pasteable rendering of the real argv — including the
        # `<shell> ... <args>` tail when trailing args are passed as positional
        # parameters. The shell still does the final `$@`/`${1:-...}` expansion,
        # so the preview shows the invocation, not the post-expansion string.
        sys.stdout.write(" ".join(shlex.quote(part) for part in argv) + "\n")
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
