"""Rich display helpers. User content is always appended as Text, never as markup."""

from rich.text import Text

from ..cli_utils import console
from ..models import MemoRow


def first_line(content: str | None) -> str:
    return ((content or "").splitlines() or [""])[0]


def print_memo(row: MemoRow) -> None:
    header = Text("\nIDX: ", style="bold cyan")
    header.append(str(row.idx), style="bold cyan")
    header.append(f" ({row.uid})")
    if row.shortcut:
        header.append(" | SC: ")
        header.append(row.shortcut, style="bold green")
    header.append(f" | created: {row.created_at}")
    if row.modified_at and row.modified_at != row.created_at:
        header.append(f" | modified: {row.modified_at}")
    if row.source == "remote":
        header.append(" | source: remote", style="yellow")
    console.print(header)
    if row.title:
        console.print(Text.assemble(("Title: ", "bold"), row.title))
    details = Text()
    details.append("Tags: ")
    details.append(str(row.tags), style="magenta")
    details.append("\n" + "-" * 20 + "\n")
    details.append(str(row.content))
    console.print(details)


def format_conflict_entry(e: dict) -> Text:
    t = Text()
    if e.get("side") == "ours":
        t.append("OURS   ", style="bold blue")
    elif e.get("side") == "theirs":
        t.append("THEIRS ", style="bold magenta")
    else:
        t.append("? ", style="dim")
    if e.get("shortcut"):
        t.append(e["shortcut"] + " ", style="bold green")
    label = e.get("title") or first_line(e.get("content"))
    t.append(label)
    prev = e.get("prev_idx")
    if prev is not None and prev != e.get("idx"):
        t.append(f" (was {prev})", style="dim")
    return t
