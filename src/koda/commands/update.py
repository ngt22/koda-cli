"""Self-update command: ``koda update``."""

from ..main import app
from ..runtime import run_update


@app.command(rich_help_panel="Config")
def update() -> None:
    """Update koda to the latest version (detects uv / pipx / pip)."""
    run_update()
