"""Telegram Webhook Bridge — FastAPI router for secure, async message ingestion.

This module replaces long-polling with a webhook-based architecture.
When a Telegram user sends a message, Telegram POSTs the update to
Kazma's webhook endpoint. The bridge validates the request, maps the
Telegram chat_id to a Kazma session (thread_id), and queues the message
for processing by the LangGraph agent loop.

Architecture
════════════

    Telegram Server
         │
         │  HTTPS POST /api/webhooks/telegram/{bot_token}
         ▼
    ┌──────────────────────┐
    │  Token Validation     │ ← os.environ["TELEGRAM_BOT_TOKEN"]
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Payload Parsing      │ ← Extract chat_id, username, text
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Session Mapping      │ ← chat_id → thread_id (in-memory dict)
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │  200 OK (< 500ms)     │ ← Respond immediately to avoid webhook timeout
    └──────────┬───────────┘
               │
               ▼  (background task)
    ┌──────────────────────┐
    │  Graph Invocation     │ ← LangGraph supervisor processes the message
    └──────────┬───────────┘
               │
               ▼
    ┌──────────────────────┐
    │  Response Delivery    │ ← send_telegram_message tool sends reply
    └──────────────────────┘

Session Mapping
═══════════════
Telegram chat IDs are integers. Kazma uses UUID-based thread_id strings
for LangGraph checkpointing. The bridge maintains a bidirectional mapping:

    _telegram_sessions: dict[int, str]   # chat_id → thread_id
    _thread_to_chat: dict[str, int]      # thread_id → chat_id

When a new chat_id appears, a UUID is generated and stored. When the
same chat_id sends another message, the existing thread_id is reused,
preserving conversation history in the SQLite checkpoint database.

Usage
─────

    import os
    from fastapi import FastAPI
    from kazma_comms.telegram_bridge import create_telegram_webhook_router

    # Set your token
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:ABC-DEF1234..."

    # Create the router (pass your compiled graph + session store)
    app = FastAPI()
    router = create_telegram_webhook_router(graph=my_graph)
    app.include_router(router)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# Session mapping — maps Telegram chat_id ↔ Kazma thread_id
# ══════════════════════════════════════════════════════════════════════════

# chat_id → thread_id
_telegram_sessions: dict[int, str] = {}

# thread_id → chat_id (reverse lookup for sending responses)
_thread_to_chat: dict[str, int] = {}

# thread_id → list of messages (for the ChatSession compatibility layer)
_thread_messages: dict[str, list[dict[str, Any]]] = {}


def _resolve_session(chat_id: int, username: str = "") -> tuple[str, bool]:
    """Resolve or create a Kazma session for a Telegram chat.

    Args:
        chat_id: The Telegram chat ID.
        username: Optional username / first_name for attribution.

    Returns:
        (thread_id, is_new) — thread_id and whether the session is fresh.
    """
    if chat_id in _telegram_sessions:
        return _telegram_sessions[chat_id], False

    # New session — generate a stable thread_id
    thread_id = str(uuid.uuid4())
    _telegram_sessions[chat_id] = thread_id
    _thread_to_chat[thread_id] = chat_id
    _thread_messages[thread_id] = []

    logger.info(
        "[TelegramBridge] New session: chat_id=%d → thread_id=%s (user=%s)",
        chat_id,
        thread_id,
        username or "unknown",
    )
    return thread_id, True


def _get_chat_id(thread_id: str) -> int | None:
    """Reverse-lookup Telegram chat_id from a Kazma thread_id."""
    return _thread_to_chat.get(thread_id)


def _get_session_messages(thread_id: str) -> list[dict[str, Any]]:
    """Get the message list for a thread, creating it if needed."""
    if thread_id not in _thread_messages:
        _thread_messages[thread_id] = []
    return _thread_messages[thread_id]


# ══════════════════════════════════════════════════════════════════════════
# Webhook payload parsing
# ══════════════════════════════════════════════════════════════════════════


class TelegramUpdate:
    """Normalized representation of a Telegram Update.

    Extracts the fields Kazma needs from the raw Telegram JSON payload.
    Handles both channel posts and direct messages.

    Attributes:
        chat_id: The Telegram chat ID (integer).
        username: Best-effort user identifier (username, first_name, or 'unknown').
        text: Message text content (empty string if no text).
        update_id: The Telegram update_id for deduplication.
    """

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.message: dict[str, Any] = {}
        self._parse()

    def _parse(self) -> None:
        """Extract the message from various Update shapes."""
        # Telegram sends updates in several shapes:
        #   {"update_id": N, "message": {...}}
        #   {"update_id": N, "channel_post": {...}}
        #   {"update_id": N, "edited_message": {...}}
        for key in ("message", "channel_post", "edited_message"):
            if key in self._raw:
                self.message = self._raw[key]
                break

    @property
    def update_id(self) -> int:
        return self._raw.get("update_id", 0)

    @property
    def chat_id(self) -> int:
        return self.message.get("chat", {}).get("id", 0)

    @property
    def chat_type(self) -> str:
        return self.message.get("chat", {}).get("type", "private")

    @property
    def username(self) -> str:
        """Best-effort user identifier."""
        from_user = self.message.get("from", {})
        return from_user.get("username", "") or from_user.get("first_name", "") or f"tg_{self.chat_id}"

    @property
    def text(self) -> str:
        """Message text. Returns empty string for non-text messages."""
        text = self.message.get("text", "")
        if not text:
            text = self.message.get("caption", "")
        return text.strip() if text else ""

    @property
    def message_id(self) -> int:
        return self.message.get("message_id", 0)

    @property
    def is_command(self) -> bool:
        """True if the message starts with a bot command (e.g. /start)."""
        return self.text.startswith("/") if self.text else False


# ══════════════════════════════════════════════════════════════════════════
# Background message processor
# ══════════════════════════════════════════════════════════════════════════


async def _process_message(
    update: TelegramUpdate,
    thread_id: str,
    graph: Any,
    system_prompt: str = "",
) -> None:
    """Process an incoming Telegram message through the LangGraph agent.

    This runs in the background after the webhook responds 200 OK.
    The function:
      1. Builds the initial SupervisorState with the user's message.
      2. Invokes the compiled graph with the thread_id checkpoint config.
      3. Extracts the assistant's response from the final state.
      4. Sends the response back to Telegram via the send_telegram_message tool.

    Args:
        update: The parsed TelegramUpdate.
        thread_id: Kazma session (thread_id) for checkpointing.
        graph: The compiled LangGraph graph (from build_supervisor_graph).
        system_prompt: System prompt for the agent.
    """
    try:
        logger.info(
            "[TelegramBridge] Processing message from chat_id=%d (thread=%s): %.100s",
            update.chat_id,
            thread_id,
            update.text,
        )

        # ── Build the user message ──────────────────────────────────
        user_msg: dict[str, Any] = {"role": "user", "content": update.text}

        # Attach Telegram metadata so the agent knows the source
        user_msg["_telegram"] = {
            "chat_id": str(update.chat_id),
            "username": update.username,
            "message_id": update.message_id,
        }

        # ── Build initial state ─────────────────────────────────────
        from kazma_core.agent.state import initial_supervisor_state

        state = initial_supervisor_state(thread_id=thread_id)
        state["messages"] = [user_msg]

        # ── Invoke the graph ────────────────────────────────────────
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

        start = time.monotonic()
        result_state = await graph.ainvoke(state, config)
        duration_ms = (time.monotonic() - start) * 1000

        messages = result_state.get("messages", [])

        logger.info(
            "[TelegramBridge] Graph completed in %.0fms (thread=%s, messages=%d)",
            duration_ms,
            thread_id,
            len(messages),
        )

        # ── Extract assistant response ──────────────────────────────
        # The last assistant message contains the response text
        assistant_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                assistant_text = msg["content"]
                break

        if not assistant_text:
            assistant_text = "تمت معالجة رسالتك. (لم يتم إنتاج رد نصي)"

        # ── Send response back to Telegram ──────────────────────────
        from kazma_core.tools.telegram_tools import send_telegram_message

        chat_id_str = str(update.chat_id)
        result = await send_telegram_message(chat_id=chat_id_str, text=assistant_text)
        logger.info("[TelegramBridge] Response sent to chat_id=%d: %s", update.chat_id, result[:100])

        # ── Store messages in session ──────────────────────────────
        session_msgs = _get_session_messages(thread_id)
        session_msgs.append(user_msg)
        session_msgs.append({"role": "assistant", "content": assistant_text})

    except Exception as exc:
        logger.exception("[TelegramBridge] Background processing failed for chat_id=%d", update.chat_id)
        # Attempt to send error message to user
        try:
            from kazma_core.tools.telegram_tools import send_telegram_message

            await send_telegram_message(
                chat_id=str(update.chat_id),
                text=f"⚠️ عذراً، حدث خطأ أثناء معالجة رسالتك: {exc}",
            )
        except Exception:
            logger.error("[TelegramBridge] Failed to send error message to chat_id=%d", update.chat_id)


# ══════════════════════════════════════════════════════════════════════════
# Router factory
# ══════════════════════════════════════════════════════════════════════════


def create_telegram_webhook_router(
    *,
    graph: Any,
    system_prompt: str = "",
    token: str | None = None,
) -> APIRouter:
    """Create a FastAPI router for the Telegram webhook endpoint.

    This follows the same factory pattern used by the Kazma UI routers
    (chat, settings, skills, etc.).

    Args:
        graph: The compiled LangGraph supervisor graph.
        system_prompt: System prompt injected into agent context.
        token: Bot token for validation. If None, reads TELEGRAM_BOT_TOKEN
               from the environment at call time (on each request).

    Returns:
        A FastAPI APIRouter ready for ``app.include_router()``.

    Endpoint
    ────────
        POST /api/webhooks/telegram/{bot_token}

    Example:
        >>> from kazma_comms.telegram_bridge import create_telegram_webhook_router
        >>> from fastapi import FastAPI
        >>> app = FastAPI()
        >>> router = create_telegram_webhook_router(graph=my_graph)
        >>> app.include_router(router)
    """
    router = APIRouter(prefix="/api/webhooks", tags=["telegram"])

    @router.post("/telegram/{bot_token}")
    async def handle_telegram_update(
        bot_token: str,
        request: Request,
    ) -> JSONResponse:
        """Handle an incoming Telegram webhook update.

        Validates the embedded bot token against the environment,
        parses the update, maps or creates a session, and hands the
        message off to the LangGraph agent for processing.

        Responds with 200 OK within 500ms to prevent Telegram webhook
        timeouts. Processing happens asynchronously in a background task.
        """
        # ── Token validation ────────────────────────────────────────
        expected_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not expected_token:
            logger.error("[TelegramBridge] TELEGRAM_BOT_TOKEN not set")
            raise HTTPException(status_code=500, detail="Server configuration error: bot token not set")

        if bot_token != expected_token:
            logger.warning("[TelegramBridge] Invalid token received (len=%d)", len(bot_token))
            raise HTTPException(status_code=403, detail="Forbidden: invalid bot token")

        # ── Parse payload ───────────────────────────────────────────
        start_time = time.monotonic()

        try:
            raw = await request.json()
        except Exception as exc:
            logger.warning("[TelegramBridge] Failed to parse JSON body: %s", exc)
            raise HTTPException(status_code=400, detail="Bad request: invalid JSON") from exc

        update = TelegramUpdate(raw)

        if not update.chat_id:
            logger.warning("[TelegramBridge] Update has no chat_id — ignoring")
            return JSONResponse(content={"status": "ignored", "reason": "no_chat_id"}, status_code=200)

        if not update.text:
            # Non-text messages (photos, stickers, etc.) — ack and ignore
            logger.info("[TelegramBridge] Non-text update from chat_id=%d (type=%s)", update.chat_id, update.chat_type)
            return JSONResponse(content={"status": "ignored", "reason": "no_text"}, status_code=200)

        # ── Session mapping ─────────────────────────────────────────
        thread_id, is_new = _resolve_session(update.chat_id, update.username)

        # ── Queue for background processing ─────────────────────────
        bg_task = asyncio.create_task(
            _process_message(
                update=update,
                thread_id=thread_id,
                graph=graph,
                system_prompt=system_prompt,
            )
        )

        # Optionally store the task reference for monitoring (not awaited)
        _ = bg_task  # prevent "coroutine not awaited" in static analysis

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "[TelegramBridge] Accepted update_id=%d from chat_id=%d (%.0fms) → thread=%s%s",
            update.update_id,
            update.chat_id,
            elapsed_ms,
            thread_id,
            " [NEW]" if is_new else "",
        )

        return JSONResponse(
            content={
                "status": "accepted",
                "thread_id": thread_id,
                "is_new_session": is_new,
            },
            status_code=200,
        )

    # ── Health check ────────────────────────────────────────────────────
    @router.get("/telegram/health")
    async def telegram_health() -> dict[str, Any]:
        """Health check endpoint for the Telegram webhook bridge."""
        sessions = len(_telegram_sessions)
        return {
            "status": "healthy",
            "provider": "telegram",
            "active_sessions": sessions,
            "token_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "")),
        }

    # ── Session introspection ───────────────────────────────────────────
    @router.get("/telegram/sessions")
    async def telegram_sessions() -> dict[str, Any]:
        """List active Telegram→Kazma session mappings."""
        return {
            "sessions": [
                {
                    "chat_id": chat_id,
                    "thread_id": thread_id,
                    "message_count": len(_get_session_messages(thread_id)),
                }
                for chat_id, thread_id in _telegram_sessions.items()
            ],
            "total": len(_telegram_sessions),
        }

    return router


# ══════════════════════════════════════════════════════════════════════════
# Session management helpers (public API)
# ══════════════════════════════════════════════════════════════════════════


def list_telegram_sessions() -> list[dict[str, Any]]:
    """Return all active Telegram session mappings."""
    return [
        {
            "chat_id": chat_id,
            "thread_id": thread_id,
            "message_count": len(_get_session_messages(thread_id)),
        }
        for chat_id, thread_id in _telegram_sessions.items()
    ]


def get_session_by_chat_id(chat_id: int) -> str | None:
    """Look up the thread_id for a Telegram chat_id, or None if not found."""
    return _telegram_sessions.get(chat_id)


def get_chat_id_for_thread(thread_id: str) -> int | None:
    """Reverse-lookup the Telegram chat_id for a Kazma thread_id."""
    return _thread_to_chat.get(thread_id)
