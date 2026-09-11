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
    header = Text("\nIDX: ", style="bold cyan")
    header.append(str(idx), style="bold cyan")
    header.append(f" ({uid})")
    if shortcut:
        header.append(" | SC: ")
        header.append(shortcut, style="bold green")
    header.append(f" | created: {created_at}")
    if modified_at and modified_at != created_at:
        header.append(f" | modified: {modified_at}")
    if source == "remote":
        header.append(" | source: remote", style="yellow")
    console.print(header)
    if title:
        # Build with Text so user-controlled content is never interpolated into
        # Rich markup — a title containing "[bold]" must render literally.
        title_line = Text()
        title_line.append("Title: ", style="bold")
        title_line.append(title)
        console.print(title_line)
    details = Text()
    details.append("Tags: ")
    details.append(str(tags), style="magenta")
    details.append("\n" + "-" * 20 + "\n")
    details.append(str(content))
    console.print(details)


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
