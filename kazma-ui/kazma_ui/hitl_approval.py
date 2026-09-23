"""HITL (Human-in-the-Loop) approval helpers.

``_get_pending_approvals`` inspects the LangGraph checkpointer for threads
paused on an ``interrupt()`` and extracts the pending tool call, so a surface
can render Approve / Deny cards. The live ``GET /api/pending-approvals`` (and
its clear) are registered in ``kazma_ui/routes_direct/misc.py``; they use this
as the thin fallback when the gate registry is off.

This module used to carry a second copy of that route in a router factory no
app mounted — its tests exercised the copy production never ran. The factory
was removed on 2026-09-23 and its tests now drive the live route
(``tests/test_hitl_approval_ui.py``).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__: list[str] = []


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
        # LangGraph's own interrupt id is the ONLY id that matches what the
        # chat journal stamped for this pause. A minted hash with a per-call
        # seq bump changes on every poll — that mismatch drew ghost second
        # cards (recovered card ≠ journal card, 2026-09-01).
        iid = ""
        for attr in ("id", "ns"):
            v = getattr(intr, attr, None)
            if v:
                iid = str(v)
                break
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
                "interrupt_id": iid,
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
                "interrupt_id": iid,
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

        try:
            from kazma_ui.hitl_status import is_truly_pending

            if not await is_truly_pending(
                thread_id, graph=graph, snapshot=state
            ):
                continue
        except Exception:
            logger.debug(
                "[HITL] hitl pending check failed thread=%s", thread_id, exc_info=True
            )
            continue

        for task in getattr(state, "tasks", ()):
            info = _extract_interrupt_info(task)
            if info is not None:
                # Prefer LangGraph's own interrupt id (matches the journal
                # stamp). Fallback: a deterministic hash anchored on the
                # checkpoint id — stable across polls (seq=0, no counter).
                iid = str(info.pop("interrupt_id", "") or "")
                if not iid:
                    try:
                        from kazma_ui.turn_document import make_interrupt_id

                        ck = ""
                        try:
                            cfg = getattr(state, "config", None) or {}
                            ck = str(
                                (cfg.get("configurable") or {}).get("checkpoint_id")
                                or ""
                            )
                        except Exception:
                            ck = ""
                        iid = make_interrupt_id(
                            thread_id=thread_id,
                            tool=info["tool_name"],
                            args=info["arguments"],
                            checkpoint_id=ck,
                            seq=0,
                        )
                    except Exception:
                        iid = ""
                approvals.append(
                    {
                        "thread_id": thread_id,
                        "tool_name": info["tool_name"],
                        "arguments": info["arguments"],
                        "message": info["message"],
                        "yolo_allowed": info.get("yolo_allowed", True),
                        "interrupt_id": iid,
                    }
                )
                # Only need one interrupt per thread
                break

    return approvals
