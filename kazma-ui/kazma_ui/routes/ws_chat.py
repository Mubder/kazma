"""FastAPI WebSocket Gateway for Real-Time Chat Telemetry Bus.

Exposes `/ws/chat/{session_id}` as the Turn Delivery V2 telemetry / cursor bus:
live journal frames for every tab watching a chat, the cursor resume
(`?last_seq=` or a `resume` action), the pending HITL card on connect, and
`ping`. Turn control -- send, approve, stop, steer, abort -- is HTTP / SSE
only (`_HTTP_ROUTE_FOR`); the socket answers those actions with a refusal
naming the route. It carried a second copy of each until 2026-09-30 (AUD-026):
a graph client behind ``KAZMA_WS_GRAPH`` and stop/steer/abort twins that no
shipped client sent to.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from kazma_core.tracing.events import EventBridge, TelemetryEvent

from kazma_ui.active_turns import (
    clear_orphan_stamp,
    get_active_turn,
    is_turn_running,
)
from kazma_ui.delivery import get_turn_broker, is_replayable
from kazma_ui.session_manager import get_session_manager

logger = logging.getLogger(__name__)


def _ws_recursion_limit(thread_id: str | None = None) -> int:
    """Aligned with gateway / long-task budgets (not a hard-coded 100)."""
    try:
        from kazma_core.agent.long_task import resolve_turn_budgets

        return int(resolve_turn_budgets(thread_id)["recursion_limit"])
    except Exception:
        return 100


async def _ws_resume_handshake(socket: Any, thread_id: str, last_seq: int) -> None:
    """V2 cursor resume: structured handshake + journal replay to one socket.

    Replies with a single ``resumed`` frame describing what follows, then
    replays journaled events strictly after *last_seq* — direct to this
    socket (they are already journalled; re-emitting through the broker
    would double-append them). ``gap=True`` tells the client its cursor
    predates retention and it must snapshot-resync instead of replaying.
    Never raises to the caller for per-frame socket failures mid-replay —
    a dead socket aborts the replay silently.
    """
    broker = get_turn_broker()
    frames, gap, head = await asyncio.to_thread(broker.resume, thread_id, int(last_seq or 0))
    try:
        await socket.send_json(
            TelemetryEvent(
                type="resumed",
                data={
                    "from": int(last_seq or 0),
                    "to": head,
                    "count": len(frames),
                    "gap": bool(gap),
                    # Structured replacement for the old prose catch-up frame
                    # ("Reconnected — previous turn still running…") that the
                    # client used to regex-match.
                    "running": bool(is_turn_running(thread_id)),
                },
                thread_id=thread_id,
            ).to_dict()
        )
        for frame in frames:
            if is_replayable(frame):
                # Replay provenance (2026-09-03): a frame re-delivered from
                # the journal is HISTORY, not a live ask. Clients must not
                # paint live approval state from it — a settled approval's
                # retained ``approval_required`` frame used to flash a
                # ghost card on every refresh of any session that once had
                # an approval. The gate registry (via the load reconciler)
                # is the only authority for what is genuinely pending.
                try:
                    data = dict(frame.get("data") or {})
                    data["replay"] = True
                    stamped = dict(frame)
                    stamped["data"] = data
                    frame = stamped
                except Exception:
                    logger.debug(
                        "[WS-Chat] replay stamp skipped", exc_info=True
                    )
                await socket.send_json(frame)
    except Exception:
        logger.debug(
            "[WS-Chat] resume replay aborted (socket gone) thread=%s",
            thread_id[:12],
            exc_info=True,
        )


#: Turn control a client may still send on the socket, and where it lives.
_HTTP_ROUTE_FOR: dict[str, str] = {
    "send_prompt": "POST /api/chat/stream",
    "approve_tool": "POST /api/approve/{thread_id}",
    "stop": "POST /api/chat/stop",
    "steer": "POST /api/chat/steer",
    "abort": "POST /api/chat/abort",
}


def _http_only_refusal(
    action: str, payload: dict[str, Any], thread_id: str, session_id: str
) -> dict[str, Any]:
    """The frame that answers a turn-control action sent on the socket.

    ``send_prompt`` and ``approve_tool`` keep the exact refusals a client got
    while the second graph client was switched off (a ``prompt_ack`` with
    ``reason: sse_only``, an ``approval_error`` with ``code: SSE_ONLY``).
    """
    route = _HTTP_ROUTE_FOR[action]
    if action == "send_prompt":
        return TelemetryEvent(
            type="prompt_ack",
            data={
                "client_msg_id": str(payload.get("client_msg_id") or "").strip(),
                "accepted": False,
                "session_id": session_id,
                "reason": "sse_only",
                "message": (
                    f"Web chat turns use SSE ({route}). This socket is telemetry only."
                ),
            },
            thread_id=thread_id,
        ).to_dict()
    if action == "approve_tool":
        from kazma_ui.sse_utils import ApprovalEventBridge

        return ApprovalEventBridge.create_approval_error_event(
            thread_id,
            error=f"HITL resume uses {route}.",
            code="SSE_ONLY",
            scope=str(payload.get("scope") or "once"),
        )
    return TelemetryEvent(
        type="graph_error",
        data={
            "code": "http_only",
            "action": action,
            "message": f"{action} uses {route}; this socket is telemetry only.",
        },
        thread_id=thread_id,
    ).to_dict()


def _extract_hitl_payload(intr: Any) -> dict[str, Any] | None:
    """Normalize LangGraph interrupt objects into a hitl payload dict."""
    value = getattr(intr, "value", None)
    if value is None and isinstance(intr, dict):
        value = intr.get("value", intr)
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if not isinstance(value, dict):
        return None
    if value.get("type") == "hitl_approval":
        return value
    if "tool" in value or "args" in value or "tools" in value:
        return {
            "type": "hitl_approval",
            "tool": value.get("tool", "unknown"),
            "args": value.get("args", value.get("arguments", {})),
            "tools": value.get("tools") or [],
            "message": value.get("message", ""),
        }
    return None


def create_ws_chat_router(
    graph: Any = None,
    graph_holder: dict[str, Any] | None = None,
    graph_getter: Callable[[], Any] | None = None,
) -> APIRouter:
    """Factory to build the WebSocket chat gateway router.

    The graph is read (the HITL card on connect), never run for
    a new turn: turns are the SSE route's.
    """
    router = APIRouter(tags=["ws-chat"])

    def _get_graph() -> Any:
        if graph_getter:
            try:
                g = graph_getter()
                if g:
                    return g
            except Exception as exc:
                logger.debug("[WS-Chat] graph_getter failed: %s", exc)
        if graph_holder and graph_holder.get("graph"):
            return graph_holder.get("graph")
        return graph

    async def _scan_and_emit_hitl_interrupt(
        graph_inst: Any,
        config: dict[str, Any],
        websocket: WebSocket,
        thread_id: str,
    ) -> bool:
        """Scan graph snapshot for pending interrupts and send approval event if found."""
        try:
            from kazma_ui.hitl_status import is_truly_pending

            if not await is_truly_pending(thread_id, graph=graph_inst):
                return False
        except Exception:
            logger.debug("[WS-Chat] HITL status check failed closed", exc_info=True)
            return False
        try:
            snapshot = await graph_inst.aget_state(config)
            if snapshot is not None and getattr(snapshot, "next", None):
                for task in getattr(snapshot, "tasks", []) or []:
                    for intr in getattr(task, "interrupts", []) or []:
                        payload = _extract_hitl_payload(intr)
                        if payload:
                            iid = ""
                            try:
                                from kazma_ui.hitl_status import (
                                    persisted_hitl_for_thread,
                                )
                                from kazma_ui.turn_document import assign_interrupt_id

                                part = await asyncio.to_thread(persisted_hitl_for_thread, thread_id)
                                stored = ""
                                if isinstance(part, dict):
                                    stored = str(
                                        part.get("interrupt_id")
                                        or (part.get("payload") or {}).get(
                                            "interrupt_id"
                                        )
                                        or ""
                                    )
                                if stored:
                                    payload["interrupt_id"] = stored
                                    iid = stored
                                else:
                                    iid = assign_interrupt_id(
                                        payload,
                                        thread_id=thread_id,
                                        interrupt=intr,
                                    )
                            except Exception:
                                logger.debug(
                                    "[WS-Chat] interrupt_id stamp skipped",
                                    exc_info=True,
                                )
                            approval_ev = EventBridge.create_approval_event(
                                thread_id=thread_id,
                                tool_name=payload.get("tool", ""),
                                args=payload.get("args", {}),
                                message=payload.get("message", ""),
                                tools=payload.get("tools"),
                                kind=payload.get("kind"),
                                items=payload.get("items"),
                                interrupt_id=iid or None,
                                yolo_allowed=payload.get("yolo_allowed"),
                            )
                            await websocket.send_json(approval_ev.to_dict())
                            logger.info(
                                "[WS-Chat] HITL interrupt emitted over WS: thread=%s tool=%s",
                                thread_id,
                                payload.get("tool"),
                            )
                            return True
        except Exception as exc:
            logger.warning("[WS-Chat] Failed scanning graph snapshot for interrupts: %s", exc)
        return False

    @router.websocket("/ws/chat/{session_id}")
    async def chat_websocket(websocket: WebSocket, session_id: str) -> None:
        """WebSocket connection handler for session-bound agent telemetry."""
        from kazma_core.tenant_context import tenant_scope
        from kazma_core.tenant_isolation import principal_tenant_id
        from kazma_ui.auth import get_websocket_principal

        try:
            principal = await asyncio.to_thread(get_websocket_principal, websocket)
        except Exception:
            logger.warning("[WS-Chat] Authentication failed closed", exc_info=True)
            principal = None
        if principal is None:
            logger.warning("[WS-Chat] Unauthenticated connection attempt for session=%s", session_id)
            await websocket.accept()
            await websocket.close(code=4003, reason="Unauthorized")
            return

        with tenant_scope(principal_tenant_id(principal) or "default"):
            await _observe_session(websocket, session_id)

    async def _observe_session(websocket: WebSocket, session_id: str) -> None:
        """Observe only a pre-existing session inside the verified tenant."""
        from kazma_ui.thread_ownership import resolve_caller_thread

        # A blank shell has no checkpoint or journal to observe. The HTTP
        # creation/first-turn path assigns its thread; a socket never claims it.
        try:
            store = await asyncio.to_thread(get_session_manager)
            thread_id = await resolve_caller_thread(session_id, "", store=store)
            session = await asyncio.to_thread(store.get, session_id)
        except Exception:
            logger.warning("[WS-Chat] Session lookup failed closed", exc_info=True)
            session = None
            thread_id = ""
        if not thread_id or session is None or session.thread_id != thread_id:
            await websocket.accept()
            await websocket.close(code=4004, reason="Session unavailable")
            return

        await websocket.accept()
        logger.info("[WS-Chat] Client connected: session_id=%s", session_id)

        # A watching client is present: a turn whose SSE stream dropped must
        # not be reaped as abandoned while this tab follows it here.
        clear_orphan_stamp(thread_id)
        # LangGraph default recursion_limit is 25 — far too low for multi-tool
        # turns. Derive from long-task / agent.max_iterations (same as gateway).
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            },
            "recursion_limit": await asyncio.to_thread(_ws_recursion_limit, thread_id),
        }

        # Scan graph state on connection/reconnection for any pending HITL interrupts
        # so if the user reloaded or navigated away while waiting for HITL approval,
        # the UI immediately receives the HITL event upon connecting.
        try:
            graph_inst = _get_graph()
            if graph_inst:
                await _scan_and_emit_hitl_interrupt(graph_inst, config, websocket, thread_id)
        except Exception as init_scan_err:
            logger.debug("[WS-Chat] Initial HITL scan on connect failed: %s", init_scan_err)

        # V2 cursor resume opt-in: ?last_seq=N on the WS URL. A cursor-aware
        # client gets the structured handshake + journal replay instead of the
        # legacy prose catch-up below (which stays byte-identical for legacy
        # clients that connect without a cursor).
        try:
            _raw_ls = str(websocket.query_params.get("last_seq") or "").strip()
            _v2_last_seq: int | None = int(_raw_ls) if _raw_ls else None
        except Exception:
            _v2_last_seq = None

        if _v2_last_seq is not None:
            await _ws_resume_handshake(websocket, thread_id, _v2_last_seq)
        else:
            # Legacy reconnect catch-up: if a turn is still running, tell the client to keep
            # waiting; if the last assistant bubble already has content, push it.
            # NOTE: never re-import active_turns names inside this scope — a
            # function-local import shadows the module-level binding for the
            # ENTIRE handler, and V2 connections skip this branch, so later
            # reads (stop, steer, abort) would raise UnboundLocalError
            # (2026-08-24 "could not deliver after several retries" outage).
            try:
                _alive = get_active_turn(thread_id)
                if _alive is not None and not _alive.done():
                    await websocket.send_json(
                        TelemetryEvent(
                            type="status_update",
                            data={
                                "status": "thinking",
                                "message": "Reconnected — previous turn still running…",
                                "active_node": "Supervisor",
                            },
                            thread_id=thread_id,
                        ).to_dict()
                    )
                else:
                    try:
                        sess = await asyncio.to_thread(store.get, session_id)
                        if sess and sess.messages:
                            last = sess.messages[-1]
                            if (
                                last.get("role") == "assistant"
                                and (last.get("content") or "").strip()
                                and not last.get("pending")
                                # Slash confirmations are not a turn answer —
                                # replaying them into the next prompt duplicated
                                # "MISSION ON" (incident 2026-08-16).
                                and last.get("kind") != "capacity"
                            ):
                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="turn_complete",
                                        data={
                                            "content": last.get("content") or "",
                                            "interrupted": False,
                                            "empty": False,
                                            "model": last.get("model") or "",
                                            "session_id": session_id,
                                            "replay": True,
                                            # Cumulative badges survive tab switches.
                                            "session_tokens": int(sess.total_tokens or 0),
                                            "session_cost": round(float(sess.total_cost or 0.0), 6),
                                        },
                                        thread_id=thread_id,
                                    ).to_dict()
                                )
                    except Exception:
                        logger.debug("[WS-Chat] reconnect message catch-up skipped", exc_info=True)
            except Exception as reconnect_exc:
                logger.debug("[WS-Chat] reconnect catch-up failed: %s", reconnect_exc)

        # Turn Delivery V2: register with the broker — multi-slot (N tabs may
        # watch one session) and every thread-scoped send fans out to all of
        # them. Registered flush against the try below so no pre-loop
        # exception can leak a dead socket into the registry; conn_id is
        # unregistered in the finally.
        _broker_conn_id = get_turn_broker().register_socket(thread_id, websocket)

        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    payload = json.loads(data_text)
                    if not isinstance(payload, dict):
                        # `123` or `[]` parse, and `.get` on them used to end
                        # the connection with a logged traceback.
                        raise ValueError("not a JSON object")
                except ValueError:
                    await websocket.send_json(
                        TelemetryEvent(
                            type="graph_error",
                            data={"message": "Invalid JSON payload format"},
                            thread_id=thread_id,
                        ).to_dict()
                    )
                    continue

                action = payload.get("action")

                if action == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                # ── Action: resume (Turn Delivery V2) ────────────────────
                # Mid-connection re-resume / late opt-in: same structured
                # handshake + journal replay as the ?last_seq= connect form.
                if action == "resume":
                    try:
                        _resume_from = int(payload.get("last_seq") or 0)
                    except Exception:
                        _resume_from = 0
                    await _ws_resume_handshake(websocket, thread_id, _resume_from)
                    continue

                # ── Turn control is HTTP / SSE only ───────────────────────
                # The chat page sends, approves, stops, steers and aborts over
                # HTTP (`_HTTP_ROUTE_FOR`); this socket carries the journal's
                # frames and the cursor. Until 2026-09-30 it also ran a second
                # copy of each -- a graph client behind KAZMA_WS_GRAPH, and
                # stop/steer/abort twins -- that no shipped client sent to,
                # while every delivery fix had to land twice (AUD-026).
                if action in _HTTP_ROUTE_FOR:
                    await websocket.send_json(
                        _http_only_refusal(action, payload, thread_id, session_id)
                    )
                    continue
        except WebSocketDisconnect:
            logger.info("[WS-Chat] Client disconnected: session_id=%s", session_id)
        except Exception as exc:
            logger.exception("[WS-Chat] WebSocket error: %s", exc)
        finally:
            # V2: drop THIS connection's broker registration; a turn it was
            # watching runs on regardless (it belongs to the SSE route).
            try:
                if _broker_conn_id:
                    get_turn_broker().unregister_socket(thread_id, _broker_conn_id)
            except Exception:
                logger.debug(
                    "[WS-Chat] broker unregister failed session=%s",
                    session_id[:12] if session_id else "?",
                    exc_info=True,
                )

    return router


# Default singleton instance for simple router registration
ws_chat_router = create_ws_chat_router()
