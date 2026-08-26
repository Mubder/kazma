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
            tool = value.get("tool") or value.get("tool_name") or "unknown"
            msg = value.get("message")
            return {
                "tool_name": str(tool) if tool is not None else "unknown",
                "arguments": value.get("args") or value.get("arguments") or {},
                "message": "" if msg is None else str(msg),
                "kind": value.get("kind", "security"),
                "items": value.get("items") or [],
                "yolo_allowed": bool(value.get("yolo_allowed", True)),
            }
        # Fallback: some interrupt payloads may not carry the type tag but
        # still have tool/args keys
        if isinstance(value, dict) and (
            "tool" in value or "tool_name" in value or "args" in value
        ):
            tool = value.get("tool") or value.get("tool_name") or "unknown"
            msg = value.get("message")
            return {
                "tool_name": str(tool) if tool is not None else "unknown",
                "arguments": value.get("args") or value.get("arguments") or {},
                "message": "" if msg is None else str(msg),
                "kind": value.get("kind", "security"),
                "items": value.get("items") or [],
                "yolo_allowed": bool(value.get("yolo_allowed", True)),
            }
    return None


async def _enumerate_thread_ids(conn: Any) -> list[str]:
    """Return distinct thread_ids from the checkpoint store.

    Handles two backends:
      * aiosqlite (SQLite checkpointer) — ``conn`` has a top-level ``.execute()``.
      * psycopg ``AsyncConnectionPool`` (Postgres checkpointer via
        ``AsyncPostgresSaver``) — must acquire a connection first; the table
        is also namespaced under the ``public`` schema and column is ``thread_id``.
    """
    # Postgres pool: acquire a connection, run, release.
    if type(conn).__name__ == "AsyncConnectionPool" or hasattr(conn, "getconn"):
        async with conn.connection() as pg_conn:  # type: ignore[union-attr]
            async with pg_conn.cursor() as cur:  # type: ignore[union-attr]
                await cur.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id IS NOT NULL"
                )
                rows = await cur.fetchall()
            # psycopg dict_row returns dicts; aiosqlite returns tuples.
            result: list[str] = []
            for r in rows:
                tid = r["thread_id"] if isinstance(r, dict) else r[0]
                if tid:
                    result.append(tid)
            return result

    # aiosqlite connection.
    cursor = await conn.execute(  # type: ignore[union-attr]
        "SELECT DISTINCT thread_id FROM checkpoints"
    )
    rows = await cursor.fetchall()
    return [row[0] for row in rows if row[0]]


async def _get_pending_approvals(
    graph: Any,
    checkpointer: Any,
) -> list[dict[str, Any]]:
    """Scan all checkpointed threads and return those in an interrupt state.

    ONLY returns threads that are ACTIVELY pending approval (hitl_state == 'pending_approval').
    This prevents stale checkpoints from showing up in the dashboard after approval/deny.

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
            thread_ids = await _enumerate_thread_ids(conn)
        except Exception as exc:
            logger.warning("[HITL] Failed to enumerate threads from DB: %s", exc)
            return []
    else:
        logger.warning("[HITL] No DB connection available to enumerate threads")
        return []

    # ── Filter by HITL state in metadata (if available) ────────────
    # Try to get thread IDs with hitl_state == 'pending_approval' from DB
    pending_thread_ids: list[str] = []
    
    # Check if connection supports direct metadata query
    if conn is not None:
        try:
            # For SQLite (aiosqlite)
            if hasattr(conn, 'execute'):
                import json as _json
                cursor = await conn.execute(
                    "SELECT thread_id FROM checkpoints WHERE json_extract(metadata, '$.hitl_state') = ?",
                    ("pending_approval",)
                )
                rows = await cursor.fetchall()
                pending_thread_ids = [row[0] for row in rows if row[0]]
            # For Postgres (psycopg)
            elif hasattr(conn, 'connection'):
                async with conn.connection() as pg_conn:
                    async with pg_conn.cursor() as cur:
                        await cur.execute(
                            "SELECT thread_id FROM checkpoints WHERE metadata->>'hitl_state' = %s",
                            ("pending_approval",)
                        )
                        rows = await cur.fetchall()
                        pending_thread_ids = [row[0] for row in rows if row[0]]
        except Exception as exc:
            logger.debug("[HITL] Failed to query hitl_state from DB, falling back to graph scan: %s", exc)
            # Fall back to scanning all threads
            pending_thread_ids = thread_ids
    
    # If we couldn't query by state, use all thread IDs (backward compatibility)
    if not pending_thread_ids:
        pending_thread_ids = thread_ids

    approvals: list[dict[str, Any]] = []
    for thread_id in pending_thread_ids:
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
                        "yolo_allowed": info.get("yolo_allowed", True),
                    }
                )
                # Only need one interrupt per thread
                break

    return approvals


async def clear_pending_approvals(graph: Any, checkpointer: Any) -> int:
    """Clear/delete checkpoints for all threads currently in an interrupt state."""
    pending = await _get_pending_approvals(graph, checkpointer)
    cleared = 0
    for item in pending:
        thread_id = item.get("thread_id")
        if thread_id:
            try:
                if hasattr(checkpointer, "adelete_thread"):
                    await checkpointer.adelete_thread(thread_id)
                elif hasattr(checkpointer, "_saver") and hasattr(checkpointer._saver, "adelete_thread"):
                    await checkpointer._saver.adelete_thread(thread_id)
                cleared += 1
            except Exception as exc:
                logger.warning("[HITL] Failed to delete checkpoint thread=%s: %s", thread_id, exc)
    return cleared


def create_hitl_approval_router(graph: Any, checkpointer: Any) -> APIRouter:
    """Create a router exposing the pending-approvals listing endpoint.

    NOTE (SoT): the LIVE /api/pending-approvals routes are registered in
    kazma_ui/routes_direct.py — this factory is kept because
    tests/test_hitl_approval_ui.py builds a test app with it. Do not mount
    both in the real app (duplicate routes); if you change one, mirror the
    other or fold this factory away by porting its tests to routes_direct.

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
            try:
                from kazma_core.mcp.spec_client import list_sampling_pending

                pending = list(pending) + list(list_sampling_pending())
            except Exception:
                pass
            # A standalone router without tenant middleware is single-tenant.
            # When middleware establishes a tenant, never expose a checkpoint
            # unless that tenant owns its session projection.
            from kazma_core.tenant_context import get_current_tenant_id

            if get_current_tenant_id() is not None:
                from kazma_ui.session_manager import get_session_manager

                store = get_session_manager()
                pending = [
                    item
                    for item in pending
                    if store.get_by_thread_id(str(item["thread_id"])) is not None
                ]
            return JSONResponse({"pending": pending, "count": len(pending)})
        except Exception as exc:
            logger.exception("[HITL] Failed to list pending approvals")
            return JSONResponse(
                {"pending": [], "count": 0, "error": str(exc)},
                status_code=500,
            )

    @router.post("/api/pending-approvals/clear")
    @router.delete("/api/pending-approvals")
    async def clear_pending_endpoint(request: Request) -> JSONResponse:
        """Clear all pending approvals by deleting their interrupted checkpoints."""
        try:
            cleared = await clear_pending_approvals(graph, checkpointer)
            return JSONResponse({"status": "ok", "cleared": cleared})
        except Exception as exc:
            logger.exception("[HITL] Failed to clear pending approvals")
            return JSONResponse(
                {"status": "error", "error": str(exc)},
                status_code=500,
            )

    return router
