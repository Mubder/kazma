"""Single brain entry — every mouth invokes the supervisor through here.

Web SSE, the gateway, and the TUI must not each invent a chat loop.
``run_agent_turn`` is the ainvoke + HITL-peek + final-text extract used
by in-process mouths (gateway today; SSE streams on the same graph).
The TUI, running in another process, is a mouth of the *same* server
via ``stream_chat_turn`` → ``POST /api/chat/stream``.
"""

from __future__ import annotations

import asyncio
import logging
import os
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


async def _ainvoke(graph: Any, state: Any, cfg: dict[str, Any]) -> Any:
    """The only ``graph.ainvoke`` in kazma_core production code."""
    return await graph.ainvoke(state, cfg)


async def _close_if_ui(
    graph: Any,
    cfg: dict[str, Any],
    *,
    thread_id: str,
    interrupted: bool,
    streamed_text: str,
) -> None:
    """Project the checkpoint into SessionStore. Fail open without the UI."""
    try:
        from kazma_ui.turn_runtime import close_turn
    except Exception:
        return
    try:
        await close_turn(
            graph,
            cfg,
            thread_id=thread_id,
            interrupted=interrupted,
            streamed_text=streamed_text or "",
        )
    except Exception:
        logger.debug("[turn] close_turn skipped thread=%s", thread_id[:12], exc_info=True)


async def run_agent_turn(
    *,
    graph: Any,
    thread_id: str,
    state: Any,
    config: dict[str, Any] | None = None,
    timeout: float | None = None,
    nonstop_config: Any = None,
    persist: bool = True,
) -> TurnResult:
    """Invoke the supervisor graph once and return a structured result.

    Callers (gateway, agent runner, CLI, tests) must pass the *live*
    compiled graph (holder/getter), never a mount-time snapshot.
    Platform IDs must not be in ``state`` (AGENTS.md §2).

    ``close_turn`` is attempted in ``finally`` so a headless finish
    (HITL pause, auto-deny, timeout) still writes the session when the
    UI package is loaded. Missing UI is fail-open.
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
    result_state: dict[str, Any] = {}
    interrupt: dict[str, Any] | None = None
    text = ""
    try:
        ns_cfg = nonstop_config
        if ns_cfg is None:
            try:
                from kazma_core.agent.nonstop import get_nonstop_config

                ns_cfg = get_nonstop_config()
            except Exception:
                ns_cfg = None

        if timeout is None:
            raw_to = (os.environ.get("KAZMA_TURN_TIMEOUT_SECONDS") or "").strip()
            if raw_to:
                try:
                    timeout = float(raw_to)
                except ValueError:
                    timeout = None

        if ns_cfg is not None and getattr(ns_cfg, "enabled", False):
            from kazma_core.agent.supervisor_watchdog import (
                reset_heartbeat,
                supervised_invoke,
            )

            try:
                raw = await supervised_invoke(
                    graph,
                    state,
                    cfg,
                    nonstop_config=ns_cfg,
                    turn_timeout=float(timeout or 0),
                    invoke_fn=_ainvoke,
                )
            finally:
                reset_heartbeat(thread_id)
        elif timeout is not None and timeout > 0:
            try:
                raw = await asyncio.wait_for(_ainvoke(graph, state, cfg), timeout=timeout)
            except TimeoutError:
                logger.error(
                    "[turn] timed out after %.0fs (thread=%s)",
                    timeout,
                    thread_id,
                )
                err = (
                    f"⚠️ Turn timed out after {int(timeout)}s. "
                    "Try a shorter request or raise KAZMA_TURN_TIMEOUT_SECONDS."
                )
                text = err
                return TurnResult(thread_id=thread_id, error=err, state=dict(state or {}))
        else:
            raw = await _ainvoke(graph, state, cfg)

        if not isinstance(raw, dict):
            result_state = {"messages": getattr(raw, "messages", [])}
        else:
            result_state = raw

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
            error=(
                str(result_state.get("error_message") or "") or None
                if turn_failed
                else None
            ),
        )
    except Exception as exc:
        logger.exception("[turn] ainvoke failed thread=%s", thread_id)
        try:
            from kazma_core.retry import friendly_llm_error

            err = friendly_llm_error(exc)
        except Exception:
            err = str(exc)
        text = err
        return TurnResult(
            thread_id=thread_id,
            error=err,
            state=dict(state) if isinstance(state, dict) else {},
        )
    finally:
        reset_current_thread_id(token)
        if persist:
            await _close_if_ui(
                graph,
                cfg,
                thread_id=thread_id,
                interrupted=interrupt is not None,
                streamed_text=text,
            )
