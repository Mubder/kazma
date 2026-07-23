"""Shared SSE (Server-Sent Events) utility functions.

Used by both ``sse_chat.py`` and ``swarm_sse.py`` to avoid duplication.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["sse_frame"]


def sse_frame(event: str, data: str | dict[str, Any] | list[Any]) -> str:
    """Format a single SSE frame.

    Args:
        event: The event type name.
        data: Payload -- dict/list is JSON-serialized, str is used as-is.

    Returns:
        Formatted SSE string: ``event: <type>\\ndata: <json>\\n\\n``
    """
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"
