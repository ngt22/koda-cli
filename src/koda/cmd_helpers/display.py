"""Rich-formatted memo display."""

from rich.console import Console

console = Console()


def print_memo(
    uid: str,
    idx: int,
    shortcut: str | None,
    content: str | None,
    tags: str | None,
    created_at: str | None,
) -> None:
    sc_str = f" | SC: [bold green]{shortcut}[/bold green]" if shortcut else ""
    console.print(
        f"\n[bold cyan]IDX: {idx}[/bold cyan] ({uid}){sc_str} | {created_at}\n"
        f"Tags: [magenta]{tags}[/magenta]\n" + "-" * 20 + f"\n{content}"
    )
