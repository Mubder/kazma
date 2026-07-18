"""HITL (Human-in-the-Loop) approval API helpers.

Provides ``GET /api/pending-approvals`` which inspects the LangGraph
checkpointer for threads that are currently paused on an ``interrupt()``
call, extracts the pending tool execution details (tool name + arguments),
and returns them so the frontend can render Approve / Deny cards.

The matching ``POST /api/approve/{thread_id}`` endpoint lives in ``app.py``
inside the gateway setup closure (it needs access to the compiled graph).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["create_hitl_approval_router"]


def _extract_interrupt_info(task: Any) -> dict[str, Any] | None:
    """Extract tool name and arguments from a PregelTask interrupt payload.

    The ``tool_worker_node`` in ``graph_builder.py`` calls::

        interrupt({"type": "hitl_approval", "tool": ..., "args": ..., "message": ...})

    So the interrupt's ``value`` dict carries the information we need to
    display to the human reviewer.

    Args:
        task: A ``PregelTask`` from ``StateSnapshot.tasks``.

    Returns:
        Dict with ``tool_name`` and ``arguments`` keys, or ``None`` if the
        task has no interrupt or the payload is not recognisable.
    """
    interrupts = getattr(task, "interrupts", ())
    if not interrupts:
        return None
    for intr in interrupts:
        value = getattr(intr, "value", None)
        if isinstance(value, dict) and value.get("type") == "hitl_approval":
            return {
                "tool_name": value.get("tool", "unknown"),
                "arguments": value.get("args", value.get("arguments", {})),
                "message": value.get("message", ""),
            }
        # Fallback: some interrupt payloads may not carry the type tag but
        # still have tool/args keys
        if isinstance(value, dict) and ("tool" in value or "args" in value):
            return {
                "tool_name": value.get("tool", "unknown"),
                "arguments": value.get("args", value.get("arguments", {})),
                "message": value.get("message", ""),
            }
    return None


async def _get_pending_approvals(
    graph: Any,
    checkpointer: Any,
) -> list[dict[str, Any]]:
    """Scan all checkpointed threads and return those in an interrupt state.

    Args:
        graph:        Compiled LangGraph (Pregel) with an attached checkpointer.
        checkpointer: The underlying ``AsyncSqliteSaver`` / ``CheckpointManager``
                      whose ``conn`` we query for distinct thread IDs.

    Returns:
        List of approval dicts:
        ``{"thread_id", "tool_name", "arguments", "message"}``
    """
    if graph is None:
        return []

    # ── Enumerate distinct thread IDs from the checkpoint DB ─────────
    thread_ids: list[str] = []
    conn = getattr(checkpointer, "conn", None)
    if conn is None:
        # CheckpointManager wraps the saver
        saver = getattr(checkpointer, "_saver", None)
        conn = getattr(saver, "conn", None) if saver else None

    if conn is not None:
        try:
            cursor = await conn.execute("SELECT DISTINCT thread_id FROM checkpoints")
            rows = await cursor.fetchall()
            thread_ids = [row[0] for row in rows if row[0]]
        except Exception as exc:
            logger.warning("[HITL] Failed to enumerate threads from DB: %s", exc)
            return []
    else:
        logger.warning("[HITL] No DB connection available to enumerate threads")
        return []

    approvals: list[dict[str, Any]] = []
    for thread_id in thread_ids:
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id, "checkpoint_ns": ""}
        }
        try:
            state = await graph.aget_state(config)
        except Exception as exc:
            logger.debug("[HITL] aget_state failed for thread=%s: %s", thread_id, exc)
            continue

        if state is None:
            continue

        # A thread is "interrupted" when it has pending next nodes AND
        # at least one task with an interrupt payload.
        if not getattr(state, "next", None):
            continue

        for task in getattr(state, "tasks", ()):
            info = _extract_interrupt_info(task)
            if info is not None:
                approvals.append(
                    {
                        "thread_id": thread_id,
                        "tool_name": info["tool_name"],
                        "arguments": info["arguments"],
                        "message": info["message"],
                    }
                )

    return approvals


def create_hitl_approval_router(graph: Any, checkpointer: Any) -> APIRouter:
    """Create a router exposing the pending-approvals listing endpoint.

    Args:
        graph:        Compiled LangGraph instance (must support ``aget_state``).
        checkpointer: The checkpointer with a ``conn`` for thread enumeration.

    Returns:
        ``APIRouter`` with ``GET /api/pending-approvals`` mounted.
    """
    router = APIRouter(tags=["hitl"])

    @router.get("/api/pending-approvals")
    async def list_pending_approvals(request: Request) -> JSONResponse:
        """List all threads currently waiting for HITL tool approval.

        Returns:
            ``{"pending": [{"thread_id", "tool_name", "arguments", "message"}], "count": N}``
        """
        try:
            pending = await _get_pending_approvals(graph, checkpointer)
            return JSONResponse({"pending": pending, "count": len(pending)})
        except Exception as exc:
            logger.exception("[HITL] Failed to list pending approvals")
            return JSONResponse(
                {"pending": [], "count": 0, "error": str(exc)},
                status_code=500,
            )

    return router
