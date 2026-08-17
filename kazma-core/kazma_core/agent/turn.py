"""Single brain entry — every mouth invokes the supervisor through here.

Web SSE, the gateway, and the TUI must not each invent a chat loop.
``run_agent_turn`` is the ainvoke + HITL-peek + final-text extract used
by in-process mouths (gateway today; SSE streams on the same graph).
The TUI, running in another process, is a mouth of the *same* server
via ``stream_chat_turn`` → ``POST /api/chat/stream``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "TurnResult",
    "extract_assistant_text",
    "peek_interrupt",
    "run_agent_turn",
]


@dataclass
class TurnResult:
    """Outcome of one supervisor turn."""

    thread_id: str
    text: str = ""
    state: dict[str, Any] = field(default_factory=dict)
    interrupted: bool = False
    interrupt_payload: dict[str, Any] | None = None
    error: str | None = None
    turn_failed: bool = False


def extract_assistant_text(state: dict[str, Any] | None) -> str:
    """Last non-empty assistant message from graph state."""
    messages = (state or {}).get("messages") or []
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        content = m.get("content")
        if content and str(content).strip():
            return str(content).strip()
    return ""


async def peek_interrupt(graph: Any, config: dict[str, Any]) -> dict[str, Any] | None:
    """Return a pending ``hitl_approval`` payload if the graph is paused."""
    try:
        snapshot = await graph.aget_state(config)
    except Exception as exc:
        logger.debug("[turn] aget_state unavailable: %s", exc)
        return None
    if not getattr(snapshot, "next", None):
        return None
    for task in getattr(snapshot, "tasks", []) or []:
        for intr in getattr(task, "interrupts", []) or []:
            payload = getattr(intr, "value", None)
            if payload is None and isinstance(intr, dict):
                payload = intr.get("value", intr)
            if isinstance(payload, (list, tuple)) and payload:
                payload = payload[0]
            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                return payload
            if isinstance(payload, dict) and (
                "tool" in payload or "args" in payload or "tools" in payload
            ):
                return {
                    "type": "hitl_approval",
                    "tool": payload.get("tool", "unknown"),
                    "args": payload.get("args", payload.get("arguments", {})),
                    "tools": payload.get("tools") or [],
                    "message": payload.get("message", ""),
                }
    return None


async def run_agent_turn(
    *,
    graph: Any,
    thread_id: str,
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> TurnResult:
    """Invoke the supervisor graph once and return a structured result.

    Callers (gateway, tests, local TUI fallback) must pass the *live*
    compiled graph (holder/getter), never a mount-time snapshot.
    Platform IDs must not be in ``state`` (AGENTS.md §2).
    """
    if not thread_id:
        return TurnResult(thread_id="", error="missing thread_id")
    if graph is None:
        return TurnResult(thread_id=thread_id, error="no graph")

    try:
        from kazma_core.agent.long_task import resolve_turn_budgets

        recursion = int(resolve_turn_budgets(thread_id)["recursion_limit"])
    except Exception:
        recursion = 100
    cfg = dict(config or {})
    inner = dict(cfg.get("configurable") or {})
    inner.setdefault("thread_id", thread_id)
    inner.setdefault("checkpoint_ns", "")
    cfg["configurable"] = inner
    cfg.setdefault("recursion_limit", recursion)

    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    token = set_current_thread_id(thread_id)
    try:
        result_state = await graph.ainvoke(state, cfg)
    except Exception as exc:
        logger.exception("[turn] ainvoke failed thread=%s", thread_id)
        return TurnResult(thread_id=thread_id, error=str(exc), state=dict(state))
    finally:
        reset_current_thread_id(token)

    if not isinstance(result_state, dict):
        result_state = {"messages": getattr(result_state, "messages", [])}

    interrupt = await peek_interrupt(graph, cfg)
    text = extract_assistant_text(result_state)
    turn_failed = bool(result_state.get("turn_failed"))
    return TurnResult(
        thread_id=thread_id,
        text=text,
        state=result_state,
        interrupted=interrupt is not None,
        interrupt_payload=interrupt,
        turn_failed=turn_failed,
        error=str(result_state.get("error_message") or "") or None if turn_failed else None,
    )
