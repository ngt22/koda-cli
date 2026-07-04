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

_GITIGNORE_LINE = ".koda/"


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
    """Make the vault a git repo (first run) and keep ``.koda/`` git-ignored."""
    vault.mkdir(parents=True, exist_ok=True)
    if not _is_repo(vault):
        _git(vault, "init")
    gitignore = vault / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    if _GITIGNORE_LINE not in lines:
        lines.append(_GITIGNORE_LINE)
        gitignore.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


def _pull_rebase_if_remote(vault: Path) -> None:
    if _has_remote(vault) and _has_upstream(vault):
        _git(vault, "pull", "--rebase")


def _push_if_remote(vault: Path) -> None:
    if not _has_remote(vault):
        console.print(
            "[yellow]No git remote configured — committed locally only.[/yellow]\n"
            "[dim]Add one with `git -C ~/.koda-cli remote add origin <url>`, then push again.[/dim]"
        )
        return
    if _has_upstream(vault):
        _git(vault, "push")
    else:
        # First push: create the upstream branch (mirrors the old sync helper).
        _git(vault, "push", "-u", _first_remote(vault), _current_branch(vault))


@app.command(rich_help_panel="Git sync")
def push():
    """Commit the vault's Markdown entries and push to the git remote. Alias: `koda push`."""
    init_db()
    vault = get_vault()
    _require_git()
    _ensure_repo(vault)
    _pull_rebase_if_remote(vault)

    _git(vault, "add", "-A")
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
