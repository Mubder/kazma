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

import json
import logging
import os
import sqlite3
import sys
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas
from kazma_core.tenant_context import get_current_tenant_id

logger = logging.getLogger(__name__)

__all__ = [
    "ChatSession",
    "MAX_SESSIONS",
    "MAX_MESSAGES_PER_SESSION",
    "SessionManager",
    "get_session_manager",
    "reset_session_manager",
]

# Maximum number of sessions retained in memory.  When exceeded the
# least-recently-used entry is evicted (LRU via OrderedDict).
MAX_SESSIONS = 10_000

# Maximum messages per session to prevent unbounded memory growth.
MAX_MESSAGES_PER_SESSION = 200


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
    tenant_id: str = "default"
    thread_id: str = ""
    updated_at: str = ""
    title: str = ""
    archived: bool = False

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_summary(self) -> dict[str, Any]:
        """Return a serializable summary used by the session-list API."""
        return {
            "session_id": self.session_id,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at or self.created_at,
            "title": self.title or "",
            "archived": self.archived,
            "total_cost": self.total_cost,
            "total_tokens": self.total_tokens,
            "thread_id": self.thread_id,
        }

    def auto_title(self) -> str:
        """Derive a human-readable title from the first user message."""
        for msg in self.messages:
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                text = str(content or "").strip().replace("\n", " ")
                return text[:60] + ("\u2026" if len(text) > 60 else "")
        return ""

    def trim_messages(self, max_messages: int = MAX_MESSAGES_PER_SESSION) -> None:
        """Cap the message history to prevent unbounded memory growth."""
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]


class SessionManager:
    """SQLite-backed session store shared across transports with LRU cache.

    Both WebSocket and SSE handlers obtain the singleton via
    :func:`get_session_manager` so a session created on one transport
    is immediately visible to the other.

    The store is bounded by :data:`MAX_SESSIONS` per tenant. When the limit is
    exceeded the least-recently-used session for the tenant is evicted from
    both the in-memory cache and SQLite database.
    """

    def __init__(self, max_sessions: int = MAX_SESSIONS, db_path: str = ":memory:") -> None:
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, ChatSession] = OrderedDict()
        self.db_path = db_path

        # If not using in-memory, ensure database directory exists
        if self.db_path != ":memory:":
            from pathlib import Path
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # Open SQLite connection with check_same_thread=False
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        apply_sqlite_pragmas(self._conn)
        # Survive process crashes: flush WAL so chat history is not lost on
        # restart / git pull when another process briefly opens the DB.
        try:
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA wal_autocheckpoint=100")
        except Exception:
            pass

        # Create schemas and load sessions
        self._create_tables()
        self._load_all_from_db()
        logger.info(
            "[SessionManager] Loaded %d sessions from %s",
            len(self._sessions),
            self.db_path,
        )

    def _create_tables(self) -> None:
        """Create the sessions table if it does not exist, then migrate."""
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    tenant_id TEXT,
                    session_id TEXT,
                    messages TEXT,
                    created_at TEXT,
                    total_cost REAL,
                    total_tokens INTEGER,
                    thread_id TEXT,
                    PRIMARY KEY (tenant_id, session_id)
                )
            """)
            # ── In-place migrations for existing DBs ──
            # Each ALTER is wrapped in try/except because SQLite errors if
            # the column already exists (no IF NOT EXISTS for ADD COLUMN).
            for col, coltype in [
                ("updated_at", "TEXT DEFAULT ''"),
                ("title", "TEXT DEFAULT ''"),
                ("archived", "INTEGER DEFAULT 0"),
            ]:
                try:
                    self._conn.execute(
                        f"ALTER TABLE sessions ADD COLUMN {col} {coltype}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists — expected on subsequent runs

    def _load_all_from_db(self) -> None:
        """Load all sessions from SQLite into the OrderedDict cache."""
        cursor = self._conn.execute(
            "SELECT tenant_id, session_id, messages, created_at, total_cost, "
            "total_tokens, thread_id, updated_at, title, archived FROM sessions"
        )
        rows = cursor.fetchall()
        for row in rows:
            (tenant_id, session_id, messages_str, created_at, total_cost,
             total_tokens, thread_id, updated_at, title, archived) = row
            try:
                messages = json.loads(messages_str) if messages_str else []
            except Exception:
                messages = []
            session = ChatSession(
                session_id=session_id,
                messages=messages,
                created_at=created_at or "",
                total_cost=total_cost or 0.0,
                total_tokens=total_tokens or 0,
                tenant_id=tenant_id or "default",
                thread_id=thread_id or "",
                updated_at=updated_at or "",
                title=title or "",
                archived=bool(archived),
            )
            key = f"{session.tenant_id}:{session_id}"
            self._sessions[key] = session

    def _evict_if_needed(self, tenant_id: str) -> None:
        """Evict the oldest session for this tenant if we exceed max_sessions."""
        tenant_keys = [key for key in self._sessions.keys() if key.startswith(f"{tenant_id}:")]
        while len(tenant_keys) > self._max_sessions:
            oldest_key = tenant_keys.pop(0)
            self._sessions.pop(oldest_key, None)
            # Delete from DB
            _, session_id = oldest_key.split(":", 1)
            with self._conn:
                self._conn.execute(
                    "DELETE FROM sessions WHERE tenant_id = ? AND session_id = ?",
                    (tenant_id, session_id)
                )

    # ── DB helpers ──────────────────────────────────────────────────

    def _upsert_db(self, session: ChatSession) -> None:
        """Insert or update a session row in SQLite (shared by all write paths)."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO sessions (tenant_id, session_id, messages, created_at,
                                          total_cost, total_tokens, thread_id, updated_at,
                                          title, archived)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, session_id) DO UPDATE SET
                        messages=excluded.messages,
                        created_at=excluded.created_at,
                        total_cost=excluded.total_cost,
                        total_tokens=excluded.total_tokens,
                        thread_id=excluded.thread_id,
                        updated_at=excluded.updated_at,
                        title=excluded.title,
                        archived=excluded.archived
                    """,
                    (
                        session.tenant_id,
                        session.session_id,
                        json.dumps(session.messages, ensure_ascii=False),
                        session.created_at,
                        session.total_cost,
                        session.total_tokens,
                        session.thread_id,
                        session.updated_at,
                        session.title,
                        int(session.archived),
                    ),
                )
            # Nudge WAL periodically so restarts keep recent chats.
            try:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except Exception:
                pass
        except Exception:
            logger.exception(
                "[SessionManager] upsert failed session=%s path=%s",
                session.session_id,
                self.db_path,
            )
            raise

    # ── core CRUD ──────────────────────────────────────────────────

    def _load_one_from_db(self, tenant_id: str, session_id: str) -> ChatSession | None:
        """Load a single session row from SQLite (cache miss path)."""
        try:
            cursor = self._conn.execute(
                "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                "total_tokens, thread_id, updated_at, title, archived "
                "FROM sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id),
            )
            row = cursor.fetchone()
        except Exception:
            logger.debug("[SessionManager] DB load failed for %s", session_id, exc_info=True)
            return None
        if not row:
            return None
        (
            tenant_id, session_id, messages_str, created_at, total_cost,
            total_tokens, thread_id, updated_at, title, archived,
        ) = row
        try:
            messages = json.loads(messages_str) if messages_str else []
        except Exception:
            messages = []
        return ChatSession(
            session_id=session_id,
            messages=messages,
            created_at=created_at or "",
            total_cost=total_cost or 0.0,
            total_tokens=total_tokens or 0,
            tenant_id=tenant_id or "default",
            thread_id=thread_id or "",
            updated_at=updated_at or "",
            title=title or "",
            archived=bool(archived),
        )

    def get(self, session_id: str) -> ChatSession | None:
        """Return the session for ``session_id`` or ``None``.

        Cache miss falls back to SQLite so restarts / multi-worker views
        do not report empty history for sessions that exist on disk.
        """
        tenant_id = get_current_tenant_id() or "default"
        key = f"{tenant_id}:{session_id}"
        session = self._sessions.get(key)
        if session is not None:
            self._sessions.move_to_end(key)
            return session
        loaded = self._load_one_from_db(tenant_id, session_id)
        if loaded is not None:
            self._sessions[key] = loaded
            self._sessions.move_to_end(key)
            return loaded
        return None

    def get_or_create(self, session_id: str | None = None) -> ChatSession:
        """Get an existing session or create a new one.

        If ``session_id`` is ``None`` a fresh UUID is generated.  If a
        session with the given ID already exists (memory **or DB**) it is
        returned as-is — never invent an empty row over existing history.
        """
        tenant_id = get_current_tenant_id() or "default"
        if session_id:
            existing = self.get(session_id)
            if existing is not None:
                return existing

        sid = session_id or str(uuid.uuid4())
        key = f"{tenant_id}:{sid}"
        session = ChatSession(session_id=sid, tenant_id=tenant_id)
        self._sessions[key] = session
        self._upsert_db(session)
        self._evict_if_needed(tenant_id)
        return session

    def put(self, session: ChatSession) -> None:
        """Insert or replace a session in the store."""
        tenant_id = get_current_tenant_id() or "default"
        session.tenant_id = tenant_id
        session.updated_at = datetime.now(UTC).isoformat()

        # Auto-generate a title from the first user message if none set.
        if not session.title:
            session.title = session.auto_title()

        key = f"{tenant_id}:{session.session_id}"
        self._sessions[key] = session
        # LRU: mark as most-recently-used.
        self._sessions.move_to_end(key)
        self._upsert_db(session)
        self._evict_if_needed(tenant_id)

    def delete(self, session_id: str) -> None:
        """Remove a session.  No-op if not found."""
        tenant_id = get_current_tenant_id() or "default"
        key = f"{tenant_id}:{session_id}"
        self._sessions.pop(key, None)

        with self._conn:
            self._conn.execute(
                "DELETE FROM sessions WHERE tenant_id = ? AND session_id = ?",
                (tenant_id, session_id)
            )

    def list_all(self, include_archived: bool = False) -> list[ChatSession]:
        """Return sessions for the current tenant, newest-first.

        Archived sessions are excluded by default (they clutter the sidebar).
        Pass ``include_archived=True`` to get everything (for the archive view).
        """
        tenant_id = get_current_tenant_id() or "default"
        sessions = [
            sess for key, sess in self._sessions.items()
            if key.startswith(f"{tenant_id}:")
            and (include_archived or not sess.archived)
        ]
        # Sort by updated_at descending (newest activity first).
        # Fall back to created_at for old sessions without updated_at.
        sessions.sort(
            key=lambda s: s.updated_at or s.created_at or "",
            reverse=True,
        )
        return sessions

    def rename(self, session_id: str, title: str) -> ChatSession | None:
        """Set a custom title on a session. Returns the session or None."""
        tenant_id = get_current_tenant_id() or "default"
        key = f"{tenant_id}:{session_id}"
        session = self._sessions.get(key)
        if session is None:
            return None
        session.title = title.strip()[:120]
        self._upsert_db(session)
        return session

    def set_archived(self, session_id: str, archived: bool) -> ChatSession | None:
        """Archive or unarchive a session. Returns the session or None."""
        tenant_id = get_current_tenant_id() or "default"
        key = f"{tenant_id}:{session_id}"
        session = self._sessions.get(key)
        if session is None:
            return None
        session.archived = archived
        self._upsert_db(session)
        return session

    # ── convenience helpers used by SSE transport ──────────────────

    def update_from_dict(self, session_id: str, data: dict[str, Any]) -> ChatSession:
        """Mirror a dict-based session (SSE format) into the shared store.

        The SSE transport historically stored sessions as plain dicts
        with keys ``messages``, ``total_cost`` and ``total_tokens``.
        This helper upserts that data into a :class:`ChatSession` so the
        WebSocket session-list / message-history endpoints see it.
        """
        tenant_id = get_current_tenant_id() or "default"
        key = f"{tenant_id}:{session_id}"
        session = self._sessions.get(key)
        if session is None:
            session = ChatSession(session_id=session_id, tenant_id=tenant_id)
            self._sessions[key] = session

        session.messages = list(data.get("messages", []))
        session.trim_messages()
        session.total_cost = data.get("total_cost", 0.0)
        session.total_tokens = data.get("total_tokens", 0)
        session.thread_id = data.get("thread_id", session.thread_id)
        session.updated_at = datetime.now(UTC).isoformat()

        # Auto-generate title from first user message if not set.
        if not session.title:
            session.title = session.auto_title()

        # LRU: mark as most-recently-used.
        self._sessions.move_to_end(key)
        self._upsert_db(session)
        self._evict_if_needed(tenant_id)
        return session

    def clear(self) -> None:
        """Remove every session (mainly for tests)."""
        tenant_id = get_current_tenant_id() or "default"
        # Delete only current tenant's sessions from DB
        with self._conn:
            self._conn.execute(
                "DELETE FROM sessions WHERE tenant_id = ?",
                (tenant_id,)
            )

        # Remove current tenant's sessions from cache
        keys_to_remove = [
            key for key in self._sessions.keys()
            if key.startswith(f"{tenant_id}:")
        ]
        for key in keys_to_remove:
            self._sessions.pop(key, None)


# ── Singleton accessor ──────────────────────────────────────────────

_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Return the process-wide :class:`SessionManager` singleton.

    Both ``chat.py`` and ``sse_chat.py`` call this so they share the
    same underlying dict.
    """
    global _session_manager
    if _session_manager is None:
        db_path = "kazma-data/chat_sessions.db"
        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            db_path = "kazma-data/chat_sessions_test.db"
        _session_manager = SessionManager(db_path=db_path)
    return _session_manager


def reset_session_manager() -> SessionManager:
    """Reset the singleton and return a fresh instance (for tests)."""
    global _session_manager
    if _session_manager is not None and hasattr(_session_manager, "_conn"):
        try:
            _session_manager._conn.close()
        except Exception as exc:
            logging.getLogger(__name__).debug("session manager close: %s", exc)

    db_path = "kazma-data/chat_sessions.db"
    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        db_path = "kazma-data/chat_sessions_test.db"
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception as exc:
                logging.getLogger(__name__).debug("test session db remove: %s", exc)

    _session_manager = SessionManager(db_path=db_path)
    if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            with _session_manager._conn:
                _session_manager._conn.execute("DELETE FROM sessions")
            _session_manager._sessions.clear()
        except Exception as exc:
            logging.getLogger(__name__).debug("test session clear: %s", exc)
    return _session_manager
