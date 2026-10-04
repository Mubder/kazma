"""Bounded opaque keyset cursors shared by Studio inventories."""
from __future__ import annotations

import base64
import binascii
import json
import math
from typing import Any


def decode_cursor(cursor: str, *, size: int) -> tuple[Any, ...] | None:
    if not cursor:
        return None
    try:
        if len(cursor) > 1024:
            raise ValueError("Cursor exceeds limit")
        values = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        if (not isinstance(values, list) or len(values) != size
                or type(values[0]) not in (int, float) or not math.isfinite(values[0])
                or any(not isinstance(value, str) or len(value) > 300 for value in values[1:])):
            raise ValueError("Invalid cursor fields")
        return tuple(values)
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise ValueError("Invalid inventory cursor") from exc


def encode_cursor(values: tuple[Any, ...]) -> str:
    return base64.urlsafe_b64encode(json.dumps(values, separators=(",", ":")).encode()).decode()
