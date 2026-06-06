"""Shared constants for koda: timestamps, separators, schema column names."""

DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

TAG_SEPARATOR = ","

# Bulk idx remaps go through a temporary offset to avoid UNIQUE collisions
# during the swap; chosen high enough to fit any realistic memo count.
IDX_TEMP_OFFSET = 2_000_000

COLUMN_NAMES: tuple[str, ...] = (
    "id",
    "uid",
    "idx",
    "content",
    "tags",
    "shortcut",
    "created_at",
    "modified_at",
)
