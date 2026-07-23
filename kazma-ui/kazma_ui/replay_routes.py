"""Time Travel Replay API — snapshot browsing, restore, fork, and compare.

Provides routes for the Web UI's Time Travel panel:

  GET  /api/replay/threads                      — threads that have snapshots
  GET  /api/replay/snapshots/{thread_id}        — list snapshots for a thread
  GET  /api/replay/snapshots/{thread_id}/{it}   — single snapshot detail
  POST /api/replay/restore                      — rewind a thread to a snapshot
  POST /api/replay/fork                         — branch into a new thread
  POST /api/replay/compare                      — diff two snapshots
  DELETE /api/replay/threads/{thread_id}        — clear snapshots for a thread

All routes are auto-auth-gated by the ``/api/`` default-deny policy.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["create_replay_router"]


def create_replay_router(
    recorder: Any,
    engine: Any,
    graph: Any = None,
) -> APIRouter:
    """Create the replay API router.

    Args:
        recorder: ``SnapshotRecorder`` instance (list/get/clear snapshots).
        engine:   ``ReplayEngine`` instance (replay_from, compare_replays).
        graph:    Optional compiled LangGraph (needed for restore/fork which
                  call ``aupdate_state``). If None, restore/fork return 503.

    Returns:
        ``APIRouter`` with the ``/api/replay/*`` endpoints.
    """
    router = APIRouter(tags=["replay"])

    @router.get("/api/replay/threads")
    async def list_threads() -> JSONResponse:
        """List distinct thread_ids that have at least one snapshot."""
        try:
            from kazma_core.config_store import get_config_store

            store = recorder._get_store(recorder._db_path) if hasattr(recorder, "_db_path") else None
            # The SnapshotStore tracks threads in the snapshots table; list
            # distinct thread_ids via a direct SQL query.
            if store is not None:
                rows = store._conn.execute(
                    "SELECT DISTINCT thread_id FROM snapshots ORDER BY thread_id"
                ).fetchall()
                threads = [r[0] for r in rows]
            else:
                threads = list({k[0] for k in recorder._memory})
            return JSONResponse({"threads": threads, "count": len(threads)})
        except Exception as exc:
            logger.exception("[replay] list threads failed")
            return JSONResponse({"threads": [], "count": 0, "error": str(exc)}, status_code=500)

    @router.get("/api/replay/snapshots/{thread_id}")
    async def list_snapshots(thread_id: str) -> JSONResponse:
        """List snapshots for a thread, ordered by iteration."""
        try:
            snaps = recorder.list_snapshots(thread_id)
            items = [
                {
                    "iteration": s.iteration,
                    "timestamp": s.timestamp,
                    "model": s.model_used or "",
                    "id": s.id,
                    "message_count": len(s.get_state().get("messages", [])),
                }
                for s in snaps
            ]
            return JSONResponse({"snapshots": items, "count": len(items)})
        except Exception as exc:
            logger.exception("[replay] list snapshots failed for %s", thread_id)
            return JSONResponse({"snapshots": [], "count": 0, "error": str(exc)}, status_code=500)

    @router.get("/api/replay/snapshots/{thread_id}/{iteration}")
    async def get_snapshot(thread_id: str, iteration: int) -> JSONResponse:
        """Get a single snapshot's detail (state + messages)."""
        try:
            state = engine.replay_from(thread_id, iteration)
            if state is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            messages = state.get("messages", [])
            return JSONResponse({
                "iteration": iteration,
                "thread_id": thread_id,
                "messages": messages,
                "model": state.get("last_model", ""),
                "cost_usd": state.get("last_cost_usd", 0.0),
                "next_node": state.get("next_node", ""),
                "message_count": len(messages),
            })
        except Exception as exc:
            logger.exception("[replay] get snapshot failed: %s/%d", thread_id, iteration)
            return JSONResponse({"error": str(exc)}, status_code=500)

    @router.post("/api/replay/restore")
    async def restore_snapshot(body: dict[str, Any]) -> JSONResponse:
        """Restore a snapshot in-place: rewind the thread to that iteration.

        Body: ``{"thread_id": "...", "iteration": N}``
        """
        if graph is None:
            return JSONResponse({"error": "graph not available"}, status_code=503)
        thread_id = body.get("thread_id", "")
        iteration = body.get("iteration")
        if not thread_id or iteration is None:
            return JSONResponse({"error": "thread_id and iteration required"}, status_code=400)
        try:
            state = engine.replay_from(thread_id, int(iteration))
            if state is None:
                return JSONResponse({"error": "snapshot not found"}, status_code=404)
            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            await graph.aupdate_state(config, {"messages": state.get("messages", [])})
            return JSONResponse({
                "ok": True,
                "thread_id": thread_id,
                "iteration": int(iteration),
                "message_count": len(state.get("messages", [])),
            })
        except Exception as exc:
            logger.exception("[replay] restore failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @router.post("/api/replay/fork")
    async def fork_snapshot(body: dict[str, Any]) -> JSONResponse:
        """Fork from a snapshot into a new thread (original stays intact).

        Body: ``{"thread_id": "...", "iteration": N}``
        Returns: ``{"new_thread_id": "...", "message_count": N}``
        """
        if graph is None:
            return JSONResponse({"error": "graph not available"}, status_code=503)
        import uuid

        thread_id = body.get("thread_id", "")
        iteration = body.get("iteration")
        if not thread_id or iteration is None:
            return JSONResponse({"error": "thread_id and iteration required"}, status_code=400)
        try:
            state = engine.replay_from(thread_id, int(iteration))
            if state is None:
                return JSONResponse({"error": "snapshot not found"}, status_code=404)
            new_thread_id = f"fork-{uuid.uuid4().hex[:12]}"
            state["thread_id"] = new_thread_id
            new_config = {"configurable": {"thread_id": new_thread_id, "checkpoint_ns": ""}}
            await graph.aupdate_state(new_config, {"messages": state.get("messages", [])})

            # Create a Web UI session for the fork.
            try:
                from kazma_ui.session_manager import get_session_manager, ChatSession

                web_store = get_session_manager()
                web_store.put(ChatSession(
                    session_id=new_thread_id,
                    thread_id=new_thread_id,
                    title=f"Fork (iter {iteration})",
                    messages=state.get("messages", []),
                ))
            except Exception:
                logger.debug("[replay] fork: could not create Web UI session", exc_info=True)

            return JSONResponse({
                "ok": True,
                "new_thread_id": new_thread_id,
                "message_count": len(state.get("messages", [])),
            })
        except Exception as exc:
            logger.exception("[replay] fork failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @router.post("/api/replay/compare")
    async def compare_snapshots(body: dict[str, Any]) -> JSONResponse:
        """Compare two snapshots from the same thread.

        Body: ``{"thread_id": "...", "a": N, "b": M}``
        """
        thread_id = body.get("thread_id", "")
        a = body.get("a")
        b = body.get("b")
        if not thread_id or a is None or b is None:
            return JSONResponse({"error": "thread_id, a, b required"}, status_code=400)
        try:
            state_a = engine.replay_from(thread_id, int(a))
            state_b = engine.replay_from(thread_id, int(b))
            if state_a is None or state_b is None:
                return JSONResponse({"error": "one or both snapshots not found"}, status_code=404)
            diff = engine.compare_replays(state_a, state_b)
            return JSONResponse({"diff": diff})
        except Exception as exc:
            logger.exception("[replay] compare failed")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @router.delete("/api/replay/threads/{thread_id}")
    async def clear_snapshots(thread_id: str) -> JSONResponse:
        """Clear all snapshots for a thread."""
        try:
            count = recorder.clear_snapshots(thread_id)
            return JSONResponse({"ok": True, "cleared": count})
        except Exception as exc:
            logger.exception("[replay] clear failed for %s", thread_id)
            return JSONResponse({"error": str(exc)}, status_code=500)

    return router
