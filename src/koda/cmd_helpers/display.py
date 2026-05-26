"""Rich-formatted memo display."""

from typing import Optional

from rich.console import Console


console = Console()


def print_memo(
    uid: str,
    idx: int,
    shortcut: Optional[str],
    content: Optional[str],
    tags: Optional[str],
    created_at: Optional[str],
) -> None:
    sc_str = f" | SC: [bold green]{shortcut}[/bold green]" if shortcut else ""
    console.print(
        f"\n[bold cyan]IDX: {idx}[/bold cyan] ({uid}){sc_str} | {created_at}\n"
        f"Tags: [magenta]{tags}[/magenta]\n"
        + "-" * 20
        + f"\n{content}"
    )
