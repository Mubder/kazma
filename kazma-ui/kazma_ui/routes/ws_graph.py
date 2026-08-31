"""Dormant WS graph client.

Web chat turns run on SSE (``POST /api/chat/stream``, ``POST /api/approve/{thread_id}``).
``/ws/chat/{session_id}`` is the Turn Delivery V2 telemetry / cursor bus.

Set ``KAZMA_WS_GRAPH=1`` to restore ``send_prompt`` / ``approve_tool`` as a
second graph client (debug / emergency only). Keep this flag here so
``ws_chat.py`` stays a telemetry bus with a single gated escape hatch.
"""

from __future__ import annotations

import os

__all__ = ["ws_graph_enabled"]


def ws_graph_enabled() -> bool:
    """WS is telemetry/cursor by default. Graph turns stay on SSE.

    Set ``KAZMA_WS_GRAPH=1`` to restore ``send_prompt`` / ``approve_tool`` as a
    second graph client (debug / emergency only).
    """
    return (os.environ.get("KAZMA_WS_GRAPH") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
