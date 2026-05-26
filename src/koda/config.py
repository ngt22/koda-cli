"""Config management for koda: defaults, TOML I/O, validation, env overrides."""

import json
import os
import tomllib
from dataclasses import asdict, dataclass, field, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console


console = Console()


DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "koda"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "koda.db"

DEFAULT_CONFIG_DIR = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "koda"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.toml"
CONFIG_PATH = Path(os.getenv("KODA_CONFIG_PATH", DEFAULT_CONFIG_PATH))


VALID_SORT_COLUMNS = {
    "id", "idx", "uid", "tags", "content",
    "created_at", "modified_at", "shortcut",
}
VALID_LIST_COLUMNS = ["idx", "uid", "sc", "tags", "content", "created_at"]
REQUIRED_LIST_COLUMNS = {"idx"}

COLUMN_DEFS: dict = {
    "idx":        ("IDX",        {"justify": "right", "width": 4}),
    "uid":        ("UID",        {"width": 7, "style": "dim"}),
    "sc":         ("SC",         {"width": 10, "style": "bold green"}),
    "tags":       ("Tags",       {"style": "magenta", "width": 15}),
    "content":    ("Content",    {"ratio": 1}),
    "created_at": ("Created At", {"width": 19}),
}


GIT_SYNC_FORMAT_JSONL = "jsonl"


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
    list_columns: List[str] = field(default_factory=lambda: ["idx", "sc", "tags", "content"])
    db_path: str = str(DEFAULT_DB_PATH)
    db_backend: str = "local"
    turso_url: str = ""
    turso_token: str = ""
    git_sync_path: str = ""
    git_payload_file: str = "koda-sync.jsonl"
    git_sync_format: str = GIT_SYNC_FORMAT_JSONL
    exec_shell: str = "sh"


def _dotkey(field_name: str) -> str:
    section, _, key = field_name.partition("_")
    return f"{section}.{key}"


def _attr(dotkey: str) -> str:
    return dotkey.replace(".", "_", 1)


@dataclass(frozen=True)
class FieldSpec:
    type: type
    validator: Optional[Callable[[Any], bool]] = None
    error: str = ""


_FIELD_SPECS: Dict[str, FieldSpec] = {
    "defaults.cmd":  FieldSpec(
        str,
        lambda v: v in tuple(c.value for c in DefaultCmd),
        "must be 'raw', 'list', 'show', or 'add'",
    ),
    "list.per_page": FieldSpec(int, lambda v: v >= 1, "must be >= 1"),
    "list.rows":     FieldSpec(int, lambda v: v >= 0, "must be >= 0"),
    "list.truncate": FieldSpec(int, lambda v: v >= 0, "must be >= 0"),
    "list.sort_by":  FieldSpec(
        str,
        lambda v: v in VALID_SORT_COLUMNS,
        f"must be one of: {', '.join(sorted(VALID_SORT_COLUMNS))}",
    ),
    "list.desc":     FieldSpec(bool),
    "list.columns":  FieldSpec(
        list,
        lambda v: (
            isinstance(v, list)
            and len(v) > 0
            and all(c in VALID_LIST_COLUMNS for c in v)
            and REQUIRED_LIST_COLUMNS.issubset(v)
        ),
        f'must include "idx"; available: {", ".join(VALID_LIST_COLUMNS)}',
    ),
    "db.path":       FieldSpec(str),
    "db.backend":    FieldSpec(
        str,
        lambda v: v in tuple(b.value for b in DbBackend),
        "must be 'local' or 'turso'",
    ),
    "turso.url":     FieldSpec(str),
    "turso.token":   FieldSpec(str),
    "git.sync_path": FieldSpec(str),
    "git.payload_file": FieldSpec(
        str,
        lambda v: (
            isinstance(v, str)
            and len(v.strip()) > 0
            and not Path(v).is_absolute()
            and ".." not in Path(v).parts
        ),
        "must be a non-empty path relative to git.sync_path without '..' components",
    ),
    "git.sync_format": FieldSpec(
        str,
        lambda v: str(v).strip().lower() == GIT_SYNC_FORMAT_JSONL,
        f"must be {GIT_SYNC_FORMAT_JSONL!r} (case-insensitive)",
    ),
    "exec.shell":    FieldSpec(str),
}

ALL_KEYS: List[str] = list(_FIELD_SPECS.keys())


_ENV_OVERRIDES: List[Tuple[str, str, Callable[[str], Any]]] = [
    ("defaults.cmd",      "KODA_DEFAULT_CMD",      lambda v: v),
    ("db.path",           "KODA_DB_PATH",          lambda v: v),
    ("turso.url",         "KODA_TURSO_URL",        lambda v: v),
    ("turso.token",       "KODA_TURSO_TOKEN",      lambda v: v),
    ("git.sync_path",     "KODA_GIT_SYNC_PATH",    lambda v: v),
    ("git.payload_file",  "KODA_GIT_PAYLOAD_FILE", lambda v: v),
    ("git.sync_format",   "KODA_GIT_SYNC_FORMAT",  lambda v: v.strip().lower()),
]


class ValidationError(ValueError):
    """Raised by ConfigManager.validate / coerce on invalid input."""


class ConfigManager:
    """Load, save, and validate koda configuration."""

    def __init__(self, config_path: Path = CONFIG_PATH) -> None:
        self.config_path = config_path

    def load(self) -> Tuple[Config, Dict[str, str]]:
        """Return (Config, source_map). source_map[dotkey] = 'default'|'file'|'env'."""
        cfg = Config()
        sources: Dict[str, str] = {k: "default" for k in ALL_KEYS}

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
        lines: List[str] = []
        for section, values in data.items():
            lines.append(f"[{section}]")
            for k, v in values.items():
                if isinstance(v, bool):
                    lines.append(f"{k} = {'true' if v else 'false'}")
                elif isinstance(v, list):
                    items = ", ".join(f'"{c}"' for c in v)
                    lines.append(f"{k} = [{items}]")
                elif isinstance(v, str):
                    lines.append(f'{k} = "{v}"')
                else:
                    lines.append(f"{k} = {v}")
            lines.append("")
        self.config_path.write_text("\n".join(lines), encoding="utf-8")

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


def config_defaults_dict() -> Dict[str, Dict[str, Any]]:
    """Build the nested {section: {key: default}} dict from Config()."""
    out: Dict[str, Dict[str, Any]] = {}
    cfg = Config()
    for dotkey in ALL_KEYS:
        section, _, key = dotkey.partition(".")
        out.setdefault(section, {})[key] = getattr(cfg, _attr(dotkey))
    return out
