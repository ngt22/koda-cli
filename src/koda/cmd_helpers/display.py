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
    modified_at: str | None = None,
    source: str | None = None,
) -> None:
    sc_str = f" | SC: [bold green]{shortcut}[/bold green]" if shortcut else ""
    ts = f"created: {created_at}"
    if modified_at and modified_at != created_at:
        ts += f" | modified: {modified_at}"
    src_str = " | [yellow]source: remote[/yellow]" if source == "remote" else ""
    console.print(
        f"\n[bold cyan]IDX: {idx}[/bold cyan] ({uid}){sc_str} | {ts}{src_str}\n"
        f"Tags: [magenta]{tags}[/magenta]\n" + "-" * 20 + f"\n{content}"
    )
