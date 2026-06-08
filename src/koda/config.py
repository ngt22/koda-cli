"""Config management for koda: defaults, TOML I/O, validation, env overrides."""

import json
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 has no stdlib tomllib; fall back to the tomli backport.
    import tomli as tomllib

from rich.console import Console

console = Console()


DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "koda"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "koda.db"

DEFAULT_CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "koda"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"
CONFIG_PATH = Path(os.getenv("KODA_CONFIG_PATH", DEFAULT_CONFIG_PATH))


VALID_SORT_COLUMNS = {
    "id",
    "idx",
    "uid",
    "tags",
    "content",
    "created_at",
    "modified_at",
    "shortcut",
}
VALID_LIST_COLUMNS = ["idx", "uid", "sc", "tags", "content", "created_at"]
REQUIRED_LIST_COLUMNS = {"idx"}

COLUMN_DEFS: dict = {
    "idx": ("IDX", {"justify": "right", "width": 4}),
    "uid": ("UID", {"width": 16, "style": "dim"}),
    "sc": ("SC", {"width": 10, "style": "bold green"}),
    "tags": ("Tags", {"style": "magenta", "width": 15}),
    "content": ("Content", {"ratio": 1}),
    "created_at": ("Created At", {"width": 19}),
}


GIT_SYNC_FORMAT_JSONL = "jsonl"

EXEC_SHELL_ALLOWLIST = ("sh", "bash", "zsh", "fish")


# Commented-out scaffold written to a fresh config file by `koda config edit`.
EXAMPLE_TEMPLATE = (
    "# Koda configuration\n"
    "# Uncomment and edit values to override defaults.\n\n"
    "# [defaults]\n"
    '# cmd = "raw"      # "raw" or "list"\n\n'
    "# [list]\n"
    "# per_page = 20\n"
    "# rows = 1         # 0 = all lines\n"
    "# truncate = 80    # 0 = no truncation\n"
    '# sort_by = "idx"\n'
    "# desc = false\n\n"
    "# [db]\n"
    f'# path = "{DEFAULT_DB_PATH}"\n'
    '# backend = "local"   # "local" or "turso"\n\n'
    "# [turso]\n"
    '# url = "libsql://your-db.turso.io"   # or set KODA_TURSO_URL\n'
    '# token = "your-auth-token"            # or set KODA_TURSO_TOKEN\n\n'
    "# [git]\n"
    '# sync_path = "/path/to/koda-sync-repo"    # clone root, or use KODA_GIT_SYNC_PATH\n'
    '# payload_file = "koda-sync.jsonl"         # relative to sync_path (JSON Lines)\n'
    '# sync_format = "jsonl"                     # or KODA_GIT_SYNC_FORMAT\n\n'
    "# [exec]\n"
    '# shell = "sh"\n'
    "# Prompt before running entries pulled from a remote (source=remote).\n"
    "# Setting this false DISABLES that safety check — a compromised sync\n"
    "# remote could then run code via `koda x` with no confirmation. Leave\n"
    "# true unless you fully trust your sync remote.\n"
    "# confirm_remote = true\n"
)


DB_PATH_OVERRIDE_ENV = "KODA_DB_PATH_OVERRIDE"


def allowed_db_roots() -> list[Path]:
    """Directories a local db.path may live under: the default koda data dir
    (~/.local/share/koda) and, when set, $XDG_DATA_HOME/koda."""
    roots = [DEFAULT_DB_DIR]
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg and xdg.strip():
        roots.append(Path(xdg) / "koda")
    return [r.expanduser() for r in roots]


def db_path_allowed(v: Any) -> bool:
    """True if ``v`` is a local DB path inside an allowed data dir. Blocks
    ``KODA_DB_PATH=/home/victim/.ssh/authorized_keys`` and similar env/config
    injection from creating arbitrary files via init_db. The
    ``KODA_DB_PATH_OVERRIDE`` env var (truthy) lifts the restriction for CI and
    tests that need a temp location."""
    if os.getenv(DB_PATH_OVERRIDE_ENV):
        return True
    if not isinstance(v, str) or not v.strip():
        return False
    try:
        resolved = Path(v).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    for root in allowed_db_roots():
        try:
            resolved.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def valid_payload_file(v: Any) -> bool:
    """True if ``v`` is a safe relative payload path. Rejects empty values,
    absolute paths, ``..`` traversal, and any path with a ``.git`` component.
    The last guard stops a tampered config from steering `koda push` into
    ``.git/hooks/post-merge`` (or any other repo-internal file), which would
    let a written payload execute on the next git operation."""
    if not isinstance(v, str) or len(v.strip()) == 0:
        return False
    parts = Path(v).parts
    if Path(v).is_absolute() or ".." in parts or ".git" in parts:
        return False
    return True


def valid_exec_shell(v: Any) -> bool:
    """True if ``v`` names an allowlisted shell that resolves to an existing
    absolute executable. Guards `koda x` against arbitrary-binary redirection
    via a tampered config (e.g. exec.shell = '/tmp/evil')."""
    if not isinstance(v, str) or not v.strip():
        return False
    if Path(v).name not in EXEC_SHELL_ALLOWLIST:
        return False
    resolved = shutil.which(v)
    return bool(resolved) and Path(resolved).is_absolute()


_TOML_STR_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def toml_basic_string(value: str) -> str:
    """Quote ``value`` as a TOML basic string with proper escaping.

    Without this, a value containing ``"`` or a newline could break out of its
    string and inject arbitrary TOML keys/tables when written back to the config
    file (e.g. forcing ``exec.confirm_remote = false``). Escaping ``\\`` and
    ``"`` plus all control characters closes that injection vector and keeps the
    value round-trippable by ``tomllib``."""
    out = []
    for ch in value:
        if ch in _TOML_STR_ESCAPES:
            out.append(_TOML_STR_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


class DefaultCmd(str, Enum):
    RAW = "raw"
    LIST = "list"
    SHOW = "show"
    ADD = "add"


class DbBackend(str, Enum):
    LOCAL = "local"
    TURSO = "turso"


@dataclass
class Config:
    """Typed snapshot of resolved configuration (defaults < file < env)."""

    defaults_cmd: str = "raw"
    list_per_page: int = 20
    list_rows: int = 1
    list_truncate: int = 80
    list_sort_by: str = "idx"
    list_desc: bool = False
    list_columns: list[str] = field(default_factory=lambda: ["idx", "sc", "tags", "content"])
    db_path: str = str(DEFAULT_DB_PATH)
    db_backend: str = "local"
    turso_url: str = ""
    turso_token: str = ""
    git_sync_path: str = ""
    git_payload_file: str = "koda-sync.jsonl"
    git_sync_format: str = GIT_SYNC_FORMAT_JSONL
    exec_shell: str = "sh"
    exec_confirm_remote: bool = True


def _dotkey(field_name: str) -> str:
    section, _, key = field_name.partition("_")
    return f"{section}.{key}"


def _attr(dotkey: str) -> str:
    return dotkey.replace(".", "_", 1)


@dataclass(frozen=True)
class FieldSpec:
    type: type
    validator: Callable[[Any], bool] | None = None
    error: str = ""


_FIELD_SPECS: dict[str, FieldSpec] = {
    "defaults.cmd": FieldSpec(
        str,
        lambda v: v in tuple(c.value for c in DefaultCmd),
        "must be 'raw', 'list', 'show', or 'add'",
    ),
    "list.per_page": FieldSpec(int, lambda v: v >= 1, "must be >= 1"),
    "list.rows": FieldSpec(int, lambda v: v >= 0, "must be >= 0"),
    "list.truncate": FieldSpec(int, lambda v: v >= 0, "must be >= 0"),
    "list.sort_by": FieldSpec(
        str,
        lambda v: v in VALID_SORT_COLUMNS,
        f"must be one of: {', '.join(sorted(VALID_SORT_COLUMNS))}",
    ),
    "list.desc": FieldSpec(bool),
    "list.columns": FieldSpec(
        list,
        lambda v: (
            isinstance(v, list)
            and len(v) > 0
            and all(c in VALID_LIST_COLUMNS for c in v)
            and REQUIRED_LIST_COLUMNS.issubset(v)
        ),
        f'must include "idx"; available: {", ".join(VALID_LIST_COLUMNS)}',
    ),
    "db.path": FieldSpec(
        str,
        db_path_allowed,
        "must be inside the koda data dir (~/.local/share/koda or "
        "$XDG_DATA_HOME/koda); set KODA_DB_PATH_OVERRIDE=1 to allow another location",
    ),
    "db.backend": FieldSpec(
        str,
        lambda v: v in tuple(b.value for b in DbBackend),
        "must be 'local' or 'turso'",
    ),
    "turso.url": FieldSpec(str),
    "turso.token": FieldSpec(str),
    "git.sync_path": FieldSpec(str),
    "git.payload_file": FieldSpec(
        str,
        valid_payload_file,
        "must be a non-empty path relative to git.sync_path without '..' or '.git' components",
    ),
    "git.sync_format": FieldSpec(
        str,
        lambda v: str(v).strip().lower() == GIT_SYNC_FORMAT_JSONL,
        f"must be {GIT_SYNC_FORMAT_JSONL!r} (case-insensitive)",
    ),
    "exec.shell": FieldSpec(
        str,
        valid_exec_shell,
        f"must be an installed shell ({', '.join(EXEC_SHELL_ALLOWLIST)}) "
        "resolvable to an absolute path",
    ),
    "exec.confirm_remote": FieldSpec(bool),
}

ALL_KEYS: list[str] = list(_FIELD_SPECS.keys())


_ENV_OVERRIDES: list[tuple[str, str, Callable[[str], Any]]] = [
    ("defaults.cmd", "KODA_DEFAULT_CMD", lambda v: v),
    ("db.path", "KODA_DB_PATH", lambda v: v),
    ("turso.url", "KODA_TURSO_URL", lambda v: v),
    ("turso.token", "KODA_TURSO_TOKEN", lambda v: v),
    ("git.sync_path", "KODA_GIT_SYNC_PATH", lambda v: v),
    ("git.payload_file", "KODA_GIT_PAYLOAD_FILE", lambda v: v),
    ("git.sync_format", "KODA_GIT_SYNC_FORMAT", lambda v: v.strip().lower()),
]


class ValidationError(ValueError):
    """Raised by ConfigManager.validate / coerce on invalid input."""


class ConfigManager:
    """Load, save, and validate koda configuration."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path

    def load(self) -> tuple[Config, dict[str, str]]:
        """Return (Config, source_map). source_map[dotkey] = 'default'|'file'|'env'."""
        cfg = Config()
        sources: dict[str, str] = {k: "default" for k in ALL_KEYS}

        if self.config_path.exists():
            try:
                with open(self.config_path, "rb") as f:
                    file_data = tomllib.load(f)
                for section, values in file_data.items():
                    if not isinstance(values, dict):
                        continue
                    for key, val in values.items():
                        dotkey = f"{section}.{key}"
                        if dotkey in _FIELD_SPECS:
                            setattr(cfg, _attr(dotkey), val)
                            sources[dotkey] = "file"
            except Exception as e:
                console.print(f"[yellow]Warning: could not read config: {e}[/yellow]")

        for dotkey, env_var, transform in _ENV_OVERRIDES:
            env_value = os.getenv(env_var)
            if env_value:
                setattr(cfg, _attr(dotkey), transform(env_value))
                sources[dotkey] = "env"

        return cfg, sources

    def read_raw(self) -> dict:
        """Read the config file as-is (no merging with defaults). Returns {} if absent."""
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            raise ValidationError(f"Could not read config file: {e}") from e

    def write_raw(self, data: dict) -> None:
        """Serialize data to TOML and write to config_path."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_path.parent, 0o700)
        lines: list[str] = []
        for section, values in data.items():
            lines.append(f"[{section}]")
            for k, v in values.items():
                if isinstance(v, bool):
                    lines.append(f"{k} = {'true' if v else 'false'}")
                elif isinstance(v, list):
                    items = ", ".join(toml_basic_string(str(c)) for c in v)
                    lines.append(f"{k} = [{items}]")
                elif isinstance(v, str):
                    lines.append(f"{k} = {toml_basic_string(v)}")
                else:
                    lines.append(f"{k} = {v}")
            lines.append("")
        self.config_path.write_text("\n".join(lines), encoding="utf-8")
        os.chmod(self.config_path, 0o600)

    @staticmethod
    def coerce(key: str, raw: str) -> Any:
        """Convert raw string from CLI/env to the field's typed value."""
        spec = _FIELD_SPECS.get(key)
        typ = spec.type if spec else str
        try:
            if typ is bool:
                if raw.lower() in ("true", "1", "yes"):
                    return True
                if raw.lower() in ("false", "0", "no"):
                    return False
                raise ValueError(raw)
            if typ is list:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError(raw)
                return parsed
            return typ(raw)
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            raise ValidationError(
                f"Invalid value for {key!r}: {raw!r} (expected {typ.__name__})"
            ) from e

    @staticmethod
    def validate(key: str, value: Any) -> Any:
        """Return value if valid; otherwise raise ValidationError."""
        spec = _FIELD_SPECS.get(key)
        if spec is None or spec.validator is None:
            return value
        if not spec.validator(value):
            raise ValidationError(f"Invalid value for {key!r}: {spec.error}")
        return value

    @staticmethod
    def default_for(key: str) -> Any:
        """Return the default value for a dotted key (used by `koda config unset`)."""
        return getattr(Config(), _attr(key))

    @staticmethod
    def error_message(key: str) -> str:
        """Return the human-readable validation error string for a dotted key."""
        spec = _FIELD_SPECS.get(key)
        return spec.error if spec else ""

    @staticmethod
    def get(cfg: Config, key: str) -> Any:
        return getattr(cfg, _attr(key))


def config_defaults_dict() -> dict[str, dict[str, Any]]:
    """Build the nested {section: {key: default}} dict from Config()."""
    out: dict[str, dict[str, Any]] = {}
    cfg = Config()
    for dotkey in ALL_KEYS:
        section, _, key = dotkey.partition(".")
        out.setdefault(section, {})[key] = getattr(cfg, _attr(dotkey))
    return out
