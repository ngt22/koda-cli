"""CLI input parsing: indices/ranges, tag lists, --var items."""

import csv
import re

from ..cli_utils import exit_error
from ..constants import TAG_SEPARATOR


def parse_indices(specs: list[str]) -> list[int]:
    result: list[int] = []
    for spec in specs:
        m = re.fullmatch(r"(\d+)-(\d+)", spec)
        if m:
            result.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif spec.isdigit():
            result.append(int(spec))
        else:
            exit_error(f"Invalid index or range: {spec!r}")
    return result


def parse_tag_args(tag_args: list[str] | None) -> list[str]:
    result: list[str] = []
    for t in tag_args or []:
        result.extend(item.strip() for item in t.split(TAG_SEPARATOR) if item.strip())
    return result


def parse_var_items(var_spec: str) -> list[str]:
    """Parse a var spec into items using CSV rules: comma-delimited, "..." for quoting."""
    reader = csv.reader([var_spec], quotechar='"', delimiter=",", skipinitialspace=True)
    return list(reader)[0]
