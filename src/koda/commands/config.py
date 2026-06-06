"""The `config` subcommand group: show, get, set, unset, reset, edit, path.

This module builds its own ``config_app`` Typer group; ``koda.main`` mounts it
with ``app.add_typer``. It must not import ``koda.main`` so that main can import
``config_app`` at assembly time without a circular import.
"""

import json
import os
import subprocess
import sys

import typer

from ..cli_utils import confirm, exit_error
from ..config import ALL_KEYS as _ALL_KEYS
from ..config import CONFIG_PATH, EXAMPLE_TEMPLATE, ConfigManager, ValidationError
from ..runtime import console, get_config, get_config_manager, get_config_sources

config_app = typer.Typer(
    name="config",
    help="View and modify Koda configuration. Alias: `koda g`.",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _render_config(json_output: bool) -> None:
    """Print every setting with its value (and source, in the table view)."""
    if json_output:
        out: dict[str, dict[str, object]] = {}
        for dotkey in _ALL_KEYS:
            section, _, key = dotkey.partition(".")
            val = ConfigManager.get(get_config(), dotkey)
            if dotkey == "turso.token" and val:
                val = "****"  # never emit the token, consistent with the table view
            out.setdefault(section, {})[key] = val
        sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        return
    key_width = max(len(k) for k in _ALL_KEYS)
    src_labels = {
        "default": "[dim]default[/dim]",
        "file": "[green]file[/green]",
        "env": "[cyan]env[/cyan]",
    }
    for dotkey in _ALL_KEYS:
        val = ConfigManager.get(get_config(), dotkey)
        src = get_config_sources().get(dotkey, "default")
        label = src_labels.get(src, "[dim]default[/dim]")
        display_val = "****" if dotkey == "turso.token" and val else str(val)
        console.print(f"  {dotkey:<{key_width}} = {display_val:<24} {label}")


@config_app.callback(invoke_without_command=True)
def config_show(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False, "--json", help="Output the resolved config as hierarchical JSON."
    ),
) -> None:
    """Show all settings with their current values and source.

    Running bare `koda config` is equivalent to `koda config show`.
    """
    if ctx.invoked_subcommand is not None:
        return
    _render_config(json_output)


@config_app.command("show")
def config_show_cmd(
    json_output: bool = typer.Option(
        False, "--json", help="Output the resolved config as hierarchical JSON."
    ),
) -> None:
    """Show all settings with their current values and source (same as bare `koda config`)."""
    _render_config(json_output)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config key (e.g. defaults.cmd)."),
) -> None:
    """Print a single config value (plain text, for scripting)."""
    if key not in _ALL_KEYS:
        exit_error(f"Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}")
    sys.stdout.write(str(ConfigManager.get(get_config(), key)) + "\n")


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(..., help="Config key (e.g. list.per_page)."),
    value: str = typer.Argument(..., help="New value."),
) -> None:
    """Write a setting to the config file."""
    if key not in _ALL_KEYS:
        exit_error(f"Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}")
    try:
        coerced = ConfigManager.coerce(key, value)
        ConfigManager.validate(key, coerced)
    except ValidationError as e:
        exit_error(str(e))
    sec, subkey = key.split(".", 1)
    try:
        file_data = get_config_manager().read_raw()
    except ValidationError as e:
        exit_error(str(e))
    if sec not in file_data:
        file_data[sec] = {}
    file_data[sec][subkey] = coerced
    get_config_manager().write_raw(file_data)
    console.print(f"[green]Set {key} = {coerced!r}[/green]")


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(..., help="Config key to remove from the file."),
) -> None:
    """Remove a key from the config file (reverts to default)."""
    if key not in _ALL_KEYS:
        exit_error(f"Unknown key: {key!r}. Valid keys: {', '.join(sorted(_ALL_KEYS))}")
    sec, subkey = key.split(".", 1)
    try:
        file_data = get_config_manager().read_raw()
    except ValidationError as e:
        exit_error(str(e))
    if sec not in file_data or subkey not in file_data[sec]:
        console.print(f"[yellow]{key} is not set in the config file.[/yellow]")
        return
    del file_data[sec][subkey]
    if not file_data[sec]:
        del file_data[sec]
    get_config_manager().write_raw(file_data)
    default_val = ConfigManager.default_for(key)
    console.print(f"[green]Unset {key} (reverts to default: {default_val!r})[/green]")


@config_app.command("reset")
def config_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Reset without prompting."),
) -> None:
    """Delete the config file, reverting all settings to defaults."""
    if not CONFIG_PATH.exists():
        console.print("[yellow]No config file found.[/yellow]")
        return
    if not force and not confirm(f"Delete config file at {CONFIG_PATH}?"):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit(code=0)
    CONFIG_PATH.unlink()
    console.print(f"[green]Config reset (deleted {CONFIG_PATH}).[/green]")


@config_app.command("edit")
def config_edit_cmd() -> None:
    """Open the config file in $EDITOR."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(EXAMPLE_TEMPLATE, encoding="utf-8")
    editor = os.environ.get("EDITOR", "vim")
    subprocess.call([editor, str(CONFIG_PATH)])


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the path to the config file."""
    sys.stdout.write(str(CONFIG_PATH) + "\n")
