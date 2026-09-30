"""Small pure helpers shared by the SSE chat router.

Extracted from the former 3,099-line ``kazma_ui/sse_chat.py``
(audit O5). Bodies are unchanged.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

# Shared mutable state: create_sse_chat_router() installs its _get_graph
# closure here and _module_graph() reads it. Defined in this module (and
# imported by __init__) so both halves mutate the SAME dict (audit O5).
_module_graph_holder: dict[str, Any] = {"getter": None}

__all__ = ["_module_graph_holder"]

router = APIRouter(tags=["chat-sse"])


# ══════════════════════════════════════════════════════════════════════════
# Detached turn registry — keeps graph tasks alive across client disconnects
# ══════════════════════════════════════════════════════════════════════════
# Strong-reference map: thread_id → running graph pump task. Prevents CPython
# from garbage-collecting the task when the SSE generator is cancelled by a
# client disconnect (refresh / tab switch). The task runs to completion;
# the checkpointer + done_callback persist the result so the client finds
# it on reload.
#
# The registry is SHARED with the WebSocket transport (kazma_ui.active_turns)
# so a WS turn is visible to the SSE duplicate-turn guard and to the session
# status endpoint — otherwise a refresh could start a second concurrent
# graph run on the same thread/checkpointer.  ``_active_turns`` stays as a
# back-compat alias to the shared dict.
from kazma_ui.active_turns import (
    active_turns,
)

_active_turns = active_turns  # type: ignore[name-defined]

# T1: strong references to detached-pump watchdog tasks so CPython never
# GCs one while its pump is still running.


# ══════════════════════════════════════════════════════════════════════════
# SSE frame helper (imported from shared utility)
# ══════════════════════════════════════════════════════════════════════════




def _convert_messages_to_dicts(langgraph_messages) -> list[dict[str, Any]]:
    dicts = []
    for m in langgraph_messages:
        role = "user"
        content = ""
        if isinstance(m, dict):
            role = m.get("role") or "user"
            content = m.get("content") or ""
        else:
            cls_name = m.__class__.__name__
            if cls_name == "AIMessage":
                role = "assistant"
            elif cls_name == "SystemMessage":
                role = "system"
            else:
                role = "user"
            content = getattr(m, "content", "")
        
        if role in ("system", "user", "assistant") and content:
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in content
                )
            dicts.append({"role": role, "content": str(content).strip()})
    return dicts

def _message_text(m: Any) -> str:
    """Extract plain assistant text from a dict or LangChain message object."""
    if m is None:
        return ""

    if isinstance(m, dict):
        role = (m.get("role") or m.get("type") or "").lower()
        if role in ("user", "system", "tool", "human"):
            return ""
        # assistant / ai / empty role with tool_calls
        if role and role not in ("assistant", "ai") and not m.get("tool_calls"):
            return ""
        text = m.get("content")
    else:
        cls = m.__class__.__name__
        role_attr = (getattr(m, "type", None) or getattr(m, "role", None) or "").lower()
        if cls not in ("AIMessage", "AIMessageChunk") and role_attr not in (
            "ai",
            "assistant",
            "",
        ):
            return ""
        text = getattr(m, "content", None)

    if isinstance(text, list):
        parts: list[str] = []
        for block in text:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                t = getattr(block, "text", None)
                if t:
                    parts.append(str(t))
        text = "".join(parts)
    if text is None:
        return ""
    return str(text).strip()

def _extract_hitl_payload(intr: Any) -> dict[str, Any] | None:
    """Normalize LangGraph interrupt objects into a hitl payload dict."""
    value = getattr(intr, "value", None)
    if value is None and isinstance(intr, dict):
        value = intr.get("value", intr)
    # Some versions wrap the value in a 1-tuple / list
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if not isinstance(value, dict):
        return None
    if value.get("type") == "hitl_approval":
        return value
    # Fallback: tool/args shape without type tag (still show a card)
    if "tool" in value or "args" in value or "tools" in value:
        return {
            "type": "hitl_approval",
            "tool": value.get("tool", "unknown"),
            "args": value.get("args", value.get("arguments", {})),
            "tools": value.get("tools") or [],
            "message": value.get("message", ""),
        }
    return None

def _last_assistant_text(messages: list[Any] | None) -> str:
    """Return the last non-empty assistant text from a message list."""
    if not messages:
        return ""
    for m in reversed(list(messages)):
        text = _message_text(m)
        if text:
            return text
    return ""

def _user_facing_reply(*parts: str) -> str:
    """Best user-facing assistant payload (plan fence un-glued). Never raises."""
    try:
        from kazma_core.agent.plan_fence import pick_user_facing_text

        return pick_user_facing_text(*parts)
    except Exception:
        logger.debug("[SSE] plan_fence pick failed", exc_info=True)
        for p in parts:
            if p and str(p).strip():
                return str(p).strip()
        return ""

def _module_store():
    """Session-store accessor for module-level helpers.

    ``_get_store`` is a closure inside the router factory; the extracted
    detached-persist/backfill helpers live at module scope and use the
    same singleton through this alias.
    """
    from kazma_ui.session_manager import get_session_manager

    return get_session_manager()

def _module_graph() -> Any:
    getter = _module_graph_holder.get("getter")
    if getter is None:
        return None
    try:
        return getter()
    except Exception:
        return None

def _is_cloud_url(base_url: str) -> bool:
    """Return True if *base_url* points to a real cloud LLM API.

    Local endpoints (localhost, 127.0.0.1, 0.0.0.0) and known local
    services (Ollama port 11434, LM Studio port 1234, LiteLLM port 4000)
    do NOT require a real API key and are excluded.
    """
    if not base_url:
        return False
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    port = parsed.port

    # Local addresses never need a real API key
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    # Known local-service ports
    if port in (11434, 1234, 4000):
        return False
    return True


def _parse_sse_frame(frame: str) -> tuple[str, dict[str, Any]] | None:
    """Split an SSE frame into (event_type, data) or None.

    Line-scoped field parsing per the SSE spec: ``id:`` / ``retry:`` lines
    may legally precede or follow ``event:`` (Turn Delivery V2 prepends
    ``id: <seq>``), so the first line is not assumed to be the event field.
    """
    try:
        ev_type = ""
        data: dict[str, Any] = {}
        for line in frame.split("\n"):
            if line.startswith("event: "):
                ev_type = line[len("event: "):].strip()
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if not ev_type:
            return None
        return ev_type, data
    except (json.JSONDecodeError, ValueError):
        return None


def _record_frame_activity(
    activity_log: list[dict[str, Any]], ev_type: str, data: dict[str, Any]
) -> None:
    """Append a workbench row for a tool/status frame of a turn's stream.

    The one capturer of the web chat's activity rows (the WebSocket had a
    twin, ``ws_chat._record_ws_activity``, until 2026-09-30; AUD-026).
    """
    try:
        # Both frames carry tool_call_id; keyed by it, a call's running and
        # done rows are ONE part. This dropped it, so the stored row keyed on
        # name + state + text and "Running..." stuck beside "Done"
        # (2026-09-24).
        _cid = str(data.get("tool_call_id") or "")
        if ev_type == "tool_call":
            activity_log.append({
                **({"id": "tool#" + _cid} if _cid else {}),
                "kind": "tool",
                "title": str(data.get("tool_name") or "tool"),
                "detail": str(data.get("inputs") or ""),
                "state": "running",
                "ts": datetime.now(UTC).isoformat(),
            })
        elif ev_type == "tool_result":
            activity_log.append({
                **({"id": "tool#" + _cid} if _cid else {}),
                "kind": "tool",
                "title": str(data.get("tool_name") or "tool"),
                "detail": str(data.get("result") or ""),
                "state": "done",
                "ts": datetime.now(UTC).isoformat(),
            })
        elif ev_type == "status_update":
            status = str(data.get("status") or "").strip()
            # Only persist meaningful progress states; skip the synthesizing
            # heartbeat (cosmetic, noisy on reload).
            if status and status != "synthesizing":
                activity_log.append({
                    "kind": "status",
                    "title": status,
                    "state": "running",
                    "ts": datetime.now(UTC).isoformat(),
                })
    except Exception:
        logger.debug("[SSE] activity capture failed", exc_info=True)
