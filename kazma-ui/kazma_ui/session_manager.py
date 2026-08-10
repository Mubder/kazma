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
import threading
import uuid
import contextlib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterator

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

# Empty non-gateway web shells older than this are deleted on list (connect
# used to create durable 0-msg rows; GC cleans the backlog + stragglers).
EMPTY_WEB_SESSION_GC_SECONDS = 10 * 60


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
    pinned: bool = False

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def add_message(
        self,
        role: str,
        content: str,
        *,
        model: str | None = None,
        activity: list[dict[str, Any]] | None = None,
        **extra: Any,
    ) -> None:
        """Append a message dict to message history and update timestamp.

        Optional *model* stamps which LLM produced an assistant reply (for UI
        meta + reload). *activity* stores CoT/workbench rows. Extra kwargs are
        merged onto the message dict (best-effort, string/JSON-serializable).
        """
        if not content:
            return
        # Deduplicate consecutive identical messages
        if self.messages and self.messages[-1].get("role") == role and self.messages[-1].get("content") == content:
            return
        now = datetime.now(UTC).isoformat()
        msg: dict[str, Any] = {"role": role, "content": content, "ts": now}
        if model:
            msg["model"] = str(model)
        if activity:
            msg["activity"] = list(activity)
        for k, v in extra.items():
            if v is not None and k not in msg:
                msg[k] = v
        self.messages.append(msg)
        if len(self.messages) > MAX_MESSAGES_PER_SESSION:
            self.messages = self.messages[-MAX_MESSAGES_PER_SESSION:]
        self.updated_at = now

    def to_summary(self) -> dict[str, Any]:
        """Return a serializable summary used by the session-list API."""
        platform = "web"
        sid = self.session_id or ""
        t_low = (self.title or "").lower()
        if sid.startswith("gw-telegram") or "telegram" in t_low:
            platform = "telegram"
        elif sid.startswith("gw-discord") or "discord" in t_low:
            platform = "discord"
        elif sid.startswith("gw-slack") or "slack" in t_low:
            platform = "slack"
        elif sid.startswith("gw-"):
            platform = "gateway"

        return {
            "session_id": self.session_id,
            "platform": platform,
            "message_count": len(self.messages),
            "created_at": self.created_at,
            "updated_at": self.updated_at or self.created_at,
            "title": self.title or f"{platform.capitalize()} Session",
            "archived": bool(self.archived),
            "pinned": bool(self.pinned),
            "total_cost": self.total_cost,
            "total_tokens": self.total_tokens,
            "thread_id": self.thread_id or self.session_id,
        }

    def auto_title(self) -> str:
        """Derive a human-readable title from the first user message."""
        for msg in self.messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            text = str(content or "").strip().replace("\n", " ")
            # Skip Soul / inject fences that were ever stored as role=user
            if "<kazma:data" in text and "untrusted" in text:
                continue
            if "[SelfImprovement]" in text and "BEGIN OBSERVATION" in text:
                continue
            if not text:
                continue
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
        # Guard OrderedDict + sqlite across threadpool/async (audit M15)
        self._lock = threading.RLock()
        # T4: per-session mutation locks so read-modify-write sequences from
        # concurrent persist paths (live stream, detached done_callback,
        # /reset) never interleave on the same ChatSession.
        self._mutation_locks: dict[str, threading.Lock] = {}
        self._pg = False
        self._conn: sqlite3.Connection | None = None

        try:
            from kazma_core.db.pg_helpers import use_postgres

            self._pg = use_postgres() and db_path != ":memory:"
        except Exception:
            self._pg = False

        if self._pg:
            from kazma_core.db.pg_helpers import get_pool

            get_pool()  # ensure schema
            logger.info("[SessionManager] using Postgres backend (kazma_chat_sessions)")
        else:
            # If not using in-memory, ensure database directory exists
            if self.db_path != ":memory:":
                from pathlib import Path
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

            # Open SQLite connection with check_same_thread=False
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            apply_sqlite_pragmas(self._conn)
            try:
                self._conn.execute("PRAGMA synchronous=NORMAL")
                self._conn.execute("PRAGMA wal_autocheckpoint=100")
            except Exception:
                pass

        # Create schemas and load sessions
        with self._lock:
            if not self._pg:
                self._create_tables()
            # Cap warm cache: load newest N only (full history still on disk)
            self._load_all_from_db(limit=min(max_sessions, 2000))
        logger.info(
            "[SessionManager] Loaded %d sessions from %s",
            len(self._sessions),
            "postgres" if self._pg else self.db_path,
        )

    def _create_tables(self) -> None:
        """Create the sessions table if it does not exist, then migrate."""
        if self._pg or self._conn is None:
            return
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
                ("pinned", "INTEGER DEFAULT 0"),
            ]:
                try:
                    self._conn.execute(
                        f"ALTER TABLE sessions ADD COLUMN {col} {coltype}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists — expected on subsequent runs

    def _session_from_row(
        self,
        tenant_id: Any,
        session_id: Any,
        messages_raw: Any,
        created_at: Any,
        total_cost: Any,
        total_tokens: Any,
        thread_id: Any,
        updated_at: Any,
        title: Any,
        archived: Any,
        pinned: Any = None,
    ) -> ChatSession:
        if isinstance(messages_raw, (list, dict)):
            messages = messages_raw if isinstance(messages_raw, list) else []
        else:
            try:
                messages = json.loads(messages_raw) if messages_raw else []
            except Exception:
                messages = []
        return ChatSession(
            session_id=str(session_id),
            messages=messages,
            created_at=created_at or "",
            total_cost=float(total_cost or 0.0),
            total_tokens=int(total_tokens or 0),
            tenant_id=str(tenant_id or "default"),
            thread_id=thread_id or "",
            updated_at=updated_at or "",
            title=title or "",
            archived=bool(archived),
            pinned=bool(pinned),
        )

    def _load_all_from_db(self, limit: int | None = None) -> None:
        """Load sessions into the OrderedDict cache (SQLite or Postgres)."""
        if self._pg:
            from kazma_core.db.pg_helpers import get_pool

            sql = (
                "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                "total_tokens, thread_id, updated_at, title, archived, pinned "
                "FROM kazma_chat_sessions "
                "ORDER BY COALESCE(NULLIF(updated_at,''), created_at) DESC"
            )
            params: tuple = ()
            if limit is not None and limit > 0:
                sql += " LIMIT %s"
                params = (int(limit),)
            rows = get_pool().execute(sql, params if params else None)
            for row in rows:
                session = self._session_from_row(
                    row["tenant_id"], row["session_id"], row["messages"],
                    row["created_at"], row["total_cost"], row["total_tokens"],
                    row["thread_id"], row["updated_at"], row["title"], row["archived"],
                    row["pinned"] if "pinned" in row else None,
                )
                self._sessions[f"{session.tenant_id}:{session.session_id}"] = session
            return

        assert self._conn is not None
        sql = (
            "SELECT tenant_id, session_id, messages, created_at, total_cost, "
            "total_tokens, thread_id, updated_at, title, archived, pinned FROM sessions "
            "ORDER BY COALESCE(NULLIF(updated_at,''), created_at) DESC"
        )
        if limit is not None and limit > 0:
            sql += f" LIMIT {int(limit)}"
        cursor = self._conn.execute(sql)
        rows = cursor.fetchall()
        for row in rows:
            (tenant_id, session_id, messages_str, created_at, total_cost,
             total_tokens, thread_id, updated_at, title, archived, pinned) = row
            session = self._session_from_row(
                tenant_id, session_id, messages_str, created_at, total_cost,
                total_tokens, thread_id, updated_at, title, archived, pinned,
            )
            self._sessions[f"{session.tenant_id}:{session_id}"] = session

    def _evict_if_needed(self, tenant_id: str) -> None:
        """Evict the oldest session for this tenant if we exceed max_sessions."""
        tenant_keys = [key for key in self._sessions.keys() if key.startswith(f"{tenant_id}:")]
        while len(tenant_keys) > self._max_sessions:
            oldest_key = tenant_keys.pop(0)
            self._sessions.pop(oldest_key, None)
            _, session_id = oldest_key.split(":", 1)
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                get_pool().execute(
                    "DELETE FROM kazma_chat_sessions WHERE tenant_id = %s AND session_id = %s",
                    (tenant_id, session_id),
                )
            else:
                assert self._conn is not None
                with self._conn:
                    self._conn.execute(
                        "DELETE FROM sessions WHERE tenant_id = ? AND session_id = ?",
                        (tenant_id, session_id),
                    )

    # ── DB helpers ──────────────────────────────────────────────────

    def _upsert_db(self, session: ChatSession) -> None:
        """Insert or update a session row (SQLite or Postgres)."""
        try:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool, json_dumps

                get_pool().execute(
                    """
                    INSERT INTO kazma_chat_sessions (
                        tenant_id, session_id, messages, created_at,
                        total_cost, total_tokens, thread_id, updated_at,
                        title, archived, pinned
                    ) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, session_id) DO UPDATE SET
                        messages = EXCLUDED.messages,
                        created_at = EXCLUDED.created_at,
                        total_cost = EXCLUDED.total_cost,
                        total_tokens = EXCLUDED.total_tokens,
                        thread_id = EXCLUDED.thread_id,
                        updated_at = EXCLUDED.updated_at,
                        title = EXCLUDED.title,
                        archived = EXCLUDED.archived,
                        pinned = EXCLUDED.pinned
                    """,
                    (
                        session.tenant_id,
                        session.session_id,
                        json_dumps(session.messages),
                        session.created_at,
                        session.total_cost,
                        session.total_tokens,
                        session.thread_id,
                        session.updated_at,
                        session.title,
                        bool(session.archived),
                        bool(session.pinned),
                    ),
                )
                return

            assert self._conn is not None
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO sessions (tenant_id, session_id, messages, created_at,
                                          total_cost, total_tokens, thread_id, updated_at,
                                          title, archived, pinned)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, session_id) DO UPDATE SET
                        messages=excluded.messages,
                        created_at=excluded.created_at,
                        total_cost=excluded.total_cost,
                        total_tokens=excluded.total_tokens,
                        thread_id=excluded.thread_id,
                        updated_at=excluded.updated_at,
                        title=excluded.title,
                        archived=excluded.archived,
                        pinned=excluded.pinned
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
                        int(session.pinned),
                    ),
                )
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
        """Load a single session row (cache miss path)."""
        try:
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                row = get_pool().execute_one(
                    "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                    "total_tokens, thread_id, updated_at, title, archived, pinned "
                    "FROM kazma_chat_sessions WHERE tenant_id = %s AND session_id = %s",
                    (tenant_id, session_id),
                )
                if not row:
                    return None
                return self._session_from_row(
                    row["tenant_id"], row["session_id"], row["messages"],
                    row["created_at"], row["total_cost"], row["total_tokens"],
                    row["thread_id"], row["updated_at"], row["title"], row["archived"],
                    row["pinned"] if "pinned" in row else None,
                )

            assert self._conn is not None
            cursor = self._conn.execute(
                "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                "total_tokens, thread_id, updated_at, title, archived, pinned "
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
            total_tokens, thread_id, updated_at, title, archived, pinned,
        ) = row
        return self._session_from_row(
            tenant_id, session_id, messages_str, created_at, total_cost,
            total_tokens, thread_id, updated_at, title, archived, pinned,
        )

    def get(self, session_id: str) -> ChatSession | None:
        """Return the current tenant's session for ``session_id`` or ``None``."""
        with self._lock:
            tenant_id = get_current_tenant_id() or "default"
            key = f"{tenant_id}:{session_id}"
            session = self._sessions.get(key)
            if session is not None:
                self._sessions.move_to_end(key)
                return session

            loaded = self._load_one_from_db(tenant_id, session_id)
            if loaded is not None:
                self._sessions[f"{loaded.tenant_id}:{loaded.session_id}"] = loaded
                return loaded
            return None

    def get_or_create(
        self,
        session_id: str | None = None,
        *,
        durable: bool = True,
    ) -> ChatSession:
        """Get an existing session or create a new one.

        If ``session_id`` is ``None`` a fresh UUID is generated.  If a
        session with the given ID already exists (memory **or DB**) it is
        returned as-is — never invent an empty row over existing history.

        *durable* (default True): write the new empty shell to the DB.
        Pass ``durable=False`` for WS connect / UI bind so we do not pollute
        the sidebar with "Web Session · 0 msgs" until the first real message
        (which calls :meth:`put` and persists).
        """
        with self._lock:
            tenant_id = get_current_tenant_id() or "default"
            if session_id:
                existing = self.get(session_id)
                if existing is not None:
                    return existing

            sid = session_id or str(uuid.uuid4())
            key = f"{tenant_id}:{sid}"
            session = ChatSession(session_id=sid, tenant_id=tenant_id)
            self._sessions[key] = session
            if durable:
                self._upsert_db(session)
            self._evict_if_needed(tenant_id)
            return session

    def get_by_thread_id(self, thread_id: str) -> ChatSession | None:
        """Return the current tenant's session associated with ``thread_id``."""
        if not thread_id:
            return None
        with self._lock:
            tenant_id = get_current_tenant_id() or "default"
            for key, session in self._sessions.items():
                if key.startswith(f"{tenant_id}:") and session.thread_id == thread_id:
                    self._sessions.move_to_end(key)
                    return session

            try:
                if self._pg:
                    from kazma_core.db.pg_helpers import get_pool

                    row = get_pool().execute_one(
                        "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                        "total_tokens, thread_id, updated_at, title, archived, pinned "
                        "FROM kazma_chat_sessions WHERE tenant_id = %s AND thread_id = %s",
                        (tenant_id, thread_id),
                    )
                    if not row:
                        return None
                    loaded = self._session_from_row(
                        row["tenant_id"], row["session_id"], row["messages"],
                        row["created_at"], row["total_cost"], row["total_tokens"],
                        row["thread_id"], row["updated_at"], row["title"], row["archived"],
                        row["pinned"] if "pinned" in row else None,
                    )
                else:
                    assert self._conn is not None
                    row = self._conn.execute(
                        "SELECT tenant_id, session_id, messages, created_at, total_cost, "
                        "total_tokens, thread_id, updated_at, title, archived, pinned "
                        "FROM sessions WHERE tenant_id = ? AND thread_id = ?",
                        (tenant_id, thread_id),
                    ).fetchone()
                    if not row:
                        return None
                    loaded = self._session_from_row(*row)
            except Exception:
                logger.debug(
                    "[SessionManager] thread lookup failed for %s",
                    thread_id,
                    exc_info=True,
                )
                return None

            self._sessions[f"{loaded.tenant_id}:{loaded.session_id}"] = loaded
            return loaded

    def put(self, session: ChatSession) -> None:
        """Insert or replace a session in the store."""
        with self._lock:
            tenant_id = session.tenant_id or get_current_tenant_id() or "default"
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

    def _refresh_from_db(self, session_id: str) -> None:
        """Force-reload a session from the database, bypassing the in-memory cache.

        For gw-* platform sessions (Telegram/Discord/Slack), the gateway
        writes directly to Postgres (bypassing this process's in-memory
        cache). The cache can be stale — missing the latest assistant
        response. This method reloads from DB and replaces the cached entry
        so the Web UI always shows current data.

        Safe to call even if the session doesn't exist in the DB yet.
        """
        with self._lock:
            tenant_id = get_current_tenant_id() or "default"
            loaded = self._load_one_from_db(tenant_id, session_id)
            if loaded is not None:
                key = f"{loaded.tenant_id}:{loaded.session_id}"
                self._sessions[key] = loaded
                self._sessions.move_to_end(key)

    @contextlib.contextmanager
    def transact(self, session_id: str) -> Iterator[ChatSession]:
        """Serialize read-modify-write on *session_id* (T4).

        Per-session lock held while the caller mutates the ChatSession,
        then the transaction calls :meth:`put` before releasing. Without
        this, the live incremental persist, the detached done_callback
        persist, and /reset could interleave on the same message list and
        last-write-wins could drop a concurrent update.

        Usage::

            with store.transact(session_id) as sess:
                sess.messages.append(...)
        """
        with self._lock:
            lock = self._mutation_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._mutation_locks[session_id] = lock
        with lock:
            session = self.get(session_id)
            if session is None:
                raise KeyError(f"session not found: {session_id}")
            yield session
            self.put(session)

    def delete(self, session_id: str) -> None:
        """Remove a session.  No-op if not found."""
        with self._lock:
            tenant_id = get_current_tenant_id() or "default"
            key = f"{tenant_id}:{session_id}"
            self._sessions.pop(key, None)

            if self._pg:
                from kazma_core.db.pg_helpers import get_pool

                get_pool().execute(
                    "DELETE FROM kazma_chat_sessions WHERE tenant_id = %s AND session_id = %s",
                    (tenant_id, session_id),
                )
            else:
                assert self._conn is not None
                with self._conn:
                    self._conn.execute(
                        "DELETE FROM sessions WHERE tenant_id = ? AND session_id = ?",
                        (tenant_id, session_id),
                    )

    def _hydrate_from_checkpoints_db(self) -> None:
        """Auto-discover platform gateway sessions (gw-*) from checkpoints (Postgres or SQLite)."""
        try:
            thread_ids: set[str] = set()
            if self._pg:
                from kazma_core.db.pg_helpers import get_pool
                try:
                    rows = get_pool().execute(
                        "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE 'gw-%'"
                    )
                    for r in rows:
                        tid = r.get("thread_id") if isinstance(r, dict) else (r[0] if r else None)
                        if tid:
                            thread_ids.add(str(tid))
                except Exception as pg_exc:
                    logger.debug("[SessionManager] Postgres checkpoint query skipped: %s", pg_exc)
            else:
                from kazma_core.paths import checkpoints_db
                cp_path = checkpoints_db()
                if os.path.exists(cp_path):
                    conn = sqlite3.connect(cp_path)
                    cursor = conn.execute(
                        "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE 'gw-%'"
                    )
                    for (tid,) in cursor.fetchall():
                        if tid:
                            thread_ids.add(str(tid))
                    conn.close()

            for thread_id in thread_ids:
                if any(s.session_id == thread_id for s in self._sessions.values()):
                    continue
                tid_low = thread_id.lower()
                if "telegram" in tid_low:
                    plat = "Telegram"
                elif "discord" in tid_low:
                    plat = "Discord"
                elif "slack" in tid_low:
                    plat = "Slack"
                else:
                    plat = "Gateway"

                sess = ChatSession(
                    session_id=thread_id,
                    thread_id=thread_id,
                    tenant_id="default",
                    title=f"{plat} · Session",
                )
                key = f"default:{thread_id}"
                self._sessions[key] = sess
                self._upsert_db(sess)
        except Exception as exc:
            logger.debug("[SessionManager] _hydrate_from_checkpoints_db skipped: %s", exc)

    def prune_empty_web_sessions(
        self,
        *,
        max_age_seconds: float = EMPTY_WEB_SESSION_GC_SECONDS,
    ) -> int:
        """Delete empty non-gateway web shells older than *max_age_seconds*.

        Returns the number of sessions removed. Safe to call from the list
        endpoint — never touches gateway (``gw-*``) sessions or any session
        that has messages / a custom title / pin.
        """
        now = datetime.now(UTC)
        removed = 0
        # Snapshot ids under lock, delete outside the iteration to reuse delete().
        to_delete: list[str] = []
        with self._lock:
            tenant_id = get_current_tenant_id() or "default"
            for key, sess in list(self._sessions.items()):
                if not key.startswith(f"{tenant_id}:"):
                    continue
                if not self._is_empty_web_shell(sess):
                    continue
                age = self._session_age_seconds(sess, now=now)
                if age is None or age < max_age_seconds:
                    continue
                to_delete.append(sess.session_id)
        for sid in to_delete:
            try:
                self.delete(sid)
                removed += 1
            except Exception:
                logger.debug(
                    "[SessionManager] prune_empty_web_sessions failed for %s",
                    sid,
                    exc_info=True,
                )
        if removed:
            logger.info(
                "[SessionManager] Pruned %d empty web session shell(s)",
                removed,
            )
        return removed

    @staticmethod
    def _is_empty_web_shell(sess: ChatSession) -> bool:
        """True for unpinned, untitled, message-less non-gateway web rows."""
        sid = sess.session_id or ""
        if sid.startswith("gw-"):
            return False
        if sess.messages:
            return False
        if sess.pinned:
            return False
        # Keep user-renamed shells even if empty.
        title = (sess.title or "").strip()
        if title and title.lower() not in ("web session", "web", "new chat"):
            return False
        return True

    @staticmethod
    def _session_age_seconds(
        sess: ChatSession,
        *,
        now: datetime | None = None,
    ) -> float | None:
        raw = sess.updated_at or sess.created_at or ""
        if not raw:
            return None
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            ref = now or datetime.now(UTC)
            return max(0.0, (ref - ts).total_seconds())
        except Exception:
            return None

    def list_all(
        self,
        include_archived: bool = False,
        *,
        include_empty: bool = False,
        prune_empty: bool = True,
    ) -> list[ChatSession]:
        """Return sessions for the current tenant, newest-first.

        Archived sessions are excluded by default (they clutter the sidebar).
        Pass ``include_archived=True`` to get everything (for the archive view).

        Empty non-gateway web shells are hidden by default (``include_empty``)
        and aged ones are garbage-collected when ``prune_empty`` is True so
        the sidebar does not accumulate "Web Session · 0 msgs" forever.
        """
        if prune_empty:
            try:
                self.prune_empty_web_sessions()
            except Exception:
                logger.debug(
                    "[SessionManager] prune_empty_web_sessions raised",
                    exc_info=True,
                )

        with self._lock:
            tenant_id = get_current_tenant_id() or "default"
            sessions: list[ChatSession] = []
            seen_sids: set[str] = set()

            for key, sess in self._sessions.items():
                if sess.session_id in seen_sids:
                    continue
                if not key.startswith(f"{tenant_id}:"):
                    continue
                if not include_archived and sess.archived:
                    continue
                if not include_empty and self._is_empty_web_shell(sess):
                    continue
                sessions.append(sess)
                seen_sids.add(sess.session_id)

            # Sort by updated_at descending (newest activity first).
            # Fall back to created_at for old sessions without updated_at.
            sessions.sort(
                key=lambda s: s.updated_at or s.created_at or "",
                reverse=True,
            )
            return sessions

    def rename(self, session_id: str, title: str) -> ChatSession | None:
        """Set a custom title on a session. Returns the session or None."""
        with self._lock:
            session = self.get(session_id)
            if session is None:
                return None
            session.title = title.strip()[:120]
            self._upsert_db(session)
            return session

    def set_archived(self, session_id: str, archived: bool) -> ChatSession | None:
        """Archive or unarchive a session. Returns the session or None."""
        with self._lock:
            session = self.get(session_id)
            if session is None:
                return None
            session.archived = archived
            self._upsert_db(session)
            return session

    def set_pinned(self, session_id: str, pinned: bool) -> ChatSession | None:
        """Pin or unpin a session (stays at the top of the sidebar)."""
        with self._lock:
            session = self.get(session_id)
            if session is None:
                return None
            session.pinned = pinned
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
        with self._lock:
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
        if self._pg:
            from kazma_core.db.pg_helpers import get_pool

            get_pool().execute(
                "DELETE FROM kazma_chat_sessions WHERE tenant_id = %s",
                (tenant_id,),
            )
        else:
            assert self._conn is not None
            with self._conn:
                self._conn.execute(
                    "DELETE FROM sessions WHERE tenant_id = ?",
                    (tenant_id,),
                )

        keys_to_remove = [
            key for key in self._sessions.keys()
            if key.startswith(f"{tenant_id}:")
        ]
        for key in keys_to_remove:
            self._sessions.pop(key, None)

    def close(self) -> None:
        """Explicitly close the database connection and flush WAL checkpoint."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(FULL)")
                    self._conn.close()
                except Exception as exc:
                    logger.debug("[SessionManager] error closing db: %s", exc)
                finally:
                    self._conn = None


# ── Singleton accessor ──────────────────────────────────────────────

_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Return the process-wide :class:`SessionManager` singleton.

    Both ``chat.py`` and ``sse_chat.py`` call this so they share the
    same underlying dict.
    """
    global _session_manager
    if _session_manager is None:
        try:
            from kazma_core.paths import data_dir
            db_path = str(data_dir() / "chat_sessions.db")
        except Exception:
            db_path = "kazma-data/chat_sessions.db"
        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            db_path = "kazma-data/chat_sessions_test.db"
        _session_manager = SessionManager(db_path=db_path)
    return _session_manager


def reset_session_manager() -> SessionManager:
    """Reset the singleton and return a fresh instance (for tests)."""
    global _session_manager
    if _session_manager is not None and getattr(_session_manager, "_conn", None) is not None:
        try:
            _session_manager._conn.close()
        except Exception as exc:
            logging.getLogger(__name__).debug("session manager close: %s", exc)

    try:
        from kazma_core.paths import data_dir
        db_path = str(data_dir() / "chat_sessions.db")
    except Exception:
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
