"""Git sync (push, pull, diff): plain git on the vault's ``entries/*.md``."""

import shutil
import subprocess
from pathlib import Path

import typer
from rich.text import Text

from ..cli_utils import ExitCode, exit_error
from ..cmd_helpers.display import format_conflict_entry
from ..conflicts import load_conflicts, save_conflicts
from ..main import app
from ..reconcile import (
    annotate_groups,
    compute_sync_diff,
    find_conflicts,
    reconcile_all,
    snapshot_idx,
)
from ..runtime import console, get_db, get_entries_dir, get_vault, init_db

# Local cache/trust ledger and per-machine Obsidian state never sync.
_GITIGNORE_LINES = (".koda/", ".obsidian/")


def _require_git() -> None:
    if shutil.which("git") is None:
        exit_error("git is not installed or not on PATH.")


def _git(vault: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(vault), *args], check=check, capture_output=True, text=True
    )


def _is_repo(vault: Path) -> bool:
    return (vault / ".git").exists()


def _ensure_repo(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    if not _is_repo(vault):
        _git(vault, "init")
    gitignore = vault / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    missing = [line for line in _GITIGNORE_LINES if line not in lines]
    if missing:
        gitignore.write_text("\n".join(lines + missing) + "\n", encoding="utf-8")


def _has_remote(vault: Path) -> bool:
    return bool(_git(vault, "remote", check=False).stdout.strip())


def _current_branch(vault: Path) -> str:
    out = _git(vault, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip()
    return out or "main"


def _first_remote(vault: Path) -> str:
    remotes = _git(vault, "remote", check=False).stdout.split()
    return remotes[0] if remotes else "origin"


def _has_upstream(vault: Path) -> bool:
    return (
        _git(
            vault, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False
        ).returncode
        == 0
    )


def _git_or_exit(vault: Path, *args: str, hint: str = "") -> subprocess.CompletedProcess:
    result = _git(vault, *args, check=False)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        suffix = f"\n\n{hint}" if hint else ""
        exit_error(f"git {' '.join(args)} failed:\n{message}{suffix}")
    return result


def _push_hint(vault: Path) -> str:
    return (
        "The remote has commits your vault does not. Run `koda pull` to merge them "
        "first, or overwrite the remote with this vault via "
        f"`git -C {vault} push --force origin <branch>` (discards the remote's history)."
    )


def _rebase_hint(vault: Path) -> str:
    return (
        f"Resolve the conflicts in {vault}, then `git -C {vault} rebase --continue` "
        f"(or `git -C {vault} rebase --abort` to back out)."
    )


def _pull_rebase_if_remote(vault: Path) -> None:
    if _has_remote(vault) and _has_upstream(vault):
        _git_or_exit(vault, "pull", "--rebase", hint=_rebase_hint(vault))


def _push_if_remote(vault: Path) -> None:
    if not _has_remote(vault):
        console.print(
            "[yellow]No git remote configured — committed locally only.[/yellow]\n"
            f"[dim]Add one with `git -C {vault} remote add origin <url>`, then push again.[/dim]"
        )
        return
    if _has_upstream(vault):
        _git_or_exit(vault, "push", hint=_push_hint(vault))
    else:
        _git_or_exit(
            vault,
            "push",
            "-u",
            _first_remote(vault),
            _current_branch(vault),
            hint=_push_hint(vault),
        )


def _report_conflicts(groups: list[dict]) -> None:
    console.print("[bold red]idx conflicts detected — not auto-resolved.[/bold red]")
    console.print("These entries share a display index; koda skips them and asks you to choose.")
    for g in groups:
        console.print(f"\n  [bold cyan]idx {g['idx']}[/bold cyan]")
        for e in g["entries"]:
            console.print(Text.assemble("    ", format_conflict_entry(e)))
    console.print(
        "\n[dim]Resolve with `koda resolve` (interactive) or `koda resolve --ours|--theirs`, "
        "then `koda push`.[/dim]"
    )


def _report_sync(diff: dict) -> None:
    added, removed, moved = diff["added"], diff["removed"], diff["moved"]
    parts = []
    if added:
        parts.append(f"[green]+{len(added)} added[/green]")
    if removed:
        parts.append(f"[red]-{len(removed)} removed[/red]")
    if moved:
        parts.append(f"[yellow]{len(moved)} moved[/yellow]")
    if parts:
        console.print("Sync: " + ", ".join(parts))
    for uid, old, new in moved:
        console.print(f"  [yellow]{old} → {new}[/yellow] {uid}")


def _exit_if_unresolved(vault: Path, db) -> None:
    """Exit if conflicts are recorded in the ledger or present on disk (recording the latter)."""
    groups = load_conflicts(vault)
    if not groups:
        groups = find_conflicts(db)
        if groups:
            annotate_groups(groups, snapshot_idx(db))
            save_conflicts(vault, groups)
    if groups:
        exit_error(
            "Unresolved idx conflicts present — run `koda resolve` first.",
            code=ExitCode.CONFLICT,
        )


def _reconcile_after_pull(vault: Path, db, before: dict[str, int]) -> tuple[dict, list[dict]]:
    """Reconcile after a pull; return ``(diff, new_conflict_groups)``."""
    reconcile_all(db, get_entries_dir())
    after = snapshot_idx(db)
    diff = compute_sync_diff(before, after)
    groups = find_conflicts(db)
    if groups:
        annotate_groups(groups, before)
        save_conflicts(vault, groups)
    return diff, groups


def _classify_changes(status_output: str) -> dict[str, str]:
    """``git status --porcelain`` → ``{path: 'new'|'updated'|'deleted'}`` (renames: new path)."""
    result: dict[str, str] = {}
    for line in status_output.splitlines():
        if not line:
            continue
        xy, path = line[:2], line[3:]
        if " -> " in path:  # rename: "R  old -> new"
            path = path.split(" -> ", 1)[1]
        if xy == "??":
            result[path] = "new"
        elif "D" in xy:
            result[path] = "deleted"
        elif "A" in xy or "M" in xy or "R" in xy:
            result[path] = "updated"
    return result


def _local_changes(vault: Path) -> dict[str, str]:
    status = _git(
        vault,
        "-c",
        "core.quotepath=false",
        "status",
        "--porcelain",
        "--untracked-files=all",
        check=False,
    ).stdout
    return _classify_changes(status)


def _incoming_changes(vault: Path) -> dict[str, str]:
    if not _has_upstream(vault):
        return {}
    name_status = _git(
        vault,
        "-c",
        "core.quotepath=false",
        "diff",
        "--name-status",
        "HEAD",
        "@{u}",
        "--",
        "entries/",
        check=False,
    ).stdout
    kinds = {"A": "new", "M": "updated", "R": "updated", "D": "deleted"}
    result: dict[str, str] = {}
    for line in name_status.splitlines():
        parts = line.split("\t")
        if parts and parts[0] and parts[0][0] in kinds:
            result[parts[-1]] = kinds[parts[0][0]]
    return result


def _print_changes(changes: dict[str, str]) -> None:
    for kind in ("new", "updated", "deleted"):
        for path in sorted(p for p, k in changes.items() if k == kind):
            console.print(f"  {kind:<8} {path}")


def _require_synced_repo(vault: Path) -> None:
    if not _is_repo(vault):
        exit_error("Vault is not a git repository yet. Run `koda push` first.")
    if not _has_remote(vault):
        exit_error("No git remote configured for the vault.")


def _preview_push(vault: Path) -> None:
    """``koda push -n``: read-only; the only network call is ``git fetch``."""
    if not _is_repo(vault):
        exit_error("Vault is not a git repository yet. Run `koda push` first.")
    changes = _local_changes(vault)
    if changes:
        _print_changes(changes)
        console.print(f"[dim]{len(changes)} change(s) would be committed.[/dim]")
    else:
        console.print("[yellow]No entry changes — nothing to push.[/yellow]")

    _git(vault, "fetch", check=False)
    if _has_upstream(vault):
        ahead = _git(vault, "rev-list", "--count", "@{u}..HEAD", check=False).stdout.strip()
        behind = _git(vault, "rev-list", "--count", "HEAD..@{u}", check=False).stdout.strip()
        remote = f"{_first_remote(vault)}/{_current_branch(vault)}"
        if ahead not in ("", "0"):
            console.print(f"[dim]Would push {ahead} commit(s) to {remote}.[/dim]")
        if behind not in ("", "0"):
            console.print(
                f"[yellow]Remote has {behind} commit(s) your vault does not — "
                f"run `koda pull` first.[/yellow]"
            )


@app.command(rich_help_panel="Git sync")
def push(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview what a push would commit and push, without writing anything.",
    ),
):
    """Commit the vault's Markdown entries and push to the git remote."""
    init_db()
    vault = get_vault()
    db = get_db()
    _require_git()
    if dry_run:
        _preview_push(vault)
        return
    _ensure_repo(vault)
    _exit_if_unresolved(vault, db)

    # Commit first so `pull --rebase` sees a clean tree.
    _git_or_exit(vault, "add", "-A")
    if _git(vault, "diff", "--cached", "--quiet", check=False).returncode != 0:
        _git_or_exit(vault, "commit", "-m", "koda: sync entries")

    before = snapshot_idx(db)
    _pull_rebase_if_remote(vault)
    diff, groups = _reconcile_after_pull(vault, db, before)

    if groups:
        _report_sync(diff)
        _report_conflicts(groups)
        exit_error(
            "Not pushing — resolve idx conflicts first (`koda resolve`).",
            code=ExitCode.CONFLICT,
        )

    _push_if_remote(vault)
    console.print("[green]Push complete.[/green]")


@app.command(rich_help_panel="Git sync")
def pull(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Fetch and show incoming changes without applying them."
    ),
):
    """Pull entries from the git remote; changed entries become source=remote until reviewed."""
    init_db()
    vault = get_vault()
    db = get_db()
    _require_git()
    _require_synced_repo(vault)

    if dry_run:
        _git(vault, "fetch", check=False)
        if not _has_upstream(vault):
            console.print("[yellow]No upstream branch to compare against.[/yellow]")
            return
        diff = _git(vault, "diff", "--stat", "HEAD", "@{u}", check=False).stdout.strip()
        console.print(diff or "[green]Up to date with the remote.[/green]")
        return

    _exit_if_unresolved(vault, db)

    before = snapshot_idx(db)
    _pull_rebase_if_remote(vault)
    diff, groups = _reconcile_after_pull(vault, db, before)

    if groups:
        _report_sync(diff)
        _report_conflicts(groups)
        exit_error(
            "Sync paused on idx conflicts — run `koda resolve`, then `koda push`.",
            code=ExitCode.CONFLICT,
        )

    _report_sync(diff)
    console.print("[green]Pull complete.[/green]")


@app.command(rich_help_panel="Git sync")
def diff():
    """Read-only: what `koda push` would publish and `koda pull` would bring in."""
    init_db()
    vault = get_vault()
    _require_git()
    _require_synced_repo(vault)

    _git(vault, "fetch", check=False)
    local = _local_changes(vault)
    pulled = _incoming_changes(vault)

    if not local and not pulled:
        console.print("[green]In sync with the remote.[/green]")
        return

    if local:
        console.print("Local changes — would be pushed with `koda push`:")
        _print_changes(local)
    if pulled:
        console.print("Remote changes — would be pulled with `koda pull`:")
        _print_changes(pulled)

    console.print(
        f"[cyan]{len(local)} change(s) would be pushed · "
        f"{len(pulled)} change(s) would be pulled[/cyan]"
    )
    if local:
        console.print("[dim]Next: `koda push` to publish local changes.[/dim]")
    if pulled:
        console.print("[dim]Next: `koda pull` to merge remote changes.[/dim]")
    if local and pulled:
        console.print(
            "[dim]Concurrent edits to the same entry surface as a git conflict on "
            "that file — resolve it in the vault, then `koda pull` again.[/dim]"
        )
