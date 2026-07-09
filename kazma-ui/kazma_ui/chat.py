"""Chat routes and deprecated WebSocket handler for the Kazma WebUI.

Primary chat transport is SSE at ``/api/chat/stream`` (full graph HITL).
WebSocket ``/ws/chat`` returns 410 Gone and must not execute tools.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, WebSocket
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

    # Session management endpoints — same format as sse_chat.py for
    # cross-transport consistency (VAL-UX-007). Both use the shared
    # SessionManager singleton so sessions are visible across transports.
    @r.get("/api/chat/sessions")
    async def api_list_sessions() -> list[dict[str, Any]]:
        """List all chat sessions (shared store)."""
        return [s.to_summary() for s in _sessions().list_all()]

    @r.get("/api/chat/sessions/{session_id}/messages")
    async def api_session_messages(session_id: str) -> list[dict[str, Any]]:
        """Get messages for a session (shared store, role/content only)."""
        session = _sessions().get(session_id)
        if not session:
            return []
        return [
            {"role": msg.get("role", "user"), "content": msg.get("content", "")}
            for msg in session.messages
        ]

    @r.delete("/api/chat/sessions/{session_id}")
    async def api_delete_session(session_id: str) -> dict[str, str]:
        """Delete a chat session (shared store)."""
        _sessions().delete(session_id)
        return {"status": "ok"}

    return r


# ── WebSocket handler (deprecated) ────────────────────────────────────


async def chat_websocket_handler(websocket: WebSocket, agent: KazmaAgent) -> None:
    """Deprecated WebSocket chat — always closes with 410 Gone.

    This path historically bypassed LangGraph ``interrupt()`` HITL.
    Clients must use SSE ``/api/chat/stream`` for full Mechanism A approval.
    """
    del agent  # unused; signature kept for call-site compatibility
    warnings.warn(
        "WebSocket /chat is deprecated. Use SSE /api/chat/stream for full HITL safety.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Code 4100 is application-specific "gone / use SSE"
    await websocket.close(
        code=4100,
        reason="Deprecated: Use /api/chat/stream for full HITL support",
    )
