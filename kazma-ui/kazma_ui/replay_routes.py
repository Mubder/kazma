"""Time Travel Replay API — snapshot browsing, restore, fork, and compare.

Provides routes for the Web UI's Time Travel panel:

  GET  /api/replay/threads                      — threads that have snapshots
  GET  /api/replay/snapshots/{thread_id}        — list snapshots for a thread
  GET  /api/replay/snapshots/{thread_id}/{it}   — single snapshot detail
  POST /api/replay/restore                      — rewind a thread to a snapshot
  POST /api/replay/fork                         — branch into a new thread
  POST /api/replay/compare                      — diff two snapshots
  DELETE /api/replay/threads/{thread_id}        — clear snapshots for a thread

All routes are auto-auth-gated by the ``/api/`` default-deny policy, and every
route acts only on threads the current tenant owns (``_require_thread_owned``).
``tests/test_replay_route_ownership.py`` walks this router's route table, so a
new route that forgets the check fails there.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from kazma_core.errors import safe_error

logger = logging.getLogger(__name__)

__all__ = ["create_replay_router"]


def _thread_items(threads: list[str], chats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per thread, named by its chat, newest activity first."""
    rows = []
    for thread_id in threads:
        chat = chats.get(thread_id) or {}
        rows.append(
            {
                "thread_id": thread_id,
                "title": str(chat.get("title") or ""),
                "platform": str(chat.get("platform") or ""),
                "updated_at": str(chat.get("updated_at") or ""),
                "archived": bool(chat.get("archived")),
            }
        )
    # ISO timestamps order as text; a thread with no chat has none and goes last.
    rows.sort(key=lambda r: (r["updated_at"] != "", r["updated_at"]), reverse=True)
    return rows


def create_replay_router(
    recorder: Any = None,
    engine: Any = None,
    graph: Any = None,
    *,
    recorder_getter: Callable[[], Any] | None = None,
    graph_getter: Callable[[], Any] | None = None,
) -> APIRouter:
    """Create the replay API router.

    Args:
        recorder: ``SnapshotRecorder`` instance (list/get/clear snapshots).
        engine:   ``ReplayEngine`` instance (replay_from, compare_replays);
                  built from the recorder when not given.
        graph:    Optional compiled LangGraph (needed for restore/fork which
                  call ``aupdate_state``). If None, restore/fork return 503.
        recorder_getter / graph_getter: read on every request instead. The
                  app mounts this router when it is built, before startup
                  makes the recorder and the graph; and a model switch
                  rebuilds the graph, which a router given one at mount time
                  never saw.

    Returns:
        ``APIRouter`` with the ``/api/replay/*`` endpoints.
    """
    router = APIRouter(tags=["replay"])

    def _recorder() -> Any:
        return recorder_getter() if recorder_getter is not None else recorder

    def _engine(rec: Any) -> Any:
        if engine is not None and recorder_getter is None:
            return engine
        from kazma_core.time_travel import ReplayEngine

        return ReplayEngine(rec)

    def _graph() -> Any:
        return graph_getter() if graph_getter is not None else graph

    def _unavailable() -> JSONResponse:
        return JSONResponse(
            {
                "threads": [],
                "count": 0,
                "error": "time_travel_unavailable",
                "detail": (
                    "Snapshot recorder not initialized — check server startup log "
                    "for '[Replay] snapshot recorder creation failed'."
                ),
            },
            status_code=503,
        )

    # Every route here takes a thread, so every route checks it — list, read,
    # compare and delete as well as restore and fork. Only the last two used to
    # (audit 2026-09-22), and that check failed OPEN on a store error while
    # saying it mirrored the approval gate, which fails closed.
    from kazma_ui.thread_ownership import (
        chats_by_thread,
        owned_threads_async,
        require_thread_owned,
    )

    _require_thread_owned = require_thread_owned

    @router.get("/api/replay/threads")
    async def list_threads() -> JSONResponse:
        """The caller's threads that have snapshots, each named by its chat.

        ``threads`` is the id list (the picker's value, the ownership test's
        subject); ``items`` names each: the chat's title and platform from
        the chat store, newest activity first, threads no chat owns last.
        The page listed 131 bare uuids on the live install (2026-09-28).
        """
        rec = _recorder()
        if rec is None:
            return _unavailable()
        try:
            threads = await owned_threads_async(
                await asyncio.to_thread(rec.list_distinct_threads)
            )
            if threads is None:
                return JSONResponse(
                    {"threads": [], "count": 0, "error": "ownership check failed"},
                    status_code=403,
                )
            chats = await asyncio.to_thread(chats_by_thread)
            items = _thread_items(threads, chats)
            return JSONResponse({"threads": threads, "items": items, "count": len(threads)})
        except Exception as exc:
            logger.exception("[replay] list threads failed")
            return JSONResponse({"threads": [], "count": 0, "error": safe_error(exc)}, status_code=500)

    @router.get("/api/replay/snapshots/{thread_id}")
    async def list_snapshots(thread_id: str) -> JSONResponse:
        """List snapshots for a thread, ordered by iteration."""
        rec = _recorder()
        if rec is None:
            return _unavailable()
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            def _items() -> list[dict[str, Any]]:
                # Decoding every snapshot's state to count its messages is the
                # expensive part; none of it may run on the event loop.
                return [
                    {
                        "iteration": s.iteration,
                        "timestamp": s.timestamp,
                        "model": s.model_used or "",
                        "id": s.id,
                        "message_count": len(s.get_state().get("messages", [])),
                    }
                    for s in rec.list_snapshots(thread_id)
                ]

            items = await asyncio.to_thread(_items)
            return JSONResponse({"snapshots": items, "count": len(items)})
        except Exception as exc:
            logger.exception("[replay] list snapshots failed for %s", thread_id)
            return JSONResponse({"snapshots": [], "count": 0, "error": safe_error(exc)}, status_code=500)

    @router.get("/api/replay/snapshots/{thread_id}/{iteration}")
    async def get_snapshot(thread_id: str, iteration: int) -> JSONResponse:
        """Get a single snapshot's detail (state + messages)."""
        rec = _recorder()
        if rec is None:
            return _unavailable()
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            state = await asyncio.to_thread(_engine(rec).replay_from, thread_id, iteration)
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
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    @router.post("/api/replay/restore")
    async def restore_snapshot(body: dict[str, Any]) -> JSONResponse:
        """Restore a snapshot in-place: rewind the thread to that iteration.

        Body: ``{"thread_id": "...", "iteration": N}``
        """
        rec = _recorder()
        if rec is None:
            return _unavailable()
        live_graph = _graph()
        if live_graph is None:
            return JSONResponse({"error": "graph not available"}, status_code=503)
        thread_id = body.get("thread_id", "")
        iteration = body.get("iteration")
        if not thread_id or iteration is None:
            return JSONResponse({"error": "thread_id and iteration required"}, status_code=400)
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            state = await asyncio.to_thread(_engine(rec).replay_from, thread_id, int(iteration))
            if state is None:
                return JSONResponse({"error": "snapshot not found"}, status_code=404)
            from kazma_core.time_travel import apply_snapshot_to_thread

            payload = await apply_snapshot_to_thread(live_graph, state, thread_id)
            return JSONResponse({
                "ok": True,
                "thread_id": thread_id,
                "iteration": int(iteration),
                "message_count": len(payload.get("messages") or []),
            })
        except Exception as exc:
            logger.exception("[replay] restore failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    @router.post("/api/replay/fork")
    async def fork_snapshot(body: dict[str, Any]) -> JSONResponse:
        """Fork from a snapshot into a new thread (original stays intact).

        Body: ``{"thread_id": "...", "iteration": N}``
        Returns: ``{"new_thread_id": "...", "message_count": N}``
        """
        rec = _recorder()
        if rec is None:
            return _unavailable()
        live_graph = _graph()
        if live_graph is None:
            return JSONResponse({"error": "graph not available"}, status_code=503)
        import uuid

        thread_id = body.get("thread_id", "")
        iteration = body.get("iteration")
        if not thread_id or iteration is None:
            return JSONResponse({"error": "thread_id and iteration required"}, status_code=400)
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            state = await asyncio.to_thread(_engine(rec).replay_from, thread_id, int(iteration))
            if state is None:
                return JSONResponse({"error": "snapshot not found"}, status_code=404)
            new_thread_id = f"fork-{uuid.uuid4().hex[:12]}"
            from kazma_core.time_travel import apply_snapshot_to_thread

            state = await apply_snapshot_to_thread(live_graph, state, new_thread_id)

            # A chat for the fork, so it shows in the sidebar. The chat store
            # is a database write: off the loop (AGENTS §35).
            def _save_fork_chat() -> None:
                from kazma_ui.session_manager import ChatSession, get_session_manager

                get_session_manager().put(ChatSession(
                    session_id=new_thread_id,
                    thread_id=new_thread_id,
                    title=f"Fork (iter {iteration})",
                    messages=state.get("messages", []),
                ))

            try:
                await asyncio.to_thread(_save_fork_chat)
            except Exception:
                logger.warning(
                    "[replay] fork %s made, but its chat could not be saved: it will "
                    "not show in the chat list", new_thread_id, exc_info=True,
                )

            return JSONResponse({
                "ok": True,
                "new_thread_id": new_thread_id,
                "message_count": len(state.get("messages", [])),
            })
        except Exception as exc:
            logger.exception("[replay] fork failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    @router.post("/api/replay/compare")
    async def compare_snapshots(body: dict[str, Any]) -> JSONResponse:
        """Compare two snapshots from the same thread.

        Body: ``{"thread_id": "...", "a": N, "b": M}``
        """
        rec = _recorder()
        if rec is None:
            return _unavailable()
        thread_id = body.get("thread_id", "")
        a = body.get("a")
        b = body.get("b")
        if not thread_id or a is None or b is None:
            return JSONResponse({"error": "thread_id, a, b required"}, status_code=400)
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            state_a = await asyncio.to_thread(_engine(rec).replay_from, thread_id, int(a))
            state_b = await asyncio.to_thread(_engine(rec).replay_from, thread_id, int(b))
            if state_a is None or state_b is None:
                return JSONResponse({"error": "one or both snapshots not found"}, status_code=404)
            diff = await asyncio.to_thread(_engine(rec).compare_replays, state_a, state_b)
            return JSONResponse({"diff": diff})
        except Exception as exc:
            logger.exception("[replay] compare failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    @router.delete("/api/replay/threads/{thread_id}")
    async def clear_snapshots(thread_id: str) -> JSONResponse:
        """Clear all snapshots for a thread."""
        rec = _recorder()
        if rec is None:
            return _unavailable()
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            count = await asyncio.to_thread(rec.clear_snapshots, thread_id)
            return JSONResponse({"ok": True, "cleared": count})
        except Exception as exc:
            logger.exception("[replay] clear failed for %s", thread_id)
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    return router
