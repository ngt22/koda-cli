"""CLI entry point: aliases, the root ``app``, and command registration."""

import typer
from typer.core import TyperGroup

from .cmd_helpers.display import print_memo
from .commands.config import config_app
from .models import VALID_SORT_COLUMNS
from .runtime import (
    emit_raw,
    get_config,
    init_db,
    resolve_ref,
    update_callback,
    version_callback,
)

ALIASES = {
    "a": "add",
    "c": "copy",
    "d": "remove",
    "e": "edit",
    "g": "config",
    "h": "shift",
    "k": "compact",
    "l": "list",
    "m": "move",
    "p": "pick",
    "r": "raw",
    "s": "show",
    "t": "tag",
    "w": "swap",
    "x": "exec",
}
RESERVED_SHORTCUTS = set(ALIASES.keys())

SORT_HELP = f"Sort column: {', '.join(sorted(VALID_SORT_COLUMNS))}. [config: list.sort_by]"
VAR_HELP = (
    "Variable substitution. Named: KEY=VALUE → replaces ${KEY}. "
    "Positional: VALUE → replaces $1,$2,... in order. "
    'Comma-separate multiple values; use "..." to include spaces or commas. '
    "Examples: -V 'localhost,5432' -V 'name=prod' -V '\"hello world\",\"foo,bar\"'"
)


class KodaGroup(TyperGroup):
    """Resolve bare refs (numeric idx or shortcut string) to the default command."""

    def resolve_command(self, ctx, args):
        if args:
            cmd_name = str(args[0])
            if cmd_name in ALIASES:
                args = [ALIASES[cmd_name]] + list(args[1:])
            elif self.get_command(ctx, cmd_name) is None and not cmd_name.startswith("-"):
                default_cmd = get_config().defaults_cmd
                target_name = ALIASES.get(default_cmd, default_cmd)
                target_cmd = self.get_command(ctx, target_name)
                if target_cmd is not None:
                    return target_name, target_cmd, list(args)
        return super().resolve_command(ctx, args)


app = typer.Typer(
    help=(
        "Koda — memos and terminal snippets stored as plain Markdown files "
        "(Obsidian / editor / AI friendly). "
        "Run with no subcommand to print the latest entry body (same as `koda raw`).\n\n"
        "One-letter aliases:\n" + " ".join(f"{k}={v}" for k, v in ALIASES.items())
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
    cls=KodaGroup,
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(config_app, name="config", rich_help_panel="Config")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
    update: bool | None = typer.Option(
        None,
        "--update",
        is_eager=True,
        callback=update_callback,
        help="Update koda to the latest version and exit.",
    ),
):
    """Default: run the default command (see `koda config get defaults.cmd`)."""
    if ctx.invoked_subcommand is not None:
        return
    cmd = get_config().defaults_cmd
    if cmd == "list":
        memo._list_memos_impl()
    elif cmd == "add":
        memo._add_impl()
    else:
        init_db()
        if cmd == "show":
            print_memo(resolve_ref(None))
        else:
            emit_raw(None)


# Command modules register themselves on ``app`` at import, so they must follow it.
from .commands import exec as _exec  # noqa: E402,F401
from .commands import git, index, manage, memo, resolve  # noqa: E402,F401
from .commands import update as _update  # noqa: E402,F401

if __name__ == "__main__":
    app()
