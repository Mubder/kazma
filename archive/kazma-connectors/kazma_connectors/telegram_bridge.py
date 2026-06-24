"""Telegram Webhook Bridge — Routes Telegram messages through Kazma's LangGraph supervisor.

Accepts incoming Telegram webhook POSTs, feeds them through the compiled
Supervisor graph, and returns the response as a Telegram sendMessage call.

Usage:
    router = create_telegram_webhook_router(graph=compiled_graph, token="BOT_TOKEN")
    app.include_router(router)

Endpoints:
    POST /api/telegram/webhook   — Telegram Bot API webhook receiver
    GET  /api/telegram/health    — Health check
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kazma_core.agent.state import initial_supervisor_state

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"


def create_telegram_webhook_router(
    graph: Any,
    token: str = "",
    system_prompt: str = "",
    cost_breaker: Any = None,
    authority: Any = None,
    tracer: Any = None,
) -> APIRouter:
    """Create the Telegram webhook router.

    Args:
        graph: Compiled LangGraph app (Supervisor graph).
        token: Telegram Bot API token (from env or config).
        system_prompt: System prompt for the agent.
        cost_breaker: CostCircuitBreaker instance.
        authority: ContextAuthority for 80% compaction.
        tracer: KazmaTracer for observability.

    Returns:
        APIRouter with POST /api/telegram/webhook.
    """
    r = APIRouter(tags=["telegram"])

    # Per-chat session tracking
    _sessions: dict[int, dict[str, Any]] = {}

    # Resolve token from env if not provided
    if not token:
        import os

        token = os.getenv("TELEGRAM_BOT_TOKEN", "")

    api_base = _TELEGRAM_API.format(token=token) if token else ""

    async def _send_message(chat_id: int, text: str) -> bool:
        """Send a message back to Telegram."""
        if not api_base:
            logger.error("No Telegram bot token configured")
            return False

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{api_base}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text[:4096],  # Telegram limit
                        "parse_mode": "Markdown",
                    },
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    @r.get("/api/telegram/health")
    async def telegram_health() -> dict[str, Any]:
        """Health check for the Telegram bridge."""
        return {
            "status": "ok",
            "configured": bool(token),
            "active_sessions": len(_sessions),
        }

    @r.post("/api/telegram/webhook")
    async def telegram_webhook(request: Request) -> JSONResponse:
        """Handle incoming Telegram webhook updates.

        Telegram sends updates as JSON POST bodies. We extract the
        message text, feed it through the LangGraph Supervisor, and
        send the response back via the Telegram Bot API.
        """
        try:
            update = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        # Extract message
        message = update.get("message")
        if not message:
            # Ignore non-message updates (edited, callback, etc.)
            return JSONResponse({"ok": True})

        chat_id = message.get("chat", {}).get("id")
        user_text = message.get("text", "").strip()

        if not chat_id or not user_text:
            return JSONResponse({"ok": True})

        logger.info("Telegram: chat=%d text=%s", chat_id, user_text[:100])

        # Get or create session
        if chat_id not in _sessions:
            _sessions[chat_id] = {
                "thread_id": f"telegram-{chat_id}",
                "messages": [],
            }
        session = _sessions[chat_id]

        # Cost breaker gate
        if cost_breaker and cost_breaker.should_halt():
            await _send_message(chat_id, "⚠️ ميزانية الجلسة انتهت. (Budget exceeded)")
            return JSONResponse({"ok": True})

        if cost_breaker:
            cost_breaker.record_user_interaction()

        # Build messages
        session["messages"].append({"role": "user", "content": user_text})
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(session["messages"])

        # Build state
        input_state = initial_supervisor_state(thread_id=session["thread_id"])
        input_state["messages"] = messages

        graph_config = {
            "configurable": {
                "thread_id": session["thread_id"],
                "checkpoint_ns": "",
            },
        }

        # Run through graph
        try:
            response_text = ""
            async for event in graph.astream_events(input_state, config=graph_config, version="v2"):
                kind = event.get("event", "")
                data = event.get("data", {})

                if kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk and hasattr(chunk, "content"):
                        response_text += chunk.content or ""

            if not response_text:
                response_text = "(No response)"

            # Store and send
            session["messages"].append({"role": "assistant", "content": response_text})
            await _send_message(chat_id, response_text)

        except Exception as exc:
            logger.error("Telegram agent error: %s", exc, exc_info=True)
            await _send_message(chat_id, f"⚠️ Error: {str(exc)[:200]}")

        return JSONResponse({"ok": True})

    return r
