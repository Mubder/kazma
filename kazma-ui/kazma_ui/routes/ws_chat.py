"""FastAPI WebSocket Gateway for Real-Time Chat Telemetry Bus.

Exposes `/ws/chat/{session_id}` to stream standardized JSON event frames from
LangGraph's `astream_events` directly into client stores (Alpine.js agentStore).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import traceback
import uuid
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.types import Command
from starlette.websockets import WebSocketState

from kazma_core.tracing.events import EventBridge, TelemetryEvent
from kazma_ui.active_turns import (
    bind_live_socket,
    cancel_turn,
    get_active_turn,
    get_live_socket,
    mark_turn_orphaned,
    reap_stale_turn,
    register_turn,
    unbind_live_socket,
    unregister_turn,
)
from kazma_ui.session_manager import get_session_manager

logger = logging.getLogger(__name__)


def ti_(key: str, fallback: str, **kwargs) -> str:
    """Translate a WS chat status string. Falls back to English if no translation."""
    try:
        from kazma_ui.i18n import TRANSLATIONS
        lang = "ar"  # WS chat is always Arabic UI (Kazma default)
        entry = TRANSLATIONS.get(key, {})
        text = entry.get(lang) or entry.get("en") or fallback
    except Exception:
        text = fallback
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


ws_chat_router = APIRouter(tags=["ws-chat"])

def _ws_recursion_limit(thread_id: str | None = None) -> int:
    """Aligned with gateway / long-task budgets (not a hard-coded 100)."""
    try:
        from kazma_core.agent.long_task import resolve_turn_budgets

        return int(resolve_turn_budgets(thread_id)["recursion_limit"])
    except Exception:
        return 100


def _make_ws_sender(
    websocket: WebSocket,
    thread_id: str = "",
) -> tuple[Callable[[dict[str, Any]], Any], Callable[[], bool]]:
    """Return (send, is_lost) around live WebSocket delivery that never raises.

    After the client disconnects (page refresh / tab switch) every send used to
    raise and abort the LangGraph ``astream_events`` generator mid-flight —
    the classic "agent stops after switching tabs" bug.  ``send`` now:

    1. Prefer the **live** socket for *thread_id* (rebound on reconnect via
       ``bind_live_socket``) so ``turn_complete`` reaches the new tab.
    2. Fall back to the socket that started the turn.
    3. Return False on loss — callers keep draining the graph and persist.
    """

    def _target() -> WebSocket:
        if thread_id:
            live = get_live_socket(thread_id)
            if live is not None:
                return live  # type: ignore[return-value]
        return websocket

    def is_lost() -> bool:
        ws = _target()
        try:
            return ws.client_state != WebSocketState.CONNECTED
        except Exception:
            return True

    async def send(payload: dict[str, Any]) -> bool:
        ws = _target()
        try:
            if ws.client_state != WebSocketState.CONNECTED:
                return False
            await ws.send_json(payload)
            return True
        except Exception:
            return False

    return send, is_lost


def _record_ws_activity(
    activity_log: list[dict[str, Any]],
    ev: TelemetryEvent,
    *,
    thought_recorded: list[bool],
) -> None:
    """Map a TelemetryEvent onto a compact workbench row (persisted CoT log).

    Rows mirror the shape chat.js ``logProgress`` consumes so the restored
    panel renders identically to the live one.  Thinking heartbeats are
    recorded once per turn (noisy as N rows on reload).
    """
    try:
        if ev.type == "tool_lifecycle":
            data = ev.data or {}
            status = str(data.get("status") or "tool_running")
            state = "done" if status == "tool_completed" else (
                "failed" if status == "tool_failed" else "running"
            )
            detail = str(
                data.get("result")
                or data.get("error")
                or data.get("inputs")
                or ""
            )
            activity_log.append({
                "kind": "tool",
                "title": str(data.get("tool_name") or "tool"),
                "detail": detail[:1000],
                "state": state,
            })
        elif ev.type == "status_update":
            status = str((ev.data or {}).get("status") or "").strip()
            if status == "thinking" and not thought_recorded[0]:
                thought_recorded[0] = True
                activity_log.append({
                    "kind": "status",
                    "title": "thinking",
                    "state": "running",
                })
            elif status in ("routing_node",):
                activity_log.append({
                    "kind": "status",
                    "title": status,
                    "state": "running",
                })
            elif status == "paused_for_approval":
                activity_log.append({
                    "kind": "status",
                    "title": "Waiting for approval",
                    "detail": str((ev.data or {}).get("tool") or ""),
                    "state": "info",
                })
    except Exception:
        logger.debug("[WS-Chat] activity capture failed", exc_info=True)


def _friendly_graph_error(exc: BaseException) -> str:
    """Human-readable error for UI (don't dump raw LangGraph exception walls)."""
    name = type(exc).__name__
    msg = str(exc) or name
    if "Recursion limit" in msg or "GRAPH_RECURSION" in msg or name == "GraphRecursionError":
        return (
            "This turn used too many graph steps (tool/supervisor hops) and hit "
            "LangGraph's recursion ceiling. Try a smaller ask, or continue in a "
            "new message — long smoke tests should be split into sections."
        )
    # Keep short; full traceback stays in server logs
    if len(msg) > 500:
        msg = msg[:500] + "…"
    return msg


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
    agent_getter: Callable[[], Any] | None = None,
    cost_breaker_getter: Callable[[], Any] | None = None,
) -> APIRouter:
    """Factory to build the WebSocket chat gateway router."""
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

    def _get_cost_breaker() -> Any:
        if cost_breaker_getter:
            try:
                cb = cost_breaker_getter()
                if cb:
                    return cb
            except Exception:
                pass
        if agent_getter:
            try:
                a = agent_getter()
                if a and hasattr(a, "cost_breaker"):
                    return a.cost_breaker
            except Exception:
                pass
        if graph_holder and graph_holder.get("agent"):
            a = graph_holder.get("agent")
            if hasattr(a, "cost_breaker"):
                return a.cost_breaker
        return None

    def _get_session_and_thread(
        session_id: str,
        *,
        durable: bool = False,
    ) -> tuple[Any, str]:
        """Resolve session + thread for this socket.

        *durable=False* (WS connect default): create an in-memory shell only —
        do **not** write an empty "Web Session · 0 msgs" row to the DB. The
        first accepted ``send_prompt`` calls ``put()`` and makes it durable.
        Gateway threads (``gw-*``) still force thread_id alignment and persist
        when needed.
        """
        store = get_session_manager()
        session = store.get(session_id)
        if session is None:
            session = store.get_or_create(session_id, durable=durable)
        if session_id.startswith("gw-"):
            if session.thread_id != session_id:
                session.thread_id = session_id
                store.put(session)
        elif not session.thread_id:
            session.thread_id = str(uuid.uuid4())
            # Only durable-write when the session already has content (or the
            # caller asked for durable). Empty shells stay memory-only.
            if durable or session.messages:
                store.put(session)
        return session, session.thread_id

    async def _scan_and_emit_hitl_interrupt(
        graph_inst: Any,
        config: dict[str, Any],
        websocket: WebSocket,
        thread_id: str,
    ) -> bool:
        """Scan graph snapshot for pending interrupts and send approval event if found."""
        try:
            snapshot = await graph_inst.aget_state(config)
            if snapshot is not None and getattr(snapshot, "next", None):
                for task in getattr(snapshot, "tasks", []) or []:
                    for intr in getattr(task, "interrupts", []) or []:
                        payload = _extract_hitl_payload(intr)
                        if payload:
                            approval_ev = EventBridge.create_approval_event(
                                thread_id=thread_id,
                                tool_name=payload.get("tool", ""),
                                args=payload.get("args", {}),
                                message=payload.get("message", ""),
                                tools=payload.get("tools"),
                                kind=payload.get("kind"),
                                items=payload.get("items"),
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

    async def _extract_new_assistant_text(
        graph_inst: Any,
        config: dict[str, Any],
        pre_msg_count: int = 0,
    ) -> str:
        """Return the latest *new* assistant text from graph state (after *pre_msg_count*)."""
        try:
            snapshot = await graph_inst.aget_state(config)
            if snapshot is None:
                return ""
            vals = getattr(snapshot, "values", None) or {}
            msgs = vals.get("messages") if isinstance(vals, dict) else None
            if not msgs or not isinstance(msgs, list):
                return ""
            new_msgs = msgs[pre_msg_count:] if pre_msg_count < len(msgs) else []
            for m in reversed(new_msgs):
                role = None
                if isinstance(m, dict):
                    role = m.get("role")
                else:
                    role = getattr(m, "type", None) or getattr(m, "role", None)
                if role in ("assistant", "ai"):
                    content = (
                        m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                    )
                    if isinstance(content, str) and content.strip():
                        return content
            return ""
        except Exception as exc:
            logger.debug("[WS-Chat] extract assistant text failed: %s", exc)
            return ""

    async def _backfill_assistant_text_if_needed(
        graph_inst: Any,
        config: dict[str, Any],
        websocket: WebSocket,
        thread_id: str,
        pre_msg_count: int = 0,
        *,
        emit_delta: bool = True,
        already_streamed: bool = False,
    ) -> str:
        """Backfill NEW assistant text from graph state after a non-streaming LLM turn.

        Returns the extracted text (may be empty). When *emit_delta* is True and
        tokens were not already streamed (*already_streamed*), sends a single
        ``llm_delta`` so the live UI paints something before ``turn_complete``.

        Industry note: full-answer backfill must NOT re-append after partial
        tokens — that doubled the reply on screen. Prefer ``turn_complete``
        (replace semantics) as the authoritative paint; *emit_delta* is only
        a progressive hint when nothing was streamed yet.
        """
        text = await _extract_new_assistant_text(graph_inst, config, pre_msg_count)
        if not text:
            return ""
        if not emit_delta or already_streamed:
            return text
        try:
            if websocket.client_state != WebSocketState.CONNECTED:
                return text
            await websocket.send_json(
                TelemetryEvent(
                    type="llm_delta",
                    data={"content": text, "full": True},
                    thread_id=thread_id,
                ).to_dict()
            )
        except Exception:
            # Client gone (refresh/tab switch) — nothing to notify; persist still wins.
            pass
        return text

    def _ensure_pending_assistant_bubble(
        session_id: str,
        *,
        force: bool = False,
    ) -> None:
        """Ensure a ``pending`` assistant bubble exists for the in-flight turn.

        The bubble is the "turn in progress" marker (empty content + pending).
        A reload then renders a processing indicator instead of a blank gap,
        and the frontend poller keeps watching until the final persist pops
        ``pending``. Mirrors the SSE path which sets the same flag.

        *force=True* marks the trailing assistant row pending even when it
        already has pre-HITL partial content — used on YOLO/approve resume so
        a mid-resume refresh still arms the background poller.
        """
        try:
            with get_session_manager().transact(session_id) as sess:
                last = sess.messages[-1] if sess.messages else None
                if last and last.get("role") == "assistant":
                    # Empty trailing bubble, or force during HITL resume.
                    if force or not (last.get("content") or "").strip():
                        last["pending"] = True
                else:
                    from datetime import UTC, datetime

                    sess.messages.append(
                        {
                            "role": "assistant",
                            "content": "",
                            "pending": True,
                            "ts": datetime.now(UTC).isoformat(),
                        }
                    )
        except Exception as exc:
            logger.warning("[WS-Chat] Failed ensuring pending bubble: %s", exc)

    def _resolve_active_model() -> str:
        try:
            from kazma_core.model_registry import get_model_registry

            return str(get_model_registry().get_active_profile().get("model") or "")
        except Exception:
            return ""

    async def _persist_final_assistant_message(
        graph_inst: Any,
        config: dict[str, Any],
        session_id: str,
        *,
        pre_msg_count: int = 0,
        prefer_text: str = "",
        activity: list[dict[str, Any]] | None = None,
        model: str | None = None,
    ) -> str:
        """Persist the latest *new* assistant text to SessionStore.

        Returns the text that was (or should have been) persisted so callers
        can also emit it over the wire. Prefer *prefer_text* when the stream
        already accumulated tokens; otherwise pull NEW messages after
        *pre_msg_count* from the checkpoint (avoids re-appending older turns).
        When *activity* (the CoT / workbench log for this turn) is given it is
        stored on the assistant message so a later reload restores it.
        *model* stamps which LLM produced the reply.
        """
        text = (prefer_text or "").strip()
        model_id = (model or "").strip() or _resolve_active_model()
        try:
            if not text:
                snapshot = await graph_inst.aget_state(config)
                if snapshot is not None:
                    vals = getattr(snapshot, "values", None) or {}
                    msgs = vals.get("messages") if isinstance(vals, dict) else None
                    if msgs and isinstance(msgs, list):
                        new_msgs = msgs[pre_msg_count:] if pre_msg_count < len(msgs) else msgs
                        for m in reversed(new_msgs):
                            role = None
                            if isinstance(m, dict):
                                role = m.get("role")
                            else:
                                role = getattr(m, "type", None) or getattr(m, "role", None)
                            if role in ("assistant", "ai"):
                                content = (
                                    m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                                )
                                if isinstance(content, str) and content.strip():
                                    text = content.strip()
                                    break

                        # Fallback: if no text response was produced, check if tools were executed
                        if not text and new_msgs:
                            tools_run = [
                                m.get("name") or getattr(m, "name", "tool")
                                for m in new_msgs
                                if (isinstance(m, dict) and m.get("role") in ("tool", "function"))
                                or (getattr(m, "type", None) in ("tool", "function"))
                            ]
                            if tools_run:
                                _tools = ', '.join(set(tools_run))
                                text = ti_("ws.tools_completed", f"Completed: {_tools}", tools=_tools)
                            elif activity:
                                text = ti_("ws.task_processing", "Task processing completed.")

            if not text:
                text = ti_("ws.task_completed", "Task completed.")

            # T4: mutate + persist under the per-session mutation lock so this
            # never interleaves with the SSE live/detached persist paths on the
            # same ChatSession.
            with get_session_manager().transact(session_id) as sess:
                # Upsert trailing assistant bubble so incremental flushes don't
                # create duplicate rows for the same turn.
                activity_rows = list(activity) if activity else None
                if sess.messages and sess.messages[-1].get("role") == "assistant":
                    # Idempotent upsert (re-persist after reconnect / double complete).
                    sess.messages[-1]["content"] = text
                    sess.messages[-1].pop("pending", None)
                    if activity_rows:
                        sess.messages[-1]["activity"] = activity_rows
                    if model_id:
                        sess.messages[-1]["model"] = model_id
                else:
                    from datetime import UTC, datetime

                    msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": text,
                        "ts": datetime.now(UTC).isoformat(),
                    }
                    if activity_rows:
                        msg["activity"] = activity_rows
                    if model_id:
                        msg["model"] = model_id
                    sess.messages.append(msg)

                # Collapse accidental identical consecutive assistant rows
                # (legacy double-persist left duplicates visible on refresh).
                while (
                    len(sess.messages) >= 2
                    and sess.messages[-1].get("role") == "assistant"
                    and sess.messages[-2].get("role") == "assistant"
                    and (sess.messages[-1].get("content") or "").strip()
                    and (sess.messages[-1].get("content") or "").strip()
                    == (sess.messages[-2].get("content") or "").strip()
                ):
                    sess.messages.pop()
            return text
        except Exception as exc:
            logger.warning("[WS-Chat] Failed persisting assistant message: %s", exc)
            return text

    def _extract_pending_tools_from_snapshot(snap: Any) -> tuple[str, list[str]]:
        """Return (primary_tool_name, tool_names) from a paused HITL snapshot."""
        primary = ""
        names: list[str] = []
        if not snap or not getattr(snap, "tasks", None):
            return primary, names
        for task in snap.tasks or []:
            for intr in getattr(task, "interrupts", None) or []:
                payload = _extract_hitl_payload(intr)
                if not payload:
                    continue
                primary = str(payload.get("tool") or "")
                for t in payload.get("tools") or []:
                    if isinstance(t, dict) and t.get("name"):
                        names.append(str(t["name"]))
                    elif isinstance(t, str) and t:
                        names.append(t)
                if primary and " tools" not in primary and primary not in names:
                    names.insert(0, primary)
                return primary, list(dict.fromkeys(names))
        return primary, names

    @router.websocket("/ws/chat/{session_id}")
    async def chat_websocket(websocket: WebSocket, session_id: str) -> None:
        """WebSocket connection handler for session-bound agent telemetry."""
        from kazma_ui.auth import websocket_is_authenticated

        if not websocket_is_authenticated(websocket):
            logger.warning("[WS-Chat] Unauthenticated connection attempt for session=%s", session_id)
            await websocket.accept()
            await websocket.close(code=4003, reason="Unauthorized")
            return

        await websocket.accept()
        logger.info("[WS-Chat] Client connected: session_id=%s", session_id)

        session, thread_id = _get_session_and_thread(session_id)
        # Rebind live delivery so an in-flight orphaned turn (previous tab /
        # socket) resumes streaming + turn_complete on THIS connection.
        bind_live_socket(thread_id, websocket)
        # LangGraph default recursion_limit is 25 — far too low for multi-tool
        # turns. Derive from long-task / agent.max_iterations (same as gateway).
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            },
            "recursion_limit": _ws_recursion_limit(thread_id),
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

        # Reconnect catch-up: if a turn is still running, tell the client to keep
        # waiting; if the last assistant bubble already has content, push it.
        try:
            from kazma_ui.active_turns import get_active_turn

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
                    sess = get_session_manager().get(session_id)
                    if sess and sess.messages:
                        last = sess.messages[-1]
                        if (
                            last.get("role") == "assistant"
                            and (last.get("content") or "").strip()
                            and not last.get("pending")
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
                                    },
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                except Exception:
                    logger.debug("[WS-Chat] reconnect message catch-up skipped", exc_info=True)
        except Exception as reconnect_exc:
            logger.debug("[WS-Chat] reconnect catch-up failed: %s", reconnect_exc)

        active_task: asyncio.Task | None = None

        try:
            while True:
                data_text = await websocket.receive_text()
                try:
                    payload = json.loads(data_text)
                except Exception:
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

                # ── Action: stop (Stop button) — cancel the running turn ─
                if action == "stop":
                    task = cancel_turn(thread_id)
                    if task is not None:
                        logger.info(
                            "[WS-Chat] Stop requested — cancelling turn for thread=%s",
                            thread_id[:12],
                        )
                    await websocket.send_json(
                        TelemetryEvent(
                            type="stream_end",
                            data={"stopped": True},
                            thread_id=thread_id,
                        ).to_dict()
                    )
                    continue

                # ── Action: steer — soft/hard-steer a running turn ──────
                if action == "steer":
                    _st_text = str(payload.get("text") or "").strip()
                    _st_mode = str(payload.get("mode") or "soft").strip().lower()
                    if not _st_text:
                        await websocket.send_json(TelemetryEvent(
                            type="graph_error", data={"message": "empty steer text"}, thread_id=thread_id,
                        ).to_dict())
                        continue
                    if not is_turn_running(thread_id):
                        await websocket.send_json(TelemetryEvent(
                            type="graph_error", data={"message": "no active task to steer"}, thread_id=thread_id,
                        ).to_dict())
                        continue
                    from kazma_core.agent.steer import (
                        clear_all_steers,
                        is_hard_steer_interrupt,
                        push_hard_steer,
                        push_soft_steer,
                    )

                    if _st_mode != "hard":
                        push_soft_steer(thread_id, _st_text)
                        await websocket.send_json(TelemetryEvent(
                            type="steer", data={"ok": True, "mode": "soft"}, thread_id=thread_id,
                        ).to_dict())
                        continue

                    # Hard steer: queue, wait for the interrupt, resume.
                    push_hard_steer(thread_id, _st_text)
                    await websocket.send_json(TelemetryEvent(
                        type="steer", data={"ok": True, "mode": "hard", "status": "pausing"},
                        thread_id=thread_id,
                    ).to_dict())
                    _st_graph = _get_graph()
                    _st_cfg = {
                        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
                        "recursion_limit": _ws_recursion_limit(thread_id),
                    }
                    _st_paused = None
                    _st_dl = time.monotonic() + 12.0
                    while time.monotonic() < _st_dl:
                        try:
                            _st_snap = await _st_graph.aget_state(_st_cfg)
                        except Exception:  # noqa: BLE001
                            break
                        _st_paused = is_hard_steer_interrupt(_st_snap)
                        if _st_paused is not None or not getattr(_st_snap, "next", None):
                            break
                        await asyncio.sleep(0.2)

                    if _st_paused is None:
                        clear_all_steers(thread_id)
                        push_soft_steer(thread_id, _st_text)
                        await websocket.send_json(TelemetryEvent(
                            type="steer", data={"ok": True, "mode": "soft", "demoted": True},
                            thread_id=thread_id,
                        ).to_dict())
                        continue

                    from kazma_core.safety.commitment.resume import build_resume_command

                    register_turn(thread_id, asyncio.current_task())
                    try:
                        _st_rs = await _st_graph.ainvoke(build_resume_command(action="apply"), _st_cfg)
                    finally:
                        unregister_turn(thread_id)
                    # Surface the resumed assistant text (custom LLM provider
                    # doesn't stream, so emit it as one delta then finalize).
                    _st_asst = ""
                    for _m in reversed((_st_rs.get("messages") if isinstance(_st_rs, dict) else []) or []):
                        if isinstance(_m, dict) and _m.get("role") == "assistant" and _m.get("content"):
                            _st_asst = _m["content"]
                            break
                    if _st_asst:
                        await websocket.send_json(TelemetryEvent(
                            type="llm_delta", data={"content": _st_asst}, thread_id=thread_id,
                        ).to_dict())
                    await websocket.send_json(TelemetryEvent(
                        type="stream_end", data={"steer_resumed": True}, thread_id=thread_id,
                    ).to_dict())
                    continue

                # ── Action: abort — cancel + abandon the running task ────
                if action == "abort":
                    from kazma_core.agent.steer import abort_marker, clear_all_steers

                    clear_all_steers(thread_id)
                    _ab_task = cancel_turn(thread_id)
                    _ab_graph = _get_graph()
                    _ab_cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                    try:
                        _ab_snap = await _ab_graph.aget_state(_ab_cfg)
                        _ab_msgs = list(
                            (_ab_snap.values if _ab_snap and _ab_snap.values else {}).get("messages") or []
                        )
                        _ab_msgs.append({"role": "system", "content": abort_marker()})
                        await _ab_graph.aupdate_state(_ab_cfg, {
                            "messages": _ab_msgs,
                            "task_status": "abandoned",
                            "auto_continue": False,
                        })
                        logger.info(
                            "[WS-Chat] Abort thread=%s cancelled=%s",
                            thread_id[:12], _ab_task is not None,
                        )
                        await websocket.send_json(TelemetryEvent(
                            type="stream_end", data={"aborted": True},
                            thread_id=thread_id,
                        ).to_dict())
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[WS-Chat] Abort marker failed thread=%s: %s", thread_id[:12], exc)
                        await websocket.send_json(TelemetryEvent(
                            type="graph_error", data={"message": f"abort failed: {exc}"},
                            thread_id=thread_id,
                        ).to_dict())
                    continue

                graph_inst = _get_graph()
                if not graph_inst:
                    await websocket.send_json(
                        TelemetryEvent(
                            type="graph_error",
                            data={"message": "Agent graph execution engine not initialized"},
                            thread_id=thread_id,
                        ).to_dict()
                    )
                    continue

                # ── Action 1: send_prompt ────────────────────────────────
                if action == "send_prompt":
                    text = payload.get("text", "").strip()
                    if not text:
                        continue

                    # Ensure active model matches the UI selection (same as SSE).
                    requested_model = str(payload.get("model") or "").strip()
                    if requested_model:
                        try:
                            from kazma_core.runtime.model_switch import ensure_active_model

                            _agent = None
                            if agent_getter is not None:
                                try:
                                    _agent = agent_getter()
                                except Exception:
                                    _agent = None
                            _sw = ensure_active_model(requested_model, agent=_agent)
                            if _sw.ok:
                                logger.info(
                                    "[WS-Chat] ensure-active model=%s provider=%s",
                                    _sw.model,
                                    _sw.provider,
                                )
                            else:
                                logger.warning(
                                    "[WS-Chat] ensure-active model %s failed: %s",
                                    requested_model,
                                    _sw.error,
                                )
                        except Exception as model_exc:
                            logger.warning("[WS-Chat] model ensure failed: %s", model_exc)

                    # Record user interaction on cost circuit breaker to un-halt budget
                    try:
                        cb = _get_cost_breaker()
                        if cb:
                            cb.record_user_interaction()
                    except Exception as cb_err:
                        logger.debug("[WS-Chat] cost_breaker record_user_interaction failed: %s", cb_err)

                    # NOTE: the user message is persisted to SessionStore after
                    # the duplicate-turn guard below (and in each slash-command
                    # handler), so a rejected prompt never leaves a dangling
                    # user bubble with no reply.

                    # ── /yolo, /yolo on, /yolo off, /yolo status (parity with SSE + gateway) ──
                    if text.lower() in ("/yolo", "/yolo on", "/yolo off", "/yolo status"):
                        from kazma_core.safety.yolo import (
                            YoloDisabledError,
                            disable_yolo,
                            enable_yolo,
                            yolo_allowed,
                            yolo_status,
                        )

                        cmd = text.lower().strip()
                        if cmd == "/yolo status":
                            st = yolo_status(thread_id)
                            if st.get("active"):
                                rem = st.get("remaining_seconds")
                                ttl_note = f"Expires in ~{rem // 60}m." if rem is not None else "No auto-expiry."
                                confirmation = f"🚀 YOLO is **ON** for this session. {ttl_note}\nDisable: `/yolo off`"
                            else:
                                prod_note = ""
                                if not yolo_allowed():
                                    prod_note = "\nProduction mode blocks YOLO (set `KAZMA_ALLOW_YOLO=1` to opt in)."
                                confirmation = f"🛡️ YOLO is **OFF**. HITL approvals are required for danger tools.{prod_note}"
                        elif cmd == "/yolo off":
                            disable_yolo(thread_id, actor=f"ws:{session_id[:12]}")
                            confirmation = "🛡️ YOLO deactivated. Safety gates and tool grants are cleared."
                        else:
                            try:
                                st = enable_yolo(thread_id, actor=f"ws:{session_id[:12]}")
                                rem = st.get("remaining_seconds")
                                ttl_note = (
                                    f"Auto-expires in ~{rem // 60} minutes." if rem is not None
                                    else "No auto-expiry."
                                )
                                confirmation = (
                                    "🚀 **YOLO ON** for this session only.\n"
                                    "All danger tools run **without** approval until you `/yolo off` "
                                    f"or TTL ends.\n{ttl_note}\n"
                                    "⚠️ Use only when you fully trust this session."
                                )
                            except YoloDisabledError as yde:
                                confirmation = f"🛡️ {yde}"

                        # Send confirmation back to the client (NOT to the LLM)
                        await websocket.send_json(
                            TelemetryEvent(
                                type="llm_delta",
                                data={"content": confirmation},
                                thread_id=thread_id,
                            ).to_dict()
                        )
                        await websocket.send_json(
                            TelemetryEvent(
                                type="stream_end",
                                data={},
                                thread_id=thread_id,
                            ).to_dict()
                        )
                        # Persist to session UI projection (NOT the graph checkpoint)
                        session.messages.append({"role": "user", "content": text})
                        session.messages.append({"role": "assistant", "content": confirmation})
                        try:
                            get_session_manager().put(session)
                        except Exception:
                            logger.debug("[WS-Chat] Failed persisting YOLO message")
                        continue  # ← CRITICAL: do NOT fall through to the LLM

                    # ── /research deep (parity with SSE + gateway) ─────
                    if text.lower().startswith("/research"):
                        parts = text.split(maxsplit=2)
                        if len(parts) == 1:
                            topic, depth = "", "deep"
                        elif parts[1].lower() in (
                            "deep", "full", "paper", "comprehensive"
                        ):
                            topic = parts[2] if len(parts) > 2 else ""
                            depth = "deep"
                        else:
                            topic = text[len("/research") :].strip()
                            depth = "deep"

                        async def _ws_research() -> None:
                            try:
                                # Keep the transcript complete: user question first.
                                try:
                                    session.add_message("user", text)
                                    get_session_manager().put(session)
                                except Exception:
                                    pass
                                if not topic:
                                    await websocket.send_json(
                                        TelemetryEvent(
                                            type="llm_delta",
                                            data={
                                                "content": "Usage: `/research deep <topic>`"
                                            },
                                            thread_id=thread_id,
                                        ).to_dict()
                                    )
                                    return

                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="llm_delta",
                                        data={
                                            "content": (
                                                f"🔬 Deep research starting: **{topic}**…\n\n"
                                            )
                                        },
                                        thread_id=thread_id,
                                    ).to_dict()
                                )

                                from kazma_core.tools.research_pipeline import (
                                    run_research_pipeline,
                                )

                                async def _progress(stage: str, message: str) -> None:
                                    await websocket.send_json(
                                        TelemetryEvent(
                                            type="tool_start",
                                            data={
                                                "tool_name": f"research:{stage}",
                                                "inputs": message[:200],
                                            },
                                            thread_id=thread_id,
                                        ).to_dict()
                                    )

                                out = await run_research_pipeline(
                                    topic,
                                    depth=depth,
                                    max_sources=8,
                                    progress_cb=_progress,
                                    export_docx=True,
                                )
                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="llm_delta",
                                        data={"content": out},
                                        thread_id=thread_id,
                                    ).to_dict()
                                )
                                try:
                                    session.add_message("assistant", out)
                                    get_session_manager().put(session)
                                except Exception:
                                    pass
                            except Exception as exc:
                                logger.exception("[WS-Chat] /research failed")
                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="graph_error",
                                        data={"message": f"Research failed: {exc}"},
                                        thread_id=thread_id,
                                    ).to_dict()
                                )
                            finally:
                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="idle",
                                        data={},
                                        thread_id=thread_id,
                                    ).to_dict()
                                )
                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="stream_end",
                                        data={},
                                        thread_id=thread_id,
                                    ).to_dict()
                                )

                        await _ws_research()
                        continue

                    # Client-side correlation id for prompt_ack (durable UI).
                    client_msg_id = str(
                        payload.get("client_msg_id") or uuid.uuid4()
                    ).strip() or str(uuid.uuid4())

                    async def _send_prompt_ack(
                        *,
                        accepted: bool,
                        reason: str = "",
                        message: str = "",
                    ) -> None:
                        """Tell the browser the prompt was (or was not) durable."""
                        data: dict[str, Any] = {
                            "client_msg_id": client_msg_id,
                            "accepted": accepted,
                            "session_id": session_id,
                            "message_count": len(getattr(session, "messages", []) or []),
                        }
                        if reason:
                            data["reason"] = reason
                        if message:
                            data["message"] = message
                        try:
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="prompt_ack",
                                    data=data,
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                        except Exception:
                            logger.debug(
                                "[WS-Chat] prompt_ack send failed session=%s",
                                session_id[:12],
                                exc_info=True,
                            )

                    # ── Cross-transport duplicate-turn guard ─────────────
                    # If a DETACHED turn (previous connection that refreshed/
                    # switched tabs, or an SSE pump) is still running on this
                    # thread, do NOT start a second concurrent graph invocation
                    # — checkpoint writes would interleave and one turn would
                    # error out ("no output"). The running turn persists its
                    # result; the client polls and picks it up. A turn owned by
                    # THIS connection (active_task) is superseded below instead.
                    _detached = get_active_turn(thread_id)
                    if (
                        _detached is not None
                        and not _detached.done()
                        and _detached is not active_task
                    ):
                        # Abandoned turn: the client has been gone for longer
                        # than DETACHED_TTL_S. Cancel it and await its full
                        # unwind BEFORE the new run starts — strictly
                        # sequential, so the two runs never interleave on the
                        # checkpointer, and the abandoned turn stops billing
                        # its remaining LLM calls.
                        stale = reap_stale_turn(thread_id)
                        if stale is not None and stale is _detached:
                            logger.info(
                                "[WS-Chat] Reaping stale detached turn for thread=%s",
                                thread_id[:12],
                            )
                            stale.cancel()
                            with contextlib.suppress(
                                asyncio.CancelledError, Exception
                            ):
                                await stale
                            _detached = get_active_turn(thread_id)
                        if (
                            _detached is not None
                            and not _detached.done()
                            and _detached is not active_task
                        ):
                            logger.info(
                                "[WS-Chat] Rejecting prompt for thread=%s (turn still running)",
                                thread_id[:12],
                            )
                            busy_msg = (
                                "⏳ Your previous message is still being "
                                "processed. It will appear here shortly — no need to resend."
                            )
                            await _send_prompt_ack(
                                accepted=False,
                                reason="turn_busy",
                                message=busy_msg,
                            )
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="graph_error",
                                    data={"message": busy_msg},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            await websocket.send_json(
                                EventBridge.create_idle_event(thread_id).to_dict()
                            )
                            continue

                    # Durability: persist the user message BEFORE the graph
                    # starts (and before any long setup). Tab switch / refresh
                    # must still show the prompt even if the LLM never replies.
                    # client_msg_id dedup: reconnect resends must not double-insert.
                    try:
                        store = get_session_manager()
                        live = store.get(session_id) or session
                        already = False
                        for m in live.messages or []:
                            if (
                                m.get("role") == "user"
                                and m.get("client_msg_id")
                                and m.get("client_msg_id") == client_msg_id
                            ):
                                already = True
                                break
                        if already:
                            session = live
                            await _send_prompt_ack(
                                accepted=True,
                                reason="duplicate",
                            )
                            # Turn already running from the first accept — do not
                            # start a second graph invocation.
                            _alive = get_active_turn(thread_id)
                            if _alive is not None and not _alive.done():
                                logger.info(
                                    "[WS-Chat] Duplicate prompt_ack (turn running) "
                                    "session=%s client_msg_id=%s",
                                    session_id[:12],
                                    client_msg_id[:12],
                                )
                                continue
                            # Stuck pending bubble with no live task → fall
                            # through and re-run the graph without re-inserting
                            # the user message.
                            last = live.messages[-1] if live.messages else None
                            if not (
                                last
                                and last.get("role") == "assistant"
                                and last.get("pending")
                            ):
                                logger.info(
                                    "[WS-Chat] Duplicate prompt_ack (turn finished) "
                                    "session=%s client_msg_id=%s",
                                    session_id[:12],
                                    client_msg_id[:12],
                                )
                                continue
                            logger.info(
                                "[WS-Chat] Replaying stuck turn session=%s "
                                "client_msg_id=%s",
                                session_id[:12],
                                client_msg_id[:12],
                            )
                        else:
                            live.add_message(
                                "user",
                                text,
                                client_msg_id=client_msg_id,
                            )
                            if not live.thread_id:
                                live.thread_id = thread_id
                            # First message makes the session durable.
                            store.put(live)
                            session = live
                            # "Turn in progress" marker for reload / poller.
                            _ensure_pending_assistant_bubble(session_id)
                            # ACK after durable write so the client stops retrying.
                            await _send_prompt_ack(accepted=True)
                            logger.info(
                                "[WS-Chat] Accepted prompt session=%s thread=%s "
                                "client_msg_id=%s chars=%d",
                                session_id[:12],
                                thread_id[:12],
                                client_msg_id[:12],
                                len(text),
                            )
                    except Exception as exc:
                        logger.warning(
                            "[WS-Chat] Failed writing user msg to SessionStore: %s",
                            exc,
                        )
                        await _send_prompt_ack(
                            accepted=False,
                            reason="persist_failed",
                            message="Could not save your message. Please try again.",
                        )
                        await websocket.send_json(
                            TelemetryEvent(
                                type="graph_error",
                                data={
                                    "message": "Could not save your message. Please try again."
                                },
                                thread_id=thread_id,
                            ).to_dict()
                        )
                        await websocket.send_json(
                            EventBridge.create_idle_event(thread_id).to_dict()
                        )
                        continue

                    from kazma_core.agent.turn_input import build_turn_messages
                    from kazma_core.agent.state import initial_supervisor_state
                    from kazma_core.agent.long_task import consume_long_task_turn
                    from kazma_core.memory.config import resolve_tenant_id
                    from kazma_core.ide.env_context import build_env_context

                    # Consume a long_task turn-budget at the START of each new
                    # user message — so a /long from a previous task doesn't
                    # haunt this thread with max_iterations=100 forever.
                    consume_long_task_turn(thread_id)

                    env_block = build_env_context()
                    sys_msgs = [{"role": "system", "content": env_block}] if env_block else None
                    full_messages = await build_turn_messages(
                        graph_inst,
                        config,
                        user_text=text,
                        system_messages=sys_msgs,
                        fallback_history=session.messages,
                    )

                    # T7: attachments over the WS bus — same multimodal build as
                    # the SSE path (agent_handler/attachments.py) so both
                    # transports produce identical OpenAI-compatible content.
                    raw_attachments = payload.get("attachments") or []
                    if raw_attachments and full_messages:
                        try:
                            from kazma_gateway.agent_handler.attachments import (
                                build_user_content,
                            )
                            from kazma_ui.chat_attachments import (
                                attachments_from_client_payload,
                            )

                            atts = attachments_from_client_payload(raw_attachments)
                            multimodal_content = build_user_content(text or "", atts)
                            for i in range(len(full_messages) - 1, -1, -1):
                                if (
                                    isinstance(full_messages[i], dict)
                                    and full_messages[i].get("role") == "user"
                                ):
                                    full_messages[i]["content"] = multimodal_content
                                    break
                        except Exception:  # noqa: BLE001 — never block a turn on media
                            logger.debug("[WS-Chat] attachment content build failed", exc_info=True)
                    # Stamp durable thread_id into state so YOLO/HITL grants
                    # resolve even if the ContextVar is lost mid-graph.
                    _auth_uid = ""
                    try:
                        principal = getattr(websocket.state, "principal", None) or getattr(
                            websocket.state, "user", None
                        )
                        if isinstance(principal, dict):
                            _auth_uid = str(
                                principal.get("user_id")
                                or principal.get("sub")
                                or principal.get("id")
                                or ""
                            )
                    except Exception:
                        pass
                    input_state = initial_supervisor_state(
                        thread_id=thread_id,
                        tenant_id=resolve_tenant_id(
                            "web", "", session_id, auth_user_id=_auth_uid
                        ),
                    )
                    input_state["messages"] = full_messages
                    # Transport-level working-memory pin (before supervisor loop)
                    try:
                        from kazma_core.agent.turn_input import build_turn_working_memory

                        _wm = build_turn_working_memory(
                            text,
                            messages=full_messages,
                            client_attachments=list(raw_attachments or []),
                        )
                        input_state.update(_wm)
                        if _wm.get("hard_constraints"):
                            logger.info(
                                "[WS-Chat] Pinned working memory constraints=%s "
                                "attachments=%d goal_chars=%d",
                                _wm.get("hard_constraints"),
                                len(_wm.get("active_attachments") or []),
                                len(_wm.get("active_goal") or ""),
                            )
                    except Exception:
                        logger.debug(
                            "[WS-Chat] working-memory pin skipped", exc_info=True
                        )

                    async def _run_prompt_stream():
                        from kazma_core.safety.hitl import (
                            reset_current_thread_id,
                            set_current_thread_id,
                        )

                        send, is_lost = _make_ws_sender(websocket, thread_id)
                        assistant_content_acc = ""
                        activity_log: list[dict[str, Any]] = []
                        thought_recorded: list[bool] = [False]
                        tid_token = set_current_thread_id(thread_id)
                        try:
                            pre_msg_count = 0
                            try:
                                snap = await graph_inst.aget_state(config)
                                if snap and getattr(snap, "values", None):
                                    p_msgs = snap.values.get("messages")
                                    if isinstance(p_msgs, list):
                                        pre_msg_count = len(p_msgs)
                            except Exception:
                                pass

                            # Bind YOLO/tool-grant ContextVar for the whole turn.
                            # Without this, is_yolo_active/has_tool_grant never
                            # match and every danger tool re-prompts HITL.
                            stream = graph_inst.astream_events(
                                input_state, config=config, version="v2"
                            )
                            tokens_emitted = False
                            turn_started_at = time.monotonic()
                            last_progress_at = {"t": time.monotonic()}

                            # Turn timeout (audit M14, same budget as agent_runner).
                            # Without this, a runaway tool loop on the WS path can
                            # spin for 20+ minutes with no kill switch.
                            import os as _os_turn_to
                            _raw_turn_to = (_os_turn_to.environ.get("KAZMA_TURN_TIMEOUT_SECONDS") or "600").strip()
                            try:
                                _turn_timeout_s = float(_raw_turn_to)
                            except (TypeError, ValueError):
                                _turn_timeout_s = 600.0
                            _turn_timed_out = False

                            # Industry-grade long-horizon heartbeat: every 15s
                            # with no events, emit status so the UI idle-watchdog
                            # stays armed and the user sees "still working".
                            async def _long_turn_heartbeat() -> None:
                                n = 0
                                while True:
                                    await asyncio.sleep(15.0)
                                    if is_lost():
                                        return
                                    idle = time.monotonic() - last_progress_at["t"]
                                    if idle < 14.0:
                                        continue
                                    n += 1
                                    elapsed = int(time.monotonic() - turn_started_at)
                                    await send(
                                        TelemetryEvent(
                                            type="status_update",
                                            data={
                                                "status": "thinking",
                                                "message": f"Still working… ({elapsed}s)",
                                                "active_node": "Supervisor",
                                                "heartbeat": n,
                                                "elapsed_s": elapsed,
                                            },
                                            thread_id=thread_id,
                                        ).to_dict()
                                    )

                            heartbeat_task = asyncio.create_task(_long_turn_heartbeat())
                            try:
                                async for ev in EventBridge.process_stream(stream, thread_id=thread_id):
                                    # Turn timeout check — break the stream if the
                                    # wall-clock budget is exceeded (runaway tool loop).
                                    if _turn_timeout_s > 0 and (time.monotonic() - turn_started_at) > _turn_timeout_s:
                                        _turn_timed_out = True
                                        logger.warning(
                                            "[WS-Chat] Turn timed out after %.0fs thread=%s "
                                            "— breaking stream (KAZMA_TURN_TIMEOUT_SECONDS)",
                                            _turn_timeout_s, thread_id,
                                        )
                                        break
                                    last_progress_at["t"] = time.monotonic()
                                    _record_ws_activity(activity_log, ev, thought_recorded=thought_recorded)
                                    if ev.type == "llm_delta":
                                        tokens_emitted = True
                                        if hasattr(ev, "data") and isinstance(ev.data, dict):
                                            content = ev.data.get("content", "")
                                            if content:
                                                assistant_content_acc += content
                                                if len(assistant_content_acc) % 50 == 0:
                                                    # T4: serialize with the other
                                                    # persist paths on this session.
                                                    try:
                                                        with get_session_manager().transact(session_id) as sess:
                                                            if (
                                                                sess.messages
                                                                and sess.messages[-1].get("role")
                                                                == "assistant"
                                                            ):
                                                                sess.messages[-1]["content"] = (
                                                                    assistant_content_acc
                                                                )
                                                                if activity_log:
                                                                    sess.messages[-1]["activity"] = list(
                                                                        activity_log
                                                                    )
                                                            else:
                                                                sess.add_message(
                                                                    "assistant",
                                                                    assistant_content_acc,
                                                                    model=_resolve_active_model() or None,
                                                                )
                                                    except Exception:
                                                        logger.debug(
                                                            "[WS-Chat] incremental persist failed",
                                                            exc_info=True,
                                                        )

                                    # CRITICAL: when the client disconnected (refresh /
                                    # tab switch) is_lost() is True. Keep draining the
                                    # LangGraph stream so the turn COMPLETES and
                                    # persists — never let a dead socket abort the
                                    # async-for and cancel the graph run.
                                    if is_lost():
                                        mark_turn_orphaned(thread_id)
                                        continue
                                    await send(ev.to_dict())
                            finally:
                                heartbeat_task.cancel()
                                with contextlib.suppress(asyncio.CancelledError, Exception):
                                    await heartbeat_task

                            # Turn-timeout notification: if the stream was broken
                            # by the wall-clock budget, tell the user (don't leave
                            # them staring at a dead "thinking…" indicator).
                            if _turn_timed_out and not is_lost():
                                await send(
                                    TelemetryEvent(
                                        type="status_update",
                                        data={
                                            "status": "error",
                                            "message": (
                                                f"⚠️ Turn timed out after {int(_turn_timeout_s)}s. "
                                                "The agent was stuck in a long tool loop. "
                                                "Try a shorter request or raise "
                                                "KAZMA_TURN_TIMEOUT_SECONDS."
                                            ),
                                        },
                                        thread_id=thread_id,
                                    ).to_dict()
                                )

                            # Always backfill from checkpoint at end (custom LLM
                            # has no on_chat_model_stream). Emit progressive
                            # llm_delta only when nothing was streamed yet —
                            # turn_complete is the authoritative replace paint.
                            await _backfill_assistant_text_if_needed(
                                graph_inst,
                                config,
                                websocket,
                                thread_id,
                                pre_msg_count,
                                emit_delta=not tokens_emitted,
                                already_streamed=tokens_emitted,
                            )
                            if not is_lost():
                                await asyncio.sleep(0.05)

                            interrupted = await _scan_and_emit_hitl_interrupt(
                                graph_inst, config, websocket, thread_id
                            )

                            _active_model = _resolve_active_model()
                            final_text = await _persist_final_assistant_message(
                                graph_inst,
                                config,
                                session_id,
                                pre_msg_count=pre_msg_count,
                                prefer_text=assistant_content_acc,
                                activity=activity_log,
                                model=_active_model,
                            )
                            if interrupted:
                                # Paused for approval — the approval card takes
                                # over. Pop pending so a reload doesn't show the
                                # "still processing" bubble forever.
                                try:
                                    with get_session_manager().transact(session_id) as sess:
                                        if (
                                            sess.messages
                                            and sess.messages[-1].get("role") == "assistant"
                                        ):
                                            sess.messages[-1].pop("pending", None)
                                            if _active_model:
                                                sess.messages[-1]["model"] = _active_model
                                except Exception:
                                    pass
                            elif not (final_text or "").strip():
                                # Empty turn — resolve the pending bubble with a
                                # recovery notice (never leave it stuck).
                                recovery = (
                                    "⚠️ No assistant text was returned for this turn "
                                    "(model may have failed silently or only planned "
                                    "tools). Please try again or check server logs."
                                )
                                if not is_lost():
                                    await send(
                                        TelemetryEvent(
                                            type="llm_delta",
                                            data={"content": recovery},
                                            thread_id=thread_id,
                                        ).to_dict()
                                    )
                                await _persist_final_assistant_message(
                                    graph_inst,
                                    config,
                                    session_id,
                                    pre_msg_count=pre_msg_count,
                                    prefer_text=recovery,
                                    activity=activity_log,
                                    model=_active_model,
                                )
                            # Always release the UI turn lock. HITL pause is
                            # signalled via pendingApproval; idle still ends
                            # the "generating" state so Stop never sticks.
                            # turn_complete carries final content so clients
                            # never depend on racey partial deltas alone.
                            await send(
                                TelemetryEvent(
                                    type="turn_complete",
                                    data={
                                        "content": (final_text or assistant_content_acc or ""),
                                        "interrupted": bool(interrupted),
                                        "empty": not bool((final_text or assistant_content_acc or "").strip()),
                                        "model": _active_model,
                                        "session_id": session_id,
                                        "duration_ms": int((time.monotonic() - turn_started_at) * 1000),
                                    },
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            await send(EventBridge.create_idle_event(thread_id).to_dict())
                            await send(
                                TelemetryEvent(
                                    type="stream_end",
                                    data={"interrupted": bool(interrupted)},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            logger.info(
                                "[WS-Chat] turn_complete thread=%s model=%s content_len=%d interrupted=%s",
                                thread_id[:12],
                                _active_model or "?",
                                len(final_text or assistant_content_acc or ""),
                                interrupted,
                            )
                            if interrupted:
                                logger.info(
                                    "[WS-Chat] Prompt paused for HITL thread=%s", thread_id
                                )
                        except asyncio.CancelledError:
                            logger.info(
                                "[WS-Chat] Prompt stream cancelled for session=%s", session_id
                            )
                            # Always clear pending bubble — empty cancel must not
                            # leave "still processing in the background" on reload.
                            try:
                                await _persist_final_assistant_message(
                                    graph_inst,
                                    config,
                                    session_id,
                                    prefer_text=(
                                        assistant_content_acc.strip()
                                        or "⚠️ Turn cancelled."
                                    ),
                                    activity=activity_log,
                                    model=_resolve_active_model(),
                                )
                            except Exception as e:
                                logger.warning(
                                    "[WS-Chat] Failed to persist assistant on cancel: %s",
                                    e,
                                )
                            raise
                        except Exception as exc:
                            logger.exception("[WS-Chat] Error in prompt stream: %s", exc)
                            err_msg = _friendly_graph_error(exc)
                            recovery = f"⚠️ {err_msg}"
                            # ALWAYS persist + clear pending — live UI may show the
                            # error, but SessionStore still had pending=True empty
                            # content, so refresh showed "still processing…".
                            try:
                                await _persist_final_assistant_message(
                                    graph_inst,
                                    config,
                                    session_id,
                                    prefer_text=(
                                        assistant_content_acc.strip() or recovery
                                    ),
                                    activity=activity_log,
                                    model=_resolve_active_model(),
                                )
                            except Exception as e:
                                logger.warning(
                                    "[WS-Chat] Failed to persist assistant on error: %s",
                                    e,
                                )
                            # Safe sends: no-op after the client disconnected.
                            await send(
                                TelemetryEvent(
                                    type="graph_error",
                                    data={"message": err_msg},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            # Always surface something in the transcript
                            await send(
                                TelemetryEvent(
                                    type="llm_delta",
                                    data={"content": recovery},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            await send(
                                TelemetryEvent(
                                    type="turn_complete",
                                    data={
                                        "content": recovery,
                                        "interrupted": False,
                                        "empty": False,
                                        "error": True,
                                        "model": _resolve_active_model(),
                                        "session_id": session_id,
                                    },
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            await send(
                                EventBridge.create_idle_event(thread_id).to_dict()
                            )
                            await send(
                                TelemetryEvent(
                                    type="stream_end",
                                    data={"error": True},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                        finally:
                            reset_current_thread_id(tid_token)

                    if active_task and not active_task.done():
                        active_task.cancel()
                    active_task = asyncio.create_task(_run_prompt_stream())
                    register_turn(thread_id, active_task)
                    active_task.add_done_callback(
                        lambda t: unregister_turn(thread_id, t)
                    )

                # ── Action 2: approve_tool ───────────────────────────────
                elif action == "approve_tool":
                    approved = bool(payload.get("approved", True))
                    scope = str(payload.get("scope") or "once").strip().lower()
                    if scope in ("session",):
                        scope = "yolo"
                    if scope == "allow_tool":
                        scope = "tool"
                    if scope not in ("once", "tool", "yolo"):
                        scope = "once"
                    # Prefer the LangGraph thread (session.thread_id). Clients
                    # sometimes send session_id by mistake — that would key
                    # YOLO/grants against the wrong id and re-prompt forever.
                    requested_tid = str(payload.get("thread_id") or "").strip()
                    target_thread_id = thread_id
                    if requested_tid and requested_tid == thread_id:
                        target_thread_id = requested_tid
                    elif requested_tid and requested_tid == session_id:
                        target_thread_id = thread_id
                    elif requested_tid:
                        logger.warning(
                            "[WS-Chat] Rejecting approval for non-session thread=%s session=%s",
                            requested_tid,
                            session_id,
                        )
                        await websocket.send_json(
                            ApprovalEventBridge.create_approval_error_event(
                                thread_id,
                                error="Approval request does not belong to this session",
                                code="THREAD_OWNERSHIP_MISMATCH",
                                scope=scope,
                            )
                        )
                        continue

                    actor = f"ws:{(session_id or '')[:12] or 'anon'}"
                    approve_config: dict[str, Any] = {
                        "configurable": {
                            "thread_id": target_thread_id,
                            "checkpoint_ns": "",
                        },
                        "recursion_limit": _ws_recursion_limit(target_thread_id),
                    }

                    from kazma_ui.sse_utils import ApprovalEventBridge
                    # Use module-level `import time` — a nested import here makes
                    # `time` a local of chat_websocket and breaks free-variable
                    # lookup in _run_prompt_stream / heartbeat (UnboundLocalError:
                    # "cannot access free variable 'time'...").

                    approval_start_time = time.monotonic()
                    tool_name = "unknown"
                    tools_to_grant: list[str] = []

                    # Snapshot pending tools BEFORE resume so we can apply
                    # tool-scope grants (and report the real tool name).
                    try:
                        pre_snap = await graph_inst.aget_state(approve_config)
                        tool_name, tools_to_grant = _extract_pending_tools_from_snapshot(pre_snap)
                        if not tool_name:
                            tool_name = "unknown"
                    except Exception as e:
                        logger.debug("[WS-Chat] Failed to extract tool name for approval: %s", e)

                    # Explicit client tool name as fallback
                    explicit_tool = payload.get("tool") or payload.get("grant_tool")
                    if explicit_tool:
                        tools_to_grant.append(str(explicit_tool))
                    tools_to_grant = list(dict.fromkeys(t for t in tools_to_grant if t))

                    # Apply scope grants *before* resume so later danger tools
                    # in the same ainvoke skip interrupt (mirrors HTTP path).
                    if approved and scope == "yolo":
                        try:
                            from kazma_core.safety.yolo import YoloDisabledError, enable_yolo

                            enable_yolo(target_thread_id, actor=actor)
                            logger.warning(
                                "[WS-Chat] YOLO enabled for thread=%s actor=%s",
                                target_thread_id,
                                actor,
                            )
                        except YoloDisabledError as yde:
                            logger.warning("[WS-Chat] YOLO blocked: %s", yde)
                            await websocket.send_json(
                                ApprovalEventBridge.create_approval_error_event(
                                    target_thread_id,
                                    error=str(yde),
                                    code="YOLO_DISABLED",
                                    tool=tool_name,
                                    scope=scope,
                                )
                            )
                            await websocket.send_json(
                                EventBridge.create_idle_event(target_thread_id).to_dict()
                            )
                            continue
                        except Exception as exc:
                            logger.warning("[WS-Chat] Failed to enable YOLO scope: %s", exc)
                    elif approved and scope == "tool":
                        try:
                            from kazma_core.safety.hitl_grants import grant_tool

                            for tname in tools_to_grant:
                                grant_tool(target_thread_id, tname, actor=actor)
                        except Exception as exc:
                            logger.warning("[WS-Chat] Failed to apply tool grant: %s", exc)

                    # Build the resume Command via the single chokepoint
                    # (build_resume_command). Semantic clarify needs
                    # {tool_call_id: option_id}; security needs {approved, scope}.
                    # The old hardcoded security shape here never resolved a
                    # semantic card → infinite loop (incident 2026-08-12).
                    from kazma_core.safety.commitment.resume import (
                        build_resume_command,
                        read_pending_interrupt,
                    )

                    _intr_payload = await read_pending_interrupt(
                        graph_inst, approve_config, snapshot=pre_snap,
                    )
                    resume_command = build_resume_command(
                        _intr_payload, approved=approved,
                        choices=payload.get("choices") if isinstance(payload.get("choices"), dict) else None,
                        scope=scope,
                    )
                    if resume_command is None:
                        # Stale card — fall back to a security resume so the
                        # approve stream completes deterministically.
                        resume_command = build_resume_command(
                            {"type": "hitl_approval", "kind": "security"},
                            approved=approved, scope=scope,
                        )

                    await websocket.send_json(
                        ApprovalEventBridge.create_approval_started_event(
                            target_thread_id,
                            tool=tool_name,
                            scope=scope,
                            request_id=session_id[:12],
                        )
                    )
                    # English message is a fallback for non-UI clients; the
                    # chat UI localizes via step + tool/details (CHAT_I18N).
                    _prep_msg = (
                        f"Preparing to execute {len(tools_to_grant)} tools..."
                        if len(tools_to_grant) > 1
                        else f"Preparing to execute {tool_name}..."
                    )
                    await websocket.send_json(
                        ApprovalEventBridge.create_approval_progress_event(
                            target_thread_id,
                            _prep_msg,
                            "preparing",
                            {
                                "tool": tool_name,
                                "scope": scope,
                                "tools": tools_to_grant,
                                "n": len(tools_to_grant) or 1,
                            },
                        )
                    )

                    async def _run_approve_stream():
                        from kazma_core.safety.hitl import (
                            reset_current_thread_id,
                            set_current_thread_id,
                        )

                        send, is_lost = _make_ws_sender(
                            websocket, target_thread_id or thread_id
                        )
                        assistant_content_acc = ""
                        activity_log: list[dict[str, Any]] = []
                        thought_recorded: list[bool] = [False]
                        tid_token = set_current_thread_id(target_thread_id)
                        try:
                            pre_msg_count = 0
                            try:
                                snap = await graph_inst.aget_state(approve_config)
                                if snap and getattr(snap, "values", None):
                                    p_msgs = snap.values.get("messages")
                                    if isinstance(p_msgs, list):
                                        pre_msg_count = len(p_msgs)
                            except Exception:
                                pass

                            from datetime import UTC, datetime

                            _hitl_state = "approved" if approved else "denied"
                            _resolution_time = datetime.now(UTC).isoformat()
                            # Postgres jsonb_set requires a *JSON* value, not a
                            # bare string token (error: Token "approved" is invalid).
                            _hitl_state_json = json.dumps(_hitl_state)
                            _resolution_json = json.dumps(_resolution_time)
                            try:
                                cp = getattr(graph_inst, "checkpointer", None)
                                if cp is not None:
                                    conn = getattr(cp, "conn", None)
                                    if conn is not None:
                                        try:
                                            if hasattr(conn, "execute"):
                                                # SQLite: plain strings become JSON strings
                                                await conn.execute(
                                                    "UPDATE checkpoints SET metadata = json_set(metadata, '$.hitl_state', ?) WHERE thread_id = ?",
                                                    (_hitl_state, target_thread_id),
                                                )
                                                await conn.execute(
                                                    "UPDATE checkpoints SET metadata = json_set(metadata, '$.hitl_resolved_at', ?) WHERE thread_id = ?",
                                                    (_resolution_time, target_thread_id),
                                                )
                                                await conn.commit()
                                            elif hasattr(conn, "connection"):
                                                # Postgres: jsonb_set requires a JSON document
                                                async with conn.connection() as pg_conn:
                                                    async with pg_conn.cursor() as cur:
                                                        await cur.execute(
                                                            "UPDATE checkpoints SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{hitl_state}', %s::jsonb) WHERE thread_id = %s",
                                                            (_hitl_state_json, target_thread_id),
                                                        )
                                                        await cur.execute(
                                                            "UPDATE checkpoints SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{hitl_resolved_at}', %s::jsonb) WHERE thread_id = %s",
                                                            (_resolution_json, target_thread_id),
                                                        )
                                                        await pg_conn.commit()
                                        except Exception as e:
                                            logger.warning(
                                                "[WS-Chat] Failed to update checkpoint metadata for thread=%s: %s",
                                                target_thread_id,
                                                e,
                                            )
                            except Exception as e:
                                logger.debug(
                                    "[WS-Chat] Could not update checkpoint metadata: %s", e
                                )

                            activity_log.append({
                                "kind": "thought",
                                "title": "Approval resumed — continuing",
                                "state": "running",
                            })
                            thought_recorded[0] = True
                            await send(
                                ApprovalEventBridge.create_approval_resuming_event(
                                    target_thread_id,
                                    tool=tool_name,
                                    scope=scope,
                                )
                            )
                            await send(
                                TelemetryEvent(
                                    type="status_update",
                                    data={
                                        "status": "thinking",
                                        "message": (
                                            f"Running after {scope} approval…"
                                            if approved
                                            else "Continuing after deny…"
                                        ),
                                        "active_node": "ToolWorker",
                                    },
                                    thread_id=target_thread_id,
                                ).to_dict()
                            )

                            # CRITICAL: ainvoke for Command resume (custom LLM
                            # has no on_chat_model_stream; astream_events can hang).
                            # Heartbeat so the UI is not silent for multi-minute
                            # YOLO tool loops (user thought the agent "stopped").
                            logger.info(
                                "[WS-Chat] HITL resume via ainvoke thread=%s scope=%s",
                                target_thread_id,
                                scope,
                            )
                            # Mark trailing assistant pending *before* long ainvoke
                            # so a mid-resume F5 still arms the SessionStore poller
                            # and the final answer is not "only visible after refresh".
                            _ensure_pending_assistant_bubble(session_id, force=True)
                            resume_task = asyncio.create_task(
                                graph_inst.ainvoke(resume_command, approve_config)
                            )
                            heartbeat_n = 0
                            while not resume_task.done():
                                try:
                                    await asyncio.wait_for(
                                        asyncio.shield(resume_task), timeout=4.0
                                    )
                                except asyncio.TimeoutError:
                                    heartbeat_n += 1
                                    if not is_lost():
                                        await send(
                                            TelemetryEvent(
                                                type="status_update",
                                                data={
                                                    "status": "thinking",
                                                    "message": (
                                                        f"Still working after approval "
                                                        f"({heartbeat_n * 4}s)…"
                                                    ),
                                                    "active_node": "ToolWorker",
                                                },
                                                thread_id=target_thread_id,
                                            ).to_dict()
                                        )
                                    else:
                                        mark_turn_orphaned(target_thread_id)
                            await resume_task  # re-raise graph errors

                            # Resume uses ainvoke — no live token stream. Extract
                            # + persist, then deliver via turn_complete (replace
                            # semantics). Do NOT emit a full-text llm_delta here:
                            # that appended onto pre-HITL partials and duplicated
                            # the answer; refresh then showed store dups too.
                            _active_model = _resolve_active_model()
                            backfill_text = await _backfill_assistant_text_if_needed(
                                graph_inst,
                                approve_config,
                                websocket,
                                target_thread_id,
                                pre_msg_count,
                                emit_delta=False,
                                already_streamed=False,
                            )
                            final_text = await _persist_final_assistant_message(
                                graph_inst,
                                approve_config,
                                session_id,
                                pre_msg_count=pre_msg_count,
                                prefer_text=backfill_text or assistant_content_acc,
                                activity=activity_log,
                                model=_active_model,
                            )
                            # Guarantee the user sees *something* after YOLO/approve.
                            # Empty backfill is the root of "only saw pre-HITL line".
                            if not (final_text or "").strip():
                                recovery = (
                                    "⚠️ Approved tools finished, but no final summary "
                                    "was produced (often max tool rounds). "
                                    "Ask me to summarize what I found, or retry with "
                                    "a narrower question."
                                )
                                final_text = await _persist_final_assistant_message(
                                    graph_inst,
                                    approve_config,
                                    session_id,
                                    pre_msg_count=pre_msg_count,
                                    prefer_text=recovery,
                                    activity=activity_log,
                                    model=_active_model,
                                )
                                logger.warning(
                                    "[WS-Chat] Empty post-approve turn thread=%s — recovery notice sent",
                                    target_thread_id,
                                )
                            else:
                                logger.info(
                                    "[WS-Chat] Post-approve text delivered thread=%s chars=%d",
                                    target_thread_id,
                                    len(final_text),
                                )

                            interrupted = await _scan_and_emit_hitl_interrupt(
                                graph_inst, approve_config, websocket, target_thread_id
                            )

                            duration_ms = (time.monotonic() - approval_start_time) * 1000
                            # Authoritative client paint — same contract as prompt path.
                            # Without this the UI stayed on "Still working after approval"
                            # until refresh (industry gap).
                            await send(
                                TelemetryEvent(
                                    type="turn_complete",
                                    data={
                                        "content": final_text or "",
                                        "interrupted": bool(interrupted),
                                        "empty": not bool((final_text or "").strip()),
                                        "model": _active_model,
                                        "session_id": session_id,
                                        "duration_ms": int(duration_ms),
                                        "source": "hitl_resume",
                                    },
                                    thread_id=target_thread_id,
                                ).to_dict()
                            )
                            await send(
                                ApprovalEventBridge.create_approval_complete_event(
                                    target_thread_id,
                                    tool=tool_name,
                                    scope=scope,
                                    duration_ms=duration_ms,
                                )
                            )
                            # Always idle + stream_end — releases UI even if
                            # another HITL card is about to show.
                            await send(
                                EventBridge.create_idle_event(target_thread_id).to_dict()
                            )
                            await send(
                                TelemetryEvent(
                                    type="stream_end",
                                    data={"interrupted": bool(interrupted)},
                                    thread_id=target_thread_id,
                                ).to_dict()
                            )
                            logger.info(
                                "[WS-Chat] approve turn_complete thread=%s model=%s "
                                "content_len=%d interrupted=%s",
                                target_thread_id[:12],
                                _active_model or "?",
                                len(final_text or ""),
                                interrupted,
                            )
                            if interrupted:
                                logger.info(
                                    "[WS-Chat] Resume paused again for HITL thread=%s "
                                    "(scope=%s — YOLO/grants should prevent this)",
                                    target_thread_id,
                                    scope,
                                )
                        except asyncio.CancelledError:
                            logger.info(
                                "[WS-Chat] Approve stream cancelled for session=%s", session_id
                            )
                            if assistant_content_acc:
                                try:
                                    await _persist_final_assistant_message(
                                        graph_inst,
                                        approve_config,
                                        session_id,
                                        prefer_text=assistant_content_acc,
                                        activity=activity_log,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "[WS-Chat] Failed to persist partial assistant on approve cancel: %s",
                                        e,
                                    )
                            raise
                        except Exception as exc:
                            logger.exception("[WS-Chat] Error in approve stream: %s", exc)
                            if assistant_content_acc:
                                try:
                                    await _persist_final_assistant_message(
                                        graph_inst,
                                        approve_config,
                                        session_id,
                                        prefer_text=assistant_content_acc,
                                        activity=activity_log,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "[WS-Chat] Failed to persist partial assistant on approve error: %s",
                                        e,
                                    )

                            err_msg = _friendly_graph_error(exc)
                            await send(
                                ApprovalEventBridge.create_approval_error_event(
                                    target_thread_id,
                                    error=err_msg,
                                    code="APPROVAL_FAILED",
                                    traceback_str=traceback.format_exc(),
                                    tool=tool_name,
                                    scope=scope,
                                )
                            )
                            await send(
                                TelemetryEvent(
                                    type="graph_error",
                                    data={"message": err_msg},
                                    thread_id=target_thread_id,
                                ).to_dict()
                            )
                            try:
                                await send(
                                    TelemetryEvent(
                                        type="llm_delta",
                                        data={"content": f"⚠️ {err_msg}"},
                                        thread_id=target_thread_id,
                                    ).to_dict()
                                )
                            except Exception:
                                pass
                            await send(
                                EventBridge.create_idle_event(target_thread_id).to_dict()
                            )
                        finally:
                            reset_current_thread_id(tid_token)

                    # Guard against a cross-transport duplicate resume: if an
                    # HTTP/SSE approve (or another WS approve) already resumed
                    # this thread, starting a second ainvoke(resume) on the same
                    # checkpoint would double-execute the tool + interleave
                    # checkpoint writes. SSE guards this; the WS path did not
                    # (audit finding).
                    if is_turn_running(target_thread_id):
                        await send({"type": "error", "code": "TURN_BUSY",
                                    "thread_id": target_thread_id,
                                    "message": "A turn is already running for this thread"})
                        continue
                    if active_task and not active_task.done():
                        active_task.cancel()
                    active_task = asyncio.create_task(_run_approve_stream())
                    register_turn(target_thread_id, active_task)
                    active_task.add_done_callback(
                        lambda t: unregister_turn(target_thread_id, t)
                    )

        except WebSocketDisconnect:
            logger.info("[WS-Chat] Client disconnected: session_id=%s (graph keeps running in background)", session_id)
        except Exception as exc:
            logger.exception("[WS-Chat] WebSocket error: %s", exc)
        finally:
            # Only unbind if THIS socket is still the live one — a newer
            # reconnect may already own delivery for this thread.
            try:
                unbind_live_socket(thread_id, websocket)
            except Exception:
                logger.debug(
                    "[WS-Chat] unbind_live_socket failed session=%s",
                    session_id[:12] if session_id else "?",
                    exc_info=True,
                )
            # DETACHED: do NOT cancel active_task. The graph keeps running in
            # the background; the checkpointer + SessionStore persist the
            # result. Live rebind (above) + client poll pick it up on reconnect.
            if active_task and not active_task.done():
                logger.info("[WS-Chat] Detached stream task continues in background for session=%s", session_id)
                # Stamp orphan only when no replacement socket is live —
                # otherwise a race (new conn accepted before old finally)
                # would re-arm the TTL while the user is watching.
                try:
                    if get_live_socket(thread_id) is None:
                        mark_turn_orphaned(thread_id)
                except Exception:
                    pass

    return router


# Default singleton instance for simple router registration
ws_chat_router = create_ws_chat_router()
