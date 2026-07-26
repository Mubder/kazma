"""FastAPI WebSocket Gateway for Real-Time Chat Telemetry Bus.

Exposes `/ws/chat/{session_id}` to stream standardized JSON event frames from
LangGraph's `astream_events` directly into client stores (Alpine.js agentStore).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.types import Command

from kazma_core.tracing.events import EventBridge, TelemetryEvent
from kazma_ui.session_manager import get_session_manager

logger = logging.getLogger(__name__)

ws_chat_router = APIRouter(tags=["ws-chat"])


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
    ) -> None:
        """If no tokens were streamed (non-BaseChatModel LLM), backfill assistant text from graph state."""
        try:
            snapshot = await graph_inst.aget_state(config)
            if snapshot is None:
                return
            vals = getattr(snapshot, "values", None) or {}
            msgs = vals.get("messages") if isinstance(vals, dict) else None
            if not msgs or not isinstance(msgs, list):
                return
            text = ""
            for m in reversed(msgs):
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
        config: dict[str, Any] = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            }
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
                        if hasattr(session, "add_message"):
                            session.add_message("user", text)
                        elif hasattr(session, "messages") and isinstance(session.messages, list):
                            session.messages.append({"role": "user", "content": text})
                        get_session_manager().put(session)
                    except Exception as exc:
                        logger.warning("[WS-Chat] Failed writing user msg to SessionStore: %s", exc)

                    input_state = {"messages": [("user", text)]}

                    async def _run_prompt_stream():
                        try:
                            stream = graph_inst.astream_events(
                                input_state, config=config, version="v2"
                            )
                            tokens_emitted = False
                            async for ev in EventBridge.process_stream(stream, thread_id=thread_id):
                                if ev.type == "llm_delta":
                                    tokens_emitted = True
                                await websocket.send_json(ev.to_dict())

                            if not tokens_emitted:
                                await _backfill_assistant_text_if_needed(
                                    graph_inst, config, websocket, thread_id
                                )

                            interrupted = await _scan_and_emit_hitl_interrupt(
                                graph_inst, config, websocket, thread_id
                            )
                            if not interrupted:
                                await websocket.send_json(
                                    EventBridge.create_idle_event(thread_id).to_dict()
                                )
                        except asyncio.CancelledError:
                            logger.info("[WS-Chat] Prompt stream cancelled for session=%s", session_id)
                            raise
                        except Exception as exc:
                            logger.exception("[WS-Chat] Error in prompt stream: %s", exc)
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="graph_error",
                                    data={"message": str(exc)},
                                    thread_id=thread_id,
                                ).to_dict()
                            )
                            await websocket.send_json(
                                EventBridge.create_idle_event(thread_id).to_dict()
                            )

                    if active_task and not active_task.done():
                        active_task.cancel()
                    active_task = asyncio.create_task(_run_prompt_stream())

                # ── Action 2: approve_tool ───────────────────────────────
                elif action == "approve_tool":
                    approved = bool(payload.get("approved", True))
                    scope = str(payload.get("scope") or "once").strip().lower()
                    target_thread_id = str(payload.get("thread_id") or thread_id)

                    # Persist scope grants (yolo / per-tool) so future turns skip HITL
                    actor = f"ws:{(session_id or '')[:12] or 'anon'}"
                    if approved and scope == "yolo":
                        try:
                            from kazma_core.safety.yolo import enable_yolo
                            enable_yolo(target_thread_id, actor=actor)
                        except Exception as exc:
                            logger.warning("[WS-Chat] Failed to enable YOLO scope: %s", exc)
                    elif approved and scope in ("tool", "allow_tool"):
                        try:
                            from kazma_core.safety.hitl_grants import grant_tool
                            tname = payload.get("tool") or payload.get("grant_tool")
                            if tname:
                                grant_tool(target_thread_id, str(tname), actor=actor)
                        except Exception as exc:
                            logger.warning("[WS-Chat] Failed to apply tool grant: %s", exc)

                    approve_config: dict[str, Any] = {
                        "configurable": {
                            "thread_id": target_thread_id,
                            "checkpoint_ns": "",
                        }
                    }

                    resume_val = {"approved": approved, "scope": scope}
                    resume_command = Command(resume=resume_val)

                    async def _run_approve_stream():
                        try:
                            stream = graph_inst.astream_events(
                                resume_command, config=approve_config, version="v2"
                            )
                            tokens_emitted = False
                            async for ev in EventBridge.process_stream(stream, thread_id=target_thread_id):
                                if ev.type == "llm_delta":
                                    tokens_emitted = True
                                await websocket.send_json(ev.to_dict())

                            if not tokens_emitted:
                                await _backfill_assistant_text_if_needed(
                                    graph_inst, approve_config, websocket, target_thread_id
                                )

                            interrupted = await _scan_and_emit_hitl_interrupt(
                                graph_inst, approve_config, websocket, target_thread_id
                            )
                            if not interrupted:
                                await websocket.send_json(
                                    EventBridge.create_idle_event(target_thread_id).to_dict()
                                )
                        except asyncio.CancelledError:
                            logger.info("[WS-Chat] Approve stream cancelled for session=%s", session_id)
                            raise
                        except Exception as exc:
                            logger.exception("[WS-Chat] Error in approve stream: %s", exc)
                            await websocket.send_json(
                                TelemetryEvent(
                                    type="graph_error",
                                    data={"message": str(exc)},
                                    thread_id=target_thread_id,
                                ).to_dict()
                            )
                            await websocket.send_json(
                                EventBridge.create_idle_event(target_thread_id).to_dict()
                            )

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
