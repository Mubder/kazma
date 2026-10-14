"""Time Travel Replay API — snapshot browsing, restore, fork, and compare.

Provides routes for the Web UI's Time Travel page (the TUI's ``/replay`` and
``/fork`` use them too):

  GET  /api/replay/threads                          — threads that have snapshots
  GET  /api/replay/snapshots/{thread_id}            — a thread's steps, by turn
  GET  /api/replay/snapshots/{thread_id}/{snapshot_id} — one step's detail
  POST /api/replay/restore                          — restore a thread to a step
  POST /api/replay/fork                             — branch a step into a new thread
  POST /api/replay/compare                          — diff two steps
  DELETE /api/replay/threads/{thread_id}            — delete a thread's steps

A step is addressed by its snapshot id, never by an iteration number (Time
Travel audit TT-01: iterations restart every turn). Restore and fork go
through ``kazma_ui.thread_rewind``: a running turn or a pending approval
answers 409 (``turn_running``, ``approval_pending`` with the cards), an
unreadable snapshot store 503 (``store_unavailable``, never an empty list:
TT-11).

All routes are auto-auth-gated by the ``/api/`` default-deny policy, and every
route acts only on threads the current tenant owns (``require_thread_owned``).
The gates: ``tests/test_thread_route_policy.py`` (each route declares its
rule) and ``tests/test_thread_ownership_routes.py`` (walks this router's route
table as another tenant).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kazma_core.errors import safe_error

logger = logging.getLogger(__name__)

__all__ = ["create_replay_router", "group_steps"]


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


def group_steps(records: list[Any]) -> list[dict[str, Any]]:
    """A thread's steps as the page lists them: each turn once, in capture
    order, each step once inside it (``step`` 1..n). The listing used to show
    one card per iteration NUMBER, so every turn after the first hid the
    earlier turns' steps (TT-01)."""
    turns: dict[str, dict[str, Any]] = {}
    for rec in records:
        key = rec.turn_key or f"turn-{rec.turn_index}"
        turn = turns.get(key)
        if turn is None:
            turn = {"turn_key": key, "turn_index": rec.turn_index, "steps": []}
            turns[key] = turn
        step = rec.summary()
        step["step"] = len(turn["steps"]) + 1
        turn["steps"].append(step)
    return list(turns.values())


def _store_unavailable(exc: Exception) -> JSONResponse:
    logger.warning("[replay] snapshot store unavailable: %s", exc)
    return JSONResponse(
        {"error": "store_unavailable", "detail": "The snapshot store could not be read."},
        status_code=503,
    )


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
        graph:    Optional compiled LangGraph (needed for restore/fork). If
                  None, restore/fork return 503.
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

    async def _actor(request: Request) -> str:
        """Who asked, for the audit row: the web session's role and id."""
        try:
            from kazma_core.security.web_sessions import SESSION_COOKIE, get_session_payload

            sid = request.cookies.get(SESSION_COOKIE) or ""
            payload: dict[str, Any] = {}
            if sid:
                payload = await asyncio.to_thread(get_session_payload, sid) or {}
            role = str(payload.get("role") or "user")
            short = str(payload.get("session_id") or sid or "")[:8]
            return f"web:{role}:{short}" if short else "web:token"
        except Exception:  # noqa: BLE001 - the audit row still names the surface
            return "web:anon"

    # Every route here takes a thread, so every route checks it — list, read,
    # compare and delete as well as restore and fork. Only the last two used to
    # (audit 2026-09-22), and that check failed OPEN on a store error while
    # saying it mirrored the approval gate, which fails closed.
    from kazma_core.time_travel import SnapshotStoreError
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
        except SnapshotStoreError as exc:
            return _store_unavailable(exc)
        except Exception as exc:
            logger.exception("[replay] list threads failed")
            return JSONResponse({"threads": [], "count": 0, "error": safe_error(exc)}, status_code=500)

    @router.get("/api/replay/snapshots/{thread_id}")
    async def list_snapshots(thread_id: str) -> JSONResponse:
        """A thread's steps: ``snapshots`` (flat, capture order) and
        ``turns`` (each turn once, its steps numbered inside it). Message
        counts come from the row (counted when the step was saved): the
        listing decoded every state to count them (TT-06)."""
        rec = _recorder()
        if rec is None:
            return _unavailable()
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            records = await asyncio.to_thread(rec.list_snapshots, thread_id)
            turns = group_steps(records)
            flat = [step for turn in turns for step in turn["steps"]]
            return JSONResponse({"snapshots": flat, "turns": turns, "count": len(flat)})
        except SnapshotStoreError as exc:
            return _store_unavailable(exc)
        except Exception as exc:
            logger.exception("[replay] list snapshots failed for %s", thread_id)
            return JSONResponse({"snapshots": [], "count": 0, "error": safe_error(exc)}, status_code=500)

    @router.get("/api/replay/snapshots/{thread_id}/{snapshot_id}")
    async def get_snapshot(thread_id: str, snapshot_id: str) -> JSONResponse:
        """One step's detail (state + messages)."""
        rec = _recorder()
        if rec is None:
            return _unavailable()
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            record = await asyncio.to_thread(rec.get, snapshot_id, thread_id=thread_id)
            if record is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            state = await asyncio.to_thread(record.get_state)
            messages = state.get("messages", [])
            return JSONResponse({
                **record.summary(),
                "thread_id": thread_id,
                "messages": messages,
                "model": state.get("last_model", ""),
                "cost_usd": state.get("last_cost_usd", 0.0),
                "next_node": state.get("next_node", ""),
                "message_count": len(messages),
            })
        except SnapshotStoreError as exc:
            return _store_unavailable(exc)
        except Exception as exc:
            logger.exception("[replay] get snapshot failed: %s/%s", thread_id, snapshot_id)
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    def _ids(body: dict[str, Any]) -> tuple[str, str]:
        return str(body.get("thread_id") or ""), str(body.get("snapshot_id") or "")

    @router.post("/api/replay/restore")
    async def restore_snapshot(body: dict[str, Any], request: Request) -> JSONResponse:
        """Restore a thread to one of its steps.

        Body: ``{"thread_id": "...", "snapshot_id": "..."}``. 409 with
        ``turn_running`` or ``approval_pending`` (and the cards), 404 for a
        step that is not this thread's, 503 when the store cannot be read.
        """
        rec = _recorder()
        if rec is None:
            return _unavailable()
        live_graph = _graph()
        if live_graph is None:
            return JSONResponse({"error": "graph not available"}, status_code=503)
        thread_id, snapshot_id = _ids(body)
        if not thread_id or not snapshot_id:
            return JSONResponse({"error": "thread_id and snapshot_id required"}, status_code=400)
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        from kazma_ui.thread_rewind import RewindRefused, rewind_thread

        try:
            out = await rewind_thread(
                live_graph, thread_id, snapshot_id,
                actor=await _actor(request), surface="web", recorder=rec,
            )
            return JSONResponse(out)
        except RewindRefused as refused:
            return JSONResponse(refused.body(), status_code=refused.status)
        except Exception as exc:
            logger.exception("[replay] restore failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    @router.post("/api/replay/fork")
    async def fork_snapshot(body: dict[str, Any], request: Request) -> JSONResponse:
        """Fork from a step into a new thread (original stays intact).

        Body: ``{"thread_id": "...", "snapshot_id": "..."}``
        Returns: ``{"new_thread_id", "session_id", "title", "message_count", "chat_saved"}``
        """
        rec = _recorder()
        if rec is None:
            return _unavailable()
        live_graph = _graph()
        if live_graph is None:
            return JSONResponse({"error": "graph not available"}, status_code=503)
        thread_id, snapshot_id = _ids(body)
        if not thread_id or not snapshot_id:
            return JSONResponse({"error": "thread_id and snapshot_id required"}, status_code=400)
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        from kazma_ui.thread_rewind import RewindRefused, fork_thread

        try:
            out = await fork_thread(
                live_graph, thread_id, snapshot_id,
                actor=await _actor(request), surface="web", recorder=rec,
            )
            return JSONResponse(out)
        except RewindRefused as refused:
            return JSONResponse(refused.body(), status_code=refused.status)
        except Exception as exc:
            logger.exception("[replay] fork failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    @router.post("/api/replay/compare")
    async def compare_snapshots(body: dict[str, Any]) -> JSONResponse:
        """Compare two steps of the same thread.

        Body: ``{"thread_id": "...", "a": "<snapshot id>", "b": "<snapshot id>"}``
        """
        rec = _recorder()
        if rec is None:
            return _unavailable()
        thread_id = str(body.get("thread_id") or "")
        a = str(body.get("a") or "")
        b = str(body.get("b") or "")
        if not thread_id or not a or not b:
            return JSONResponse({"error": "thread_id, a, b required"}, status_code=400)
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        try:
            state_a = await asyncio.to_thread(_engine(rec).replay_from, thread_id, a)
            state_b = await asyncio.to_thread(_engine(rec).replay_from, thread_id, b)
            if state_a is None or state_b is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            diff = await asyncio.to_thread(_engine(rec).compare_replays, state_a, state_b)
            return JSONResponse({"diff": diff})
        except SnapshotStoreError as exc:
            return _store_unavailable(exc)
        except Exception as exc:
            logger.exception("[replay] compare failed")
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    @router.delete("/api/replay/threads/{thread_id}")
    async def clear_snapshots(thread_id: str, request: Request) -> JSONResponse:
        """Delete every step of a thread, under each of its keys."""
        rec = _recorder()
        if rec is None:
            return _unavailable()
        if (denied := await _require_thread_owned(thread_id)) is not None:
            return denied
        from kazma_ui.thread_rewind import clear_thread_snapshots

        try:
            count = await clear_thread_snapshots(
                thread_id, actor=await _actor(request), surface="web", recorder=rec,
            )
            return JSONResponse({"ok": True, "cleared": count})
        except SnapshotStoreError as exc:
            return _store_unavailable(exc)
        except Exception as exc:
            logger.exception("[replay] clear failed for %s", thread_id)
            return JSONResponse({"error": safe_error(exc)}, status_code=500)

    return router
