"""FastAPI WebSocket Gateway for Real-Time Chat Telemetry Bus.

Exposes `/ws/chat/{session_id}` to stream standardized JSON event frames from
LangGraph's `astream_events` directly into client stores (Alpine.js agentStore).
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
import uuid
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.types import Command

from kazma_core.tracing.events import EventBridge, TelemetryEvent
from kazma_ui.session_manager import get_session_manager

logger = logging.getLogger(__name__)

ws_chat_router = APIRouter(tags=["ws-chat"])

# Match sse_chat / agent_runner / gateway — LangGraph default (25) is too low.
_GRAPH_RECURSION_LIMIT = 100


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

    def _get_session_and_thread(session_id: str) -> tuple[Any, str]:
        store = get_session_manager()
        session = store.get_or_create(session_id)
        if session_id.startswith("gw-"):
            if session.thread_id != session_id:
                session.thread_id = session_id
                store.put(session)
        elif not session.thread_id:
            session.thread_id = str(uuid.uuid4())
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

    async def _backfill_assistant_text_if_needed(
        graph_inst: Any,
        config: dict[str, Any],
        websocket: WebSocket,
        thread_id: str,
        pre_msg_count: int = 0,
    ) -> None:
        """If no tokens were streamed (non-BaseChatModel LLM), backfill NEW assistant text from graph state."""
        try:
            snapshot = await graph_inst.aget_state(config)
            if snapshot is None:
                return
            vals = getattr(snapshot, "values", None) or {}
            msgs = vals.get("messages") if isinstance(vals, dict) else None
            if not msgs or not isinstance(msgs, list):
                return
            new_msgs = msgs[pre_msg_count:] if pre_msg_count < len(msgs) else []
            text = ""
            for m in reversed(new_msgs):
                role = None
                if isinstance(m, dict):
                    role = m.get("role")
                else:
                    role = getattr(m, "type", None) or getattr(m, "role", None)
                if role in ("assistant", "ai"):
                    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
                    if isinstance(content, str) and content.strip():
                        text = content
                        break
            if text:
                await websocket.send_json(
                    TelemetryEvent(
                        type="llm_delta",
                        data={"content": text},
                        thread_id=thread_id,
                    ).to_dict()
                )
        except Exception as exc:
            logger.debug("[WS-Chat] Text backfill failed: %s", exc)

    async def _persist_final_assistant_message(
        graph_inst: Any,
        config: dict[str, Any],
        session_id: str,
        *,
        pre_msg_count: int = 0,
        prefer_text: str = "",
    ) -> str:
        """Persist the latest *new* assistant text to SessionStore.

        Returns the text that was (or should have been) persisted so callers
        can also emit it over the wire. Prefer *prefer_text* when the stream
        already accumulated tokens; otherwise pull NEW messages after
        *pre_msg_count* from the checkpoint (avoids re-appending older turns).
        """
        text = (prefer_text or "").strip()
        try:
            if not text:
                snapshot = await graph_inst.aget_state(config)
                if snapshot is None:
                    return ""
                vals = getattr(snapshot, "values", None) or {}
                msgs = vals.get("messages") if isinstance(vals, dict) else None
                if not msgs or not isinstance(msgs, list):
                    return ""
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
            if not text:
                return ""
            store = get_session_manager()
            sess = store.get(session_id)
            if not sess:
                return text
            # Upsert trailing assistant bubble so incremental flushes don't
            # create duplicate rows for the same turn.
            if sess.messages and sess.messages[-1].get("role") == "assistant":
                sess.messages[-1]["content"] = text
            else:
                sess.add_message("assistant", text)
            store.put(sess)
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
        # LangGraph default recursion_limit is 25 — far too low for multi-tool
        # YOLO/smoke turns (each Supervisor↔ToolWorker hop burns steps).
        # Keep in lockstep with sse_chat / agent_runner / gateway (100).
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            },
            "recursion_limit": _GRAPH_RECURSION_LIMIT,
        }

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

                    # Record user message in SessionStore
                    try:
                        session.add_message("user", text)
                        get_session_manager().put(session)
                    except Exception as exc:
                        logger.warning("[WS-Chat] Failed writing user msg to SessionStore: %s", exc)

                    from kazma_core.agent.turn_input import build_turn_messages
                    from kazma_core.agent.state import initial_supervisor_state
                    from kazma_core.ide.env_context import build_env_context

                    env_block = build_env_context()
                    sys_msgs = [{"role": "system", "content": env_block}] if env_block else None
                    full_messages = await build_turn_messages(
                        graph_inst,
                        config,
                        user_text=text,
                        system_messages=sys_msgs,
                        fallback_history=session.messages,
                    )
                    # Stamp durable thread_id into state so YOLO/HITL grants
                    # resolve even if the ContextVar is lost mid-graph.
                    input_state = initial_supervisor_state(thread_id=thread_id)
                    input_state["messages"] = full_messages

                    async def _run_prompt_stream():
                        from kazma_core.safety.hitl import (
                            reset_current_thread_id,
                            set_current_thread_id,
                        )

                        assistant_content_acc = ""
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
                            assistant_msg_added = False

                            async for ev in EventBridge.process_stream(stream, thread_id=thread_id):
                                if ev.type == "llm_delta":
                                    tokens_emitted = True
                                    if hasattr(ev, "data") and isinstance(ev.data, dict):
                                        content = ev.data.get("content", "")
                                        if content:
                                            assistant_content_acc += content
                                            if len(assistant_content_acc) % 50 == 0:
                                                store = get_session_manager()
                                                sess = store.get(session_id)
                                                if sess:
                                                    if not assistant_msg_added:
                                                        sess.add_message(
                                                            "assistant", assistant_content_acc
                                                        )
                                                        assistant_msg_added = True
                                                    elif (
                                                        sess.messages
                                                        and sess.messages[-1].get("role")
                                                        == "assistant"
                                                    ):
                                                        sess.messages[-1]["content"] = (
                                                            assistant_content_acc
                                                        )
                                                    store.put(sess)

                                await websocket.send_json(ev.to_dict())

                            if not tokens_emitted:
                                await _backfill_assistant_text_if_needed(
                                    graph_inst, config, websocket, thread_id, pre_msg_count
                                )

                            await _persist_final_assistant_message(
                                graph_inst,
                                config,
                                session_id,
                                pre_msg_count=pre_msg_count,
                                prefer_text=assistant_content_acc,
                            )

                            interrupted = await _scan_and_emit_hitl_interrupt(
                                graph_inst, config, websocket, thread_id
                            )
                            # Always release the UI turn lock. HITL pause is
                            # signalled via pendingApproval; idle still ends
                            # the "generating" state so Stop never sticks.
                            # Emit both idle + stream_end — agentStore listens
                            # for either; double endTurn is a no-op.
                            await websocket.send_json(
                                EventBridge.create_idle_event(thread_id).to_dict()
                            )
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="stream_end",
                                    data={"interrupted": bool(interrupted)},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            if interrupted:
                                logger.info(
                                    "[WS-Chat] Prompt paused for HITL thread=%s", thread_id
                                )
                        except asyncio.CancelledError:
                            logger.info(
                                "[WS-Chat] Prompt stream cancelled for session=%s", session_id
                            )
                            if assistant_content_acc:
                                try:
                                    await _persist_final_assistant_message(
                                        graph_inst,
                                        config,
                                        session_id,
                                        prefer_text=assistant_content_acc,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "[WS-Chat] Failed to persist partial assistant on cancel: %s",
                                        e,
                                    )
                            raise
                        except Exception as exc:
                            logger.exception("[WS-Chat] Error in prompt stream: %s", exc)
                            if assistant_content_acc:
                                try:
                                    await _persist_final_assistant_message(
                                        graph_inst,
                                        config,
                                        session_id,
                                        prefer_text=assistant_content_acc,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "[WS-Chat] Failed to persist partial assistant on error: %s",
                                        e,
                                    )
                            err_msg = _friendly_graph_error(exc)
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="graph_error",
                                    data={"message": err_msg},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            # Always surface something in the transcript
                            try:
                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="llm_delta",
                                        data={"content": f"⚠️ {err_msg}"},
                                        thread_id=thread_id,
                                    ).to_dict()
                                )
                            except Exception:
                                pass
                            await websocket.send_json(
                                EventBridge.create_idle_event(thread_id).to_dict()
                            )
                            await websocket.send_json(
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
                        # Explicit non-session thread (e.g. gateway takeover)
                        target_thread_id = requested_tid

                    actor = f"ws:{(session_id or '')[:12] or 'anon'}"
                    approve_config: dict[str, Any] = {
                        "configurable": {
                            "thread_id": target_thread_id,
                            "checkpoint_ns": "",
                        },
                        "recursion_limit": _GRAPH_RECURSION_LIMIT,
                    }

                    from kazma_ui.sse_utils import ApprovalEventBridge
                    import time

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

                    resume_val = {"approved": approved, "scope": scope}
                    resume_command = Command(resume=resume_val)

                    await websocket.send_json(
                        ApprovalEventBridge.create_approval_started_event(
                            target_thread_id,
                            tool=tool_name,
                            scope=scope,
                            request_id=session_id[:12],
                        )
                    )
                    await websocket.send_json(
                        ApprovalEventBridge.create_approval_progress_event(
                            target_thread_id,
                            f"Preparing to execute {tool_name}...",
                            "preparing",
                            {"tool": tool_name, "scope": scope},
                        )
                    )

                    async def _run_approve_stream():
                        from kazma_core.safety.hitl import (
                            reset_current_thread_id,
                            set_current_thread_id,
                        )

                        assistant_content_acc = ""
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

                            await websocket.send_json(
                                ApprovalEventBridge.create_approval_resuming_event(
                                    target_thread_id,
                                    tool=tool_name,
                                    scope=scope,
                                )
                            )
                            await websocket.send_json(
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
                                    try:
                                        await websocket.send_json(
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
                                    except Exception:
                                        # Client gone — keep graph running so
                                        # checkpoint/session can still be updated.
                                        pass
                            await resume_task  # re-raise graph errors

                            # Surface assistant text (no live token stream on ainvoke)
                            await _backfill_assistant_text_if_needed(
                                graph_inst,
                                approve_config,
                                websocket,
                                target_thread_id,
                                pre_msg_count,
                            )
                            final_text = await _persist_final_assistant_message(
                                graph_inst,
                                approve_config,
                                session_id,
                                pre_msg_count=pre_msg_count,
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
                                try:
                                    await websocket.send_json(
                                        TelemetryEvent(
                                            type="llm_delta",
                                            data={"content": recovery},
                                            thread_id=target_thread_id,
                                        ).to_dict()
                                    )
                                except Exception:
                                    pass
                                await _persist_final_assistant_message(
                                    graph_inst,
                                    approve_config,
                                    session_id,
                                    pre_msg_count=pre_msg_count,
                                    prefer_text=recovery,
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
                            await websocket.send_json(
                                ApprovalEventBridge.create_approval_complete_event(
                                    target_thread_id,
                                    tool=tool_name,
                                    scope=scope,
                                    duration_ms=duration_ms,
                                )
                            )
                            # Always idle + stream_end — releases UI even if
                            # another HITL card is about to show.
                            await websocket.send_json(
                                EventBridge.create_idle_event(target_thread_id).to_dict()
                            )
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="stream_end",
                                    data={"interrupted": bool(interrupted)},
                                    thread_id=target_thread_id,
                                ).to_dict()
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
                                    )
                                except Exception as e:
                                    logger.warning(
                                        "[WS-Chat] Failed to persist partial assistant on approve error: %s",
                                        e,
                                    )

                            err_msg = _friendly_graph_error(exc)
                            await websocket.send_json(
                                ApprovalEventBridge.create_approval_error_event(
                                    target_thread_id,
                                    error=err_msg,
                                    code="APPROVAL_FAILED",
                                    traceback_str=traceback.format_exc(),
                                    tool=tool_name,
                                    scope=scope,
                                )
                            )
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="graph_error",
                                    data={"message": err_msg},
                                    thread_id=target_thread_id,
                                ).to_dict()
                            )
                            try:
                                await websocket.send_json(
                                    TelemetryEvent(
                                        type="llm_delta",
                                        data={"content": f"⚠️ {err_msg}"},
                                        thread_id=target_thread_id,
                                    ).to_dict()
                                )
                            except Exception:
                                pass
                            await websocket.send_json(
                                EventBridge.create_idle_event(target_thread_id).to_dict()
                            )
                        finally:
                            reset_current_thread_id(tid_token)

                    if active_task and not active_task.done():
                        active_task.cancel()
                    active_task = asyncio.create_task(_run_approve_stream())

        except WebSocketDisconnect:
            logger.info("[WS-Chat] Client disconnected: session_id=%s", session_id)
        except Exception as exc:
            logger.exception("[WS-Chat] WebSocket error: %s", exc)
        finally:
            if active_task and not active_task.done():
                active_task.cancel()
                logger.info("[WS-Chat] Cancelled running stream task on teardown for session=%s", session_id)

    return router


# Default singleton instance for simple router registration
ws_chat_router = create_ws_chat_router()
