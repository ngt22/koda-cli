import typer
import sqlite3
import sys
from click.utils import make_str
from typer.core import TyperGroup
import os
import subprocess
import tempfile
import re
import signal
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from rich.console import Console
from rich.table import Table

# --- Metadata ---
__app_name__ = "koda"
__version__ = "1.0.0"

# --- Configuration & Paths ---


class KodaGroup(TyperGroup):
    """Map `koda <id> [ids...] [opts]` to `koda raw <id> [ids...] [opts]` when first arg is all digits."""

    def resolve_command(self, ctx, args):
        if args:
            cmd_name = make_str(args[0])
            if self.get_command(ctx, cmd_name) is None and cmd_name.isdigit():
                raw_cmd = self.get_command(ctx, "raw")
                if raw_cmd is not None:
                    return "raw", raw_cmd, list(args)
        return super().resolve_command(ctx, args)


app = typer.Typer(
    help=(
        "Koda — memos and terminal snippets in SQLite. "
        "Run with no subcommand to print the latest entry body (same as `koda raw`)."
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
    cls=KodaGroup,
    invoke_without_command=True,
    no_args_is_help=False,
)
console = Console()

# XDG Base Directory Specification compliant paths
DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "koda"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "koda.db"

# Allow overriding DB path via environment variable
DB_PATH = Path(os.getenv("KODA_DB_PATH", DEFAULT_DB_PATH))

# --- System Utilities ---
def version_callback(value: bool):
    if value:
        console.print(f"{__app_name__} version: [bold cyan]{__version__}[/bold cyan]")
        raise typer.Exit()

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# --- Database Functions ---
def init_db():
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT,
                    tags TEXT,
                    created_at TIMESTAMP,
                    modified_at TIMESTAMP
                )
            """)
    except Exception as e:
        console.print(f"[red]Database Error:[/red] {e}")
        raise typer.Exit(code=1)

def get_memos(query=None, tag=None, limit=20):
    sql = "SELECT id, content, tags, created_at FROM memos WHERE 1=1"
    params = []
    if query:
        sql += " AND content LIKE ?"
        params.append(f"%{query}%")
    if tag:
        sql += " AND tags LIKE ?"
        params.append(f"%{tag}%")
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(sql, params).fetchall()

def delete_memo(memo_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT content FROM memos WHERE id = ?", (memo_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM memos WHERE id = ?", (memo_id,))
            return row[0]
        return None

def get_latest_memo_id() -> Optional[int]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM memos ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    return row[0] if row else None

def resolve_memo_id(explicit: Optional[int]) -> int:
    if explicit is not None:
        return explicit
    latest = get_latest_memo_id()
    if latest is None:
        console.print("[yellow]No entries in database.[/yellow]")
        raise typer.Exit(code=1)
    return latest

def apply_vars(content: str, var: Optional[List[str]]) -> str:
    """Replace {{KEY}} placeholders using KEY=VALUE pairs from --var options."""
    if not var:
        return content
    mapping = {}
    for v in var:
        if "=" in v:
            k, val = v.split("=", 1)
            mapping[k.strip()] = val
    return re.sub(r"\{\{(\w+)\}\}", lambda m: mapping.get(m.group(1), m.group(0)), content)


def emit_raw(entry_id: Optional[int]) -> None:
    """Print memo body to stdout only (shared by bare `koda`)."""
    init_db()
    memo_id = resolve_memo_id(entry_id)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT content FROM memos WHERE id = ?", (memo_id,)).fetchone()
    if not row:
        print(f"Error: Entry {memo_id} not found.", file=sys.stderr)
        raise typer.Exit(code=1)
    sys.stdout.write(row[0] if row[0] is not None else "")

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
):
    """Default: print the latest memo body to stdout (see `koda raw`)."""
    if ctx.invoked_subcommand is None:
        emit_raw(None)

def update_memo_full(memo_id: int, content: str, tags: str, created_at: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE memos SET content = ?, tags = ?, created_at = ?, modified_at = ? WHERE id = ?",
            (content.strip(), tags, created_at, now, memo_id)
        )


def _normalize_footer_segment(segment: str) -> str:
    """Strip a leading standalone --- line (duplicate delimiter from older saves)."""
    lines = segment.strip().splitlines()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if not t:
            i += 1
            continue
        if re.fullmatch(r"-{3,}", t):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def _looks_like_koda_footer(segment: str) -> bool:
    """True if this --- delimited segment is Koda's metadata block."""
    s = _normalize_footer_segment(segment)
    if not s:
        return False
    if s.startswith("# Metadata"):
        return True
    lines = [ln for ln in s.splitlines() if ln.strip()]
    return bool(lines and lines[0].strip().startswith("tags:"))


def _first_footer_index(parts: List[str]) -> Optional[int]:
    """Index of the first segment after the body that looks like a metadata footer."""
    for i in range(1, len(parts)):
        if _looks_like_koda_footer(parts[i]):
            return i
    return None


def _last_footer_segment(parts: List[str]) -> Optional[str]:
    for seg in reversed(parts):
        if _looks_like_koda_footer(seg):
            return _normalize_footer_segment(seg)
    return None


# --- Commands ---

@app.command()
def add(
    text: Optional[List[str]] = typer.Argument(
        None, help="Text to save (optional if using stdin or $EDITOR)."
    ),
    tag: Optional[List[str]] = typer.Option(
        None, "--tag", "-t", help="Comma-separated tag(s); repeat -t for more."
    ),
):
    """Create an entry from arguments, stdin, or your editor."""
    init_db()
    content = ""

    if not sys.stdin.isatty():
        content = sys.stdin.read().strip()
    elif text:
        content = " ".join(text)
    else:
        editor = os.environ.get('EDITOR', 'vim')
        with tempfile.NamedTemporaryFile(suffix=".tmp", mode='w+', delete=False) as tf:
            temp_path = tf.name
        try:
            subprocess.call([editor, temp_path])
            with open(temp_path, 'r') as f:
                content = f.read().strip()
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    if not content:
        console.print("[yellow]Aborted: Empty content.[/yellow]")
        return

    content = content.encode('utf-8', 'surrogateescape').decode('utf-8', 'ignore')

    all_tags = []
    if tag:
        for t in tag:
            all_tags.extend([item.strip() for item in t.split(",") if item.strip()])
    formatted_tags = ",".join(dict.fromkeys(all_tags))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO memos (content, tags, created_at, modified_at) VALUES (?, ?, ?, ?)",
            (content, formatted_tags, now, now)
        )
    
    console.print(f"[green]Saved to {__app_name__} [{cursor.lastrowid}] with tags: {formatted_tags}[/green]")

@app.command()
def rm(
    index: Optional[int] = typer.Argument(
        None, help="Entry ID (default: latest)."
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Delete without prompting (required when stdin is not a TTY)."
    ),
):
    """Delete an entry. Shows a preview and asks for confirmation unless -f is set."""
    init_db()
    memo_id = resolve_memo_id(index)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT content, tags, created_at FROM memos WHERE id = ?", (memo_id,)
        ).fetchone()

    if not row:
        console.print(f"[yellow]Entry with ID {memo_id} not found.[/yellow]")
        raise typer.Exit(code=1)

    content, tags, created_at = row
    console.print(
        f"\n[bold cyan]ID: {memo_id}[/bold cyan] | {created_at}\n"
        f"Tags: [magenta]{tags}[/magenta]\n"
        + "-" * 20
        + f"\n{content}"
    )
    console.print("\n[bold red]This entry will be deleted.[/bold red]")

    if not force:
        if not sys.stdin.isatty():
            console.print(
                "[red]Not a TTY: use [bold]-f/--force[/bold] to delete without a prompt.[/red]"
            )
            raise typer.Exit(code=1)
        try:
            reply = input("Delete this entry? [y/N]: ").strip().lower()
        except EOFError:
            console.print("\n[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)
        if reply not in ("y", "yes"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(code=0)

    deleted_content = delete_memo(memo_id)
    if deleted_content:
        preview = deleted_content.splitlines()[0][:50]
        console.print(f"[red]Deleted [{memo_id}]: {preview}...[/red]")
    else:
        console.print(f"[yellow]Entry with ID {memo_id} not found.[/yellow]")

@app.command()
def copy(
    index: Optional[int] = typer.Argument(
        None, help="Source entry ID (default: latest)."
    ),
):
    """Duplicate an entry to a new row (same body and tags)."""
    init_db()
    memo_id = resolve_memo_id(index)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT content, tags FROM memos WHERE id = ?", (memo_id,)).fetchone()
    
    if not row:
        console.print(f"[red]Error: Entry {memo_id} not found.[/red]")
        return

    content, tags = row
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "INSERT INTO memos (content, tags, created_at, modified_at) VALUES (?, ?, ?, ?)",
            (content, tags, now, now)
        )
    console.print(f"[green]Copied [{memo_id}] to new ID [{cursor.lastrowid}].[/green]")

@app.command()
def edit(
    index: Optional[int] = typer.Argument(
        None, help="Entry ID to edit (default: latest)."
    ),
):
    """Open an entry in $EDITOR (body plus tags/metadata footer)."""
    init_db()
    memo_id = resolve_memo_id(index)
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT content, tags, created_at FROM memos WHERE id = ?", (memo_id,)).fetchone()
    
    if not row:
        console.print(f"[red]Error: Entry {memo_id} not found.[/red]")
        return

    content, tags, created_at = row
    template = f"{content}\n\n---\n# Metadata\ntags: {tags}\ncreated_at: {created_at}\n---"

    editor = os.environ.get('EDITOR', 'vim')
    with tempfile.NamedTemporaryFile(
        suffix=".tmp", mode="w+", delete=False, encoding="utf-8"
    ) as tf:
        tf.write(template)
        temp_path = tf.name

    try:
        subprocess.call([editor, temp_path])
        with open(temp_path, 'r') as f:
            new_data = f.read()

        parts = re.split(r'\n---+\s*\n', new_data)
        while parts and not parts[-1].strip():
            parts.pop()

        footer_at = _first_footer_index(parts)
        if footer_at is not None:
            # Body is only the segments before the first metadata footer. This drops stray
            # duplicate footers that were accidentally stored in content by older versions.
            new_content = "\n---\n".join(parts[:footer_at]).strip()
            meta_section = _last_footer_segment(parts) or ""
            new_tags, new_created_at = tags, created_at
            for line in meta_section.splitlines():
                if line.startswith("tags:"):
                    new_tags = line.replace("tags:", "").strip()
                elif line.startswith("created_at:"):
                    new_created_at = line.replace("created_at:", "").strip()

            update_memo_full(memo_id, new_content, new_tags, new_created_at)
            console.print(f"[green]Entry {memo_id} updated.[/green]")
        else:
            new_content = "\n---\n".join(parts).strip() if parts else new_data.strip()
            update_memo_full(memo_id, new_content, tags, created_at)
            console.print(
                "[yellow]No metadata footer found; content updated, metadata preserved.[/yellow]"
            )
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@app.command(name="list")
def list_memos(
    query: Optional[str] = typer.Option(
        None, "--query", "-q", help="Substring match on memo body."
    ),
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Substring match on tags."
    ),
):
    """Show recent entries as a table."""
    init_db()
    memos = get_memos(query, tag)
    if not memos:
        console.print("[yellow]No entries found.[/yellow]")
        return

    table = Table(box=None, header_style="bold magenta", expand=True)
    table.add_column("ID", justify="right", width=4)
    table.add_column("Created At", width=19)
    table.add_column("Content", ratio=1)
    table.add_column("Tags", style="magenta", width=15)
    for m_id, content, tags, dt in memos:
        table.add_row(str(m_id), dt, (content.splitlines()[0] if content else ""), tags or "")
    console.print(table)

@app.command()
def show(
    entry_ids: Optional[List[int]] = typer.Argument(
        None, help="Entry ID(s) (default: latest). Multiple IDs print each entry in order."
    ),
    var: Optional[List[str]] = typer.Option(
        None, "--var", "-v", help="Variable substitution KEY=VALUE. Replaces {{KEY}} in body."
    ),
):
    """Print one or more entries with ID, tags, and timestamps (Rich formatted)."""
    init_db()
    ids = entry_ids if entry_ids else [resolve_memo_id(None)]
    for i, memo_id in enumerate(ids):
        if i > 0:
            console.print()
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT content, tags, created_at FROM memos WHERE id = ?", (memo_id,)
            ).fetchone()
        if not row:
            console.print(f"[red]Error: Entry {memo_id} not found.[/red]")
            raise typer.Exit(code=1)
        content = apply_vars(row[0] or "", var)
        console.print(
            f"\n[bold cyan]ID: {memo_id}[/bold cyan] | {row[2]}\n"
            f"Tags: [magenta]{row[1]}[/magenta]\n"
            + "-" * 20
            + f"\n{content}"
        )

@app.command()
def raw(
    entry_ids: Optional[List[int]] = typer.Argument(
        None, help="Entry ID(s) (default: latest). Multiple IDs concatenate bodies with a blank line."
    ),
    var: Optional[List[str]] = typer.Option(
        None, "--var", "-v", help="Variable substitution KEY=VALUE. Replaces {{KEY}} in body."
    ),
):
    """Print memo body to stdout only (plain text, no Rich). Supports multiple IDs and --var substitution."""
    init_db()
    ids = entry_ids if entry_ids else [resolve_memo_id(None)]
    for i, eid in enumerate(ids):
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT content FROM memos WHERE id = ?", (eid,)).fetchone()
        if not row:
            print(f"Error: Entry {eid} not found.", file=sys.stderr)
            raise typer.Exit(code=1)
        if i > 0:
            sys.stdout.write("\n\n")
        sys.stdout.write(apply_vars(row[0] or "", var))

if __name__ == "__main__":
    try:
        app()
    except Exception as e:
        console.print(f"[red]Fatal Error:[/red] {e}")
        sys.exit(1)
