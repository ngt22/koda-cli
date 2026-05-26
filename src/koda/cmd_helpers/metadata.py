"""Editor metadata footer parsing (used by `koda edit`)."""

import re
from typing import List, Optional


def normalize_footer_segment(segment: str) -> str:
    lines = segment.strip().splitlines()
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if not t:
            i += 1
            continue
        if re.fullmatch(r"-{3,}", t):
            i += 1
            continue
        break
    return "\n".join(lines[i:]).strip()


def looks_like_koda_footer(segment: str) -> bool:
    s = normalize_footer_segment(segment)
    if not s:
        return False
    if s.startswith("# Metadata"):
        return True
    lines = [ln for ln in s.splitlines() if ln.strip()]
    return bool(lines and lines[0].strip().startswith("tags:"))


def first_footer_index(parts: List[str]) -> Optional[int]:
    for i in range(1, len(parts)):
        if looks_like_koda_footer(parts[i]):
            return i
    return None


def last_footer_segment(parts: List[str]) -> Optional[str]:
    for seg in reversed(parts):
        if looks_like_koda_footer(seg):
            return normalize_footer_segment(seg)
    return None
