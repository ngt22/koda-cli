"""Koda CLI entry point: alias resolution, the Typer ``app``, and command wiring.

The actual subcommands live in ``koda.commands`` and shared runtime helpers in
``koda.runtime``. This module only owns the alias map, the ``KodaGroup`` resolver,
the root ``app`` / callback, and the imports that register every command.
"""

import typer
from typer.core import TyperGroup

from .cmd_helpers.display import print_memo as _print_memo
from .commands.config import config_app
from .runtime import (
    emit_raw,
    get_config,
    init_db,
    resolve_ref,
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
        "Koda — memos and terminal snippets in SQLite. "
        "Run with no subcommand to print the latest entry body (same as `koda raw`).\n\n"
        "One-letter aliases:\n"
        "a=add c=copy d=remove e=edit g=config h=shift k=compact\n"
        "l=list m=move p=pick r=raw s=show t=tag w=swap x=exec"
    ),
    context_settings={"help_option_names": ["-h", "--help"]},
    cls=KodaGroup,
    invoke_without_command=True,
    no_args_is_help=False,
)
app.add_typer(config_app, name="config")


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
):
    """Default: run the default command (see `koda config get defaults.cmd`)."""
    if ctx.invoked_subcommand is None:
        cmd = get_config().defaults_cmd
        if cmd == "list":
            memo._list_memos_impl()
        elif cmd == "show":
            init_db()
            row = resolve_ref(None)
            _print_memo(row.uid, row.idx, row.shortcut, row.content, row.tags, row.created_at)
        elif cmd == "add":
            memo._add_impl()
        else:
            emit_raw(None)


# Importing the command modules runs their @app.command decorators, registering
# every subcommand on ``app``. These imports must follow ``app`` (and ALIASES /
# RESERVED_SHORTCUTS, which the command modules import from here).
from .commands import exec as _exec  # noqa: E402,F401
from .commands import git, index, memo  # noqa: E402,F401

if __name__ == "__main__":
    app()
