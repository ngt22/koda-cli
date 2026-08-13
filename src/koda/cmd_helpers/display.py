"""Rich-formatted memo display."""

from rich.console import Console
from rich.text import Text

console = Console()


def print_memo(
    uid: str,
    idx: int,
    shortcut: str | None,
    content: str | None,
    tags: str | None,
    created_at: str | None,
    modified_at: str | None = None,
    source: str | None = None,
    title: str | None = None,
) -> None:
    sc_str = f" | SC: [bold green]{shortcut}[/bold green]" if shortcut else ""
    ts = f"created: {created_at}"
    if modified_at and modified_at != created_at:
        ts += f" | modified: {modified_at}"
    src_str = " | [yellow]source: remote[/yellow]" if source == "remote" else ""
    console.print(f"\n[bold cyan]IDX: {idx}[/bold cyan] ({uid}){sc_str} | {ts}{src_str}")
    if title:
        # Build with Text so user-controlled content is never interpolated into
        # Rich markup — a title containing "[bold]" must render literally.
        title_line = Text()
        title_line.append("Title: ", style="bold")
        title_line.append(title)
        console.print(title_line)
    console.print(f"Tags: [magenta]{tags}[/magenta]\n" + "-" * 20 + f"\n{content}")


def format_conflict_entry(e: dict) -> Text:
    """Render one idx-conflict entry (side + shortcut + label + move hint).

    Returns a :class:`Text` so the user-controlled ``title``/first content line
    is appended literally and never interpreted as Rich markup (mirrors the
    comment in :func:`print_memo`).
    """
    t = Text()
    if e.get("side") == "ours":
        t.append("OURS   ", style="bold blue")
    elif e.get("side") == "theirs":
        t.append("THEIRS ", style="bold magenta")
    else:
        t.append("? ", style="dim")
    if e.get("shortcut"):
        t.append(e["shortcut"] + " ", style="bold green")
    label = e.get("title") or ((e.get("content") or "").splitlines() or [""])[0]
    t.append(label)
    prev = e.get("prev_idx")
    if prev is not None and prev != e.get("idx"):
        t.append(f" (was {prev})", style="dim")
    return t
