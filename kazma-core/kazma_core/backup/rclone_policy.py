"""Named rclone remote targets, without command-option or backend confusion."""
from __future__ import annotations


def validate_remote(value: str) -> str:
    """Accept a configured remote and arbitrary data path; refuse inline backends."""
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("rclone remote must be a configured name:path without control characters")
    name, separator, _path = value.partition(":")
    if (not separator or not name or name.startswith("-") or name != name.strip()
            or not all(c.isalnum() or c in "_-. " for c in name)):
        raise ValueError("Use a configured rclone remote name:path; options and inline backends are refused")
    return value
