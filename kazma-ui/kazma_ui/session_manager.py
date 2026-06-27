"""Unified session store shared by WebSocket and SSE transports.

Both ``chat.py`` (WebSocket) and ``sse_chat.py`` (SSE) historically
maintained their own module-level ``_sessions`` dict, so a session
created via one transport was invisible to the other.  This module
provides a single :class:`SessionManager` that both transports read
from and write to, backed by one process-wide instance returned by
:func:`get_session_manager`.

The :class:`ChatSession` dataclass is also re-exported here so callers
can import it from one canonical location.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "ChatSession",
    "SessionManager",
    "get_session_manager",
]


@dataclass
class ChatSession:
    """A chat session with message history.

    Originally defined in ``chat.py``; moved here so both transports
    share the same canonical data model.
    """

    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    total_cost: float = 0.0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    def to_summary(self) -> dict[str, Any]:
        """Return a serializable summary used by the session-list API."""
        return {
            "session_id": self.session_id,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "total_cost": self.total_cost,
        }


class SessionManager:
    """In-memory session store shared across transports.

    Both WebSocket and SSE handlers obtain the singleton via
    :func:`get_session_manager` so a session created on one transport
    is immediately visible to the other.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}

    # ── core CRUD ──────────────────────────────────────────────────

    def get(self, session_id: str) -> ChatSession | None:
        """Return the session for ``session_id`` or ``None``."""
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None = None) -> ChatSession:
        """Get an existing session or create a new one.

        If ``session_id`` is ``None`` a fresh UUID is generated.  If a
        session with the given ID already exists it is returned as-is.
        """
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        sid = session_id or str(uuid.uuid4())
        session = ChatSession(session_id=sid)
        self._sessions[sid] = session
        return session

    def put(self, session: ChatSession) -> None:
        """Insert or replace a session in the store."""
        self._sessions[session.session_id] = session

    def delete(self, session_id: str) -> None:
        """Remove a session.  No-op if not found."""
        self._sessions.pop(session_id, None)

    def list_all(self) -> list[ChatSession]:
        """Return all sessions."""
        return list(self._sessions.values())

    # ── convenience helpers used by SSE transport ──────────────────

    def update_from_dict(self, session_id: str, data: dict[str, Any]) -> ChatSession:
        """Mirror a dict-based session (SSE format) into the shared store.

        The SSE transport historically stored sessions as plain dicts
        with keys ``messages``, ``total_cost`` and ``total_tokens``.
        This helper upserts that data into a :class:`ChatSession` so the
        WebSocket session-list / message-history endpoints see it.
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = ChatSession(session_id=session_id)
            self._sessions[session_id] = session
        session.messages = list(data.get("messages", []))
        session.total_cost = data.get("total_cost", 0.0)
        session.total_tokens = data.get("total_tokens", 0)
        return session

    def clear(self) -> None:
        """Remove every session (mainly for tests)."""
        self._sessions.clear()


# ── Singleton accessor ──────────────────────────────────────────────

_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Return the process-wide :class:`SessionManager` singleton.

    Both ``chat.py`` and ``sse_chat.py`` call this so they share the
    same underlying dict.
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def reset_session_manager() -> SessionManager:
    """Reset the singleton and return a fresh instance (for tests)."""
    global _session_manager
    _session_manager = SessionManager()
    return _session_manager
