"""Chat WebSocket handler and routes for the Kazma WebUI.

Provides real-time streaming chat over WebSocket with token-by-token
response delivery, tool call visualization, and session management.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kazma_ui.session_manager import ChatSession, SessionManager, get_session_manager

if TYPE_CHECKING:
    from kazma_core.agent import KazmaAgent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# ── Session management ────────────────────────────────────────────────
#
# Both the WebSocket handler (this module) and the SSE handler
# (sse_chat.py) use the shared SessionManager singleton so a session
# created on one transport is immediately visible to the other.
# See VAL-UX-007 for the contract this satisfies.


def _sessions() -> SessionManager:
    """Return the current shared SessionManager singleton.

    Resolved at call time (not import time) so test resets via
    ``reset_session_manager()`` are immediately reflected.
    """
    return get_session_manager()


def get_or_create_session(session_id: str | None = None) -> ChatSession:
    """Get an existing session or create a new one."""
    return _sessions().get_or_create(session_id)


def list_sessions() -> list[ChatSession]:
    """List all active sessions."""
    return _sessions().list_all()


# ── Routes ────────────────────────────────────────────────────────────


def create_chat_router(agent: KazmaAgent, templates: Jinja2Templates) -> APIRouter:
    """Create the chat router with agent and templates wired in."""

    r = APIRouter(tags=["chat"])

    @r.get("/chat", response_class=HTMLResponse)
    async def chat_page(request: Request) -> HTMLResponse:
        """Render the chat page."""
        return templates.TemplateResponse(
            request,
            "chat.html",
            {
                "config": agent.config,
                "sessions": list_sessions(),
            },
        )

    @r.get("/api/chat/sessions")
    async def api_list_sessions() -> list[dict[str, Any]]:
        """List all chat sessions."""
        return [
            {
                "session_id": s.session_id,
                "message_count": len(s.messages),
                "created_at": s.created_at,
                "total_cost": s.total_cost,
            }
            for s in list_sessions()
        ]

    @r.get("/api/chat/sessions/{session_id}/messages")
    async def api_session_messages(session_id: str) -> list[dict[str, Any]]:
        """Get messages for a session."""
        session = _sessions().get(session_id)
        if not session:
            return []
        return session.messages

    @r.delete("/api/chat/sessions/{session_id}")
    async def api_delete_session(session_id: str) -> dict[str, str]:
        """Delete a chat session."""
        _sessions().delete(session_id)
        return {"status": "ok"}

    return r


# ── WebSocket handler ─────────────────────────────────────────────────


async def chat_websocket_handler(websocket: WebSocket, agent: KazmaAgent) -> None:
    """Handle WebSocket chat connections with streaming responses."""
    from kazma_core.streaming import stream_chat

    # Use the agent's facade method to get LLM config as a plain dict,
    # avoiding direct access to agent.llm_config.* attributes.
    llm_cfg = agent.get_llm_config()

    await websocket.accept()
    session_id = str(uuid.uuid4())
    session = get_or_create_session(session_id)
    logger.info("WebSocket connected: session=%s", session_id)

    # Send session ID to client
    await websocket.send_json({"type": "session", "session_id": session_id})

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "content": "Invalid JSON"})
                continue

            msg_type = data.get("type", "message")

            if msg_type == "message":
                user_input = data.get("content", "").strip()
                if not user_input:
                    continue

                # Resolve model override from client, fall back to config default
                active_model = data.get("model", "") or llm_cfg["model"]
                # Resolve base_url — client can send it, otherwise stay on default
                active_base_url: str | None = data.get("base_url") or None

                # Store user message
                user_msg = {
                    "role": "user",
                    "content": user_input,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                session.messages.append(user_msg)

                # Build messages for LLM
                messages: list[dict[str, Any]] = []
                has_system = any(m.get("role") == "system" for m in session.messages)
                if not has_system:
                    messages.append({"role": "system", "content": agent.system_prompt})
                messages.extend({k: v for k, v in m.items() if k in ("role", "content")} for m in session.messages)

                # Get tool definitions
                tool_defs = agent.tools.get_tool_definitions()

                # Check cost breaker
                agent.cost_breaker.record_user_interaction()
                if agent.cost_breaker.should_halt():
                    await websocket.send_json(
                        {
                            "type": "error",
                            "content": "⚠️ ميزانية الجلسة انتهت. (Budget exceeded)",
                        }
                    )
                    continue

                # Stream response
                await websocket.send_json({"type": "thinking", "content": "Analyzing..."})

                assistant_content = ""
                tool_calls_executed: list[dict[str, Any]] = []

                # Determine the endpoint: use client-provided base_url, or resolve
                # from model prefix, or stay on config default
                chat_base_url = llm_cfg["base_url"]
                if active_base_url:
                    chat_base_url = active_base_url
                elif active_model != llm_cfg["model"] and "/" in active_model:
                    from kazma_core.models.discovery import get_model_base_url

                    resolved = await get_model_base_url(active_model)
                    if resolved:
                        chat_base_url = resolved

                # ── FORCE SANITIZATION ──
                # Ensure base_url ends with /v1 and prepend scheme if bare
                if chat_base_url and chat_base_url != llm_cfg["base_url"]:
                    chat_base_url = chat_base_url.rstrip("/")
                    if not chat_base_url.startswith("http"):
                        chat_base_url = "http://" + chat_base_url
                    if not chat_base_url.endswith("/v1"):
                        chat_base_url += "/v1"

                # If using a custom endpoint, force openai/ model prefix and dummy key
                chat_model = active_model
                chat_api_key = llm_cfg["api_key"]
                if chat_base_url and chat_base_url != llm_cfg["base_url"]:
                    if not chat_model.startswith("openai/"):
                        chat_model = f"openai/{chat_model}"
                    chat_api_key = "sk-local-dev"  # Prevent cloud fallback

                # Fallback to LM Studio if base_url is empty but model is local
                if not chat_base_url or chat_base_url == llm_cfg["base_url"]:
                    if active_model and not active_model.startswith("openai/"):
                        chat_base_url = "http://127.0.0.1:1234/v1"
                        chat_model = f"openai/{active_model}"
                        chat_api_key = "sk-local-dev"

                async for event in stream_chat(
                    client=await agent.get_llm_client(),
                    model=chat_model,
                    base_url=chat_base_url,
                    api_key=chat_api_key,
                    messages=messages,
                    tools=tool_defs if tool_defs else None,
                    max_tokens=llm_cfg["max_tokens"],
                    temperature=llm_cfg["temperature"],
                    input_cost_per_1m=llm_cfg["input_cost_per_1m"],
                    output_cost_per_1m=llm_cfg["output_cost_per_1m"],
                ):
                    if event.type == "token":
                        assistant_content += event.content
                        await websocket.send_json(
                            {
                                "type": "token",
                                "content": event.content,
                            }
                        )

                    elif event.type == "tool_call":
                        await websocket.send_json(
                            {
                                "type": "tool_call",
                                "name": event.tool_call_name,
                                "args": event.tool_call_args,
                            }
                        )

                        # Execute the tool
                        try:
                            args = json.loads(event.tool_call_args) if event.tool_call_args else {}
                        except json.JSONDecodeError:
                            args = {}

                        result = await agent.tools.execute(event.tool_call_name, args)
                        result_content = result.get("content", "")

                        tool_calls_executed.append(
                            {
                                "name": event.tool_call_name,
                                "args": args,
                                "result": result_content,
                            }
                        )

                        await websocket.send_json(
                            {
                                "type": "tool_result",
                                "name": event.tool_call_name,
                                "result": result_content[:500],
                            }
                        )

                        # Add tool result to messages for next iteration
                        messages.append(
                            {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": event.tool_call_id,
                                        "type": "function",
                                        "function": {
                                            "name": event.tool_call_name,
                                            "arguments": event.tool_call_args,
                                        },
                                    }
                                ],
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": event.tool_call_id,
                                "content": result_content,
                            }
                        )

                    elif event.type == "done":
                        session.total_cost += event.cost_usd
                        session.total_tokens += event.usage.get("total_tokens", 0)

                    elif event.type == "error":
                        assistant_content = f"عذراً، حدث خطأ: {event.content}"
                        await websocket.send_json(
                            {
                                "type": "token",
                                "content": assistant_content,
                            }
                        )

                # If tool calls were executed, do a follow-up LLM call
                if tool_calls_executed:
                    try:
                        from kazma_core.streaming import stream_chat as _stream

                        async for followup in _stream(
                            client=await agent.get_llm_client(),
                            model=chat_model,
                            base_url=chat_base_url,
                            api_key=chat_api_key,
                            messages=messages,
                            max_tokens=llm_cfg["max_tokens"],
                            temperature=llm_cfg["temperature"],
                            input_cost_per_1m=llm_cfg["input_cost_per_1m"],
                            output_cost_per_1m=llm_cfg["output_cost_per_1m"],
                        ):
                            if followup.type == "token":
                                assistant_content += followup.content
                                await websocket.send_json(
                                    {
                                        "type": "token",
                                        "content": followup.content,
                                    }
                                )
                            elif followup.type == "done":
                                session.total_cost += followup.cost_usd
                                session.total_tokens += followup.usage.get("total_tokens", 0)
                    except Exception as e:
                        logger.error("Follow-up LLM call failed: %s", e)

                # Store assistant message
                assistant_msg = {
                    "role": "assistant",
                    "content": assistant_content,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "tool_calls": tool_calls_executed,
                }
                session.messages.append(assistant_msg)

                # Send done event
                await websocket.send_json(
                    {
                        "type": "done",
                        "message_id": str(uuid.uuid4()),
                        "cost": session.total_cost,
                        "tokens": session.total_tokens,
                    }
                )

            elif msg_type == "clear":
                session.messages.clear()
                session.total_cost = 0.0
                session.total_tokens = 0
                await websocket.send_json({"type": "session", "session_id": session_id})

            elif msg_type == "new_session":
                # Create an entirely new session with a fresh UUID
                session_id = str(uuid.uuid4())
                session = get_or_create_session(session_id)
                logger.info("New session created: %s", session_id)
                await websocket.send_json({"type": "session", "session_id": session_id})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session=%s", session_id)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
