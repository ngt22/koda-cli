"""CLI input parsing: indices/ranges, tag lists, --var items."""

import csv
import re
from typing import List, Optional

from ..cli_utils import exit_error


def parse_indices(specs: List[str]) -> List[int]:
    result: List[int] = []
    for spec in specs:
        m = re.fullmatch(r'(\d+)-(\d+)', spec)
        if m:
            result.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif spec.isdigit():
            result.append(int(spec))
        else:
            exit_error(f"Invalid index or range: {spec!r}")
    return result


def parse_tag_args(tag_args: Optional[List[str]]) -> List[str]:
    result: List[str] = []
    for t in (tag_args or []):
        result.extend(item.strip() for item in t.split(",") if item.strip())
    return result


def parse_var_items(var_spec: str) -> List[str]:
    """Parse a var spec into items using CSV rules: comma-delimited, "..." for quoting."""
    reader = csv.reader([var_spec], quotechar='"', delimiter=',', skipinitialspace=True)
    return list(reader)[0]
