"""Git sync commands: push, pull.

The vault (``~/.koda-cli``) is itself a git repository and the ``entries/*.md``
files are the synced artifact — so sync is just git on plain files. ``push``
commits and pushes the entries; ``pull`` rebases and then reconciles the cache.
No JSONL payload, no bespoke merge: concurrent edits to the same entry surface
as ordinary git conflicts on that one file.
"""

import shutil
import subprocess
from pathlib import Path

import typer

from ..cli_utils import exit_error
from ..main import app
from ..reconcile import reconcile_all
from ..runtime import console, get_db, get_entries_dir, get_vault, init_db

# Kept out of sync: the local cache/trust ledger, and Obsidian's per-machine app
# state (workspace, plugins) which would otherwise churn and conflict across
# machines. Users who deliberately want to sync Obsidian config can remove the
# line from their vault's .gitignore.
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
    """Make the vault a git repo (first run) and keep the cache and Obsidian
    app-state git-ignored."""
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
    """Run a git command, exiting cleanly with git's own message (plus an optional
    hint) instead of raising a raw traceback when it fails."""
    result = _git(vault, *args, check=False)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        suffix = f"\n\n{hint}" if hint else ""
        exit_error(f"git {' '.join(args)} failed:\n{message}{suffix}")
    return result


_PUSH_HINT = (
    "The remote has commits your vault does not. Run `koda pull` to merge them "
    "first, or overwrite the remote with this vault via "
    "`git -C ~/.koda-cli push --force origin <branch>` (discards the remote's history)."
)
_REBASE_HINT = (
    "Resolve the conflicts in ~/.koda-cli, then `git -C ~/.koda-cli rebase --continue` "
    "(or `git -C ~/.koda-cli rebase --abort` to back out)."
)


def _pull_rebase_if_remote(vault: Path) -> None:
    if _has_remote(vault) and _has_upstream(vault):
        _git_or_exit(vault, "pull", "--rebase", hint=_REBASE_HINT)


def _push_if_remote(vault: Path) -> None:
    if not _has_remote(vault):
        console.print(
            "[yellow]No git remote configured — committed locally only.[/yellow]\n"
            "[dim]Add one with `git -C ~/.koda-cli remote add origin <url>`, then push again.[/dim]"
        )
        return
    if _has_upstream(vault):
        _git_or_exit(vault, "push", hint=_PUSH_HINT)
    else:
        # First push: create the upstream branch (mirrors the old sync helper).
        _git_or_exit(
            vault, "push", "-u", _first_remote(vault), _current_branch(vault), hint=_PUSH_HINT
        )


def _classify_changes(status_output: str) -> dict[str, str]:
    """Map `git status --porcelain` output → {path: 'new'|'updated'|'deleted'}.

    Renames are reported under their new path as 'updated'; anything else
    (typechange etc.) is treated as 'updated' too.
    """
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


def _preview_push(vault: Path) -> None:
    """Read-only preview for `koda push -n`: what would be committed and pushed.

    Never stages, commits, pushes, or pulls --rebase; the only network call is
    a read-only `git fetch` (mirrors `pull -n`).
    """
    if not _is_repo(vault):
        exit_error("Vault is not a git repository yet. Run `koda push` first.")
    status = _git(
        vault, "-c", "core.quotepath=false", "status", "--porcelain",
        "--untracked-files=all", check=False,
    ).stdout
    changes = _classify_changes(status)
    if changes:
        for kind, label in (("new", "new"), ("updated", "updated"), ("deleted", "deleted")):
            for path in sorted(p for p, k in changes.items() if k == kind):
                console.print(f"  {label:<8} {path}")
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
        False, "--dry-run", "-n",
        help="Preview what a push would commit and push, without writing anything.",
    ),
):
    """Commit the vault's Markdown entries and push to the git remote. Alias: `koda push`."""
    init_db()
    vault = get_vault()
    _require_git()
    if dry_run:
        _preview_push(vault)
        return
    _ensure_repo(vault)
    _pull_rebase_if_remote(vault)

    try:
        _git(vault, "add", "-A")
    except subprocess.CalledProcessError as e:
        console.print("[red]git add failed. Resolve issues in the vault and retry.[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")
        raise typer.Exit(code=1)
    if _git(vault, "diff", "--cached", "--quiet", check=False).returncode == 0:
        console.print("[yellow]No entry changes — nothing to commit.[/yellow]")
        _push_if_remote(vault)
        console.print("[green]Push complete.[/green]")
        return

    try:
        _git(vault, "commit", "-m", "koda: sync entries")
    except subprocess.CalledProcessError as e:
        console.print("[red]git commit failed. Resolve issues in the vault and retry.[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.strip()}[/dim]")
        raise typer.Exit(code=1)
    _push_if_remote(vault)
    console.print("[green]Push complete.[/green]")


@app.command(rich_help_panel="Git sync")
def pull(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Fetch and show incoming changes without applying them."
    ),
):
    """Pull Markdown entries from the git remote and reconcile the cache. Alias: `koda pull`.

    Entries changed by the pull are detected as external edits and marked
    source=remote, so `koda x` prompts before running them until you review with
    `koda edit`.
    """
    init_db()
    vault = get_vault()
    _require_git()
    if not _is_repo(vault):
        exit_error("Vault is not a git repository yet. Run `koda push` first.")
    if not _has_remote(vault):
        exit_error("No git remote configured for the vault.")

    if dry_run:
        _git(vault, "fetch", check=False)
        if not _has_upstream(vault):
            console.print("[yellow]No upstream branch to compare against.[/yellow]")
            return
        diff = _git(vault, "diff", "--stat", "HEAD", "@{u}", check=False).stdout.strip()
        console.print(diff or "[green]Up to date with the remote.[/green]")
        return

    _pull_rebase_if_remote(vault)
    reconcile_all(get_db(), get_entries_dir())
    console.print("[green]Pull complete.[/green]")


@app.command(rich_help_panel="Git sync")
def diff():
    """Read-only pre-sync check: local vs remote changes with action hints.

    Shows what ``koda push`` would publish and what ``koda pull`` would bring
    in. Never writes to the vault (no pull --rebase, no commit, no push).
    """
    init_db()
    vault = get_vault()
    _require_git()
    if not _is_repo(vault):
        exit_error("Vault is not a git repository yet. Run `koda push` first.")
    if not _has_remote(vault):
        exit_error("No git remote configured for the vault.")

    _git(vault, "fetch", check=False)
    local = _classify_changes(
        _git(
            vault, "-c", "core.quotepath=false", "status", "--porcelain",
            "--untracked-files=all", check=False,
        ).stdout
    )
    pulled: dict[str, str] = {}
    if _has_upstream(vault):
        name_status = _git(
            vault, "-c", "core.quotepath=false", "diff", "--name-status",
            "HEAD", "@{u}", "--", "entries/", check=False,
        ).stdout
        for line in name_status.splitlines():
            parts = line.split("\t")
            if not parts:
                continue
            code, path = parts[0][0], parts[-1]
            if code == "A":
                pulled[path] = "new"
            elif code in ("M", "R"):
                pulled[path] = "updated"
            elif code == "D":
                pulled[path] = "deleted"

    if not local and not pulled:
        console.print("[green]In sync with the remote.[/green]")
        return

    if local:
        console.print("Local changes — would be pushed with `koda push`:")
        for kind, label in (("new", "new"), ("updated", "updated"), ("deleted", "deleted")):
            for path in sorted(p for p, k in local.items() if k == kind):
                console.print(f"  {label:<8} {path}")
    if pulled:
        console.print("Remote changes — would be pulled with `koda pull`:")
        for kind, label in (("new", "new"), ("updated", "updated"), ("deleted", "deleted")):
            for path in sorted(p for p, k in pulled.items() if k == kind):
                console.print(f"  {label:<8} {path}")

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
