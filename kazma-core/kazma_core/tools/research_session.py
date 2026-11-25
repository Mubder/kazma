"""Durable research sessions + in-process progress broadcast (industry R3).

Sessions survive process lifetime for list/detail; progress SSE uses in-memory
queues (live only while this process runs).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import psutil
import sqlite3
import threading
import time
import uuid
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable, Iterator
from kazma_core.web_acquire.research_runtime import bounded_session
from kazma_core.web_acquire.crawl_jobs import call
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

__all__ = [
    "ResearchSession",
    "create_session",
    "get_session",
    "list_sessions",
    "update_session",
    "start_deep_research",
    "cancel_session",
    "archive_session",
    "delete_session",
    "subscribe_progress",
    "unsubscribe_progress",
    "record_chat_research",
    "session_here",
    "session_to_change",
    "suppress_chat_recording",
]

logger = logging.getLogger(__name__)

_ProgressCb = Callable[[str, str], Awaitable[None] | None]

# Chat-research session minting suppresses while a deep research pipeline
# runs its sub-queries: every sub-search flows through the shared
# web_search tool, which otherwise records a standalone "chat research"
# session row PER SUB-QUERY — the Research panel used to show 10+ "1
# sources · done" rows for a single deep run (pipeline flood, 2026-08-14).
_chat_record_suppressed: ContextVar[bool] = ContextVar(
    "kazma_chat_record_suppressed", default=False
)


@contextmanager
def suppress_chat_recording() -> Iterator[None]:
    """Scope in which ``record_chat_research`` no-ops (pipeline sub-queries)."""
    token = _chat_record_suppressed.set(True)
    try:
        yield
    finally:
        _chat_record_suppressed.reset(token)

# session_id → the SSE subscribers' queues, each with the loop that reads it.
# asyncio objects are not thread-safe, and these are touched from worker
# threads: the pipeline writes progress through asyncio.to_thread, and the
# Research routes are plain defs (AGENTS §35). So a queue is only ever put to,
# and a task only ever cancelled, on its own loop (_deliver,
# _cancel_on_its_loop); the lock covers the registry itself.
_SUBS: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}
_SUBS_LOCK = threading.Lock()
_RUNNING: dict[str, asyncio.Task] = {}
_RUNNING_LOCK = threading.RLock()
_SETTLING: set[str] = set()


class ResearchSessionBusyError(RuntimeError):
    """A running session must remain inspectable until its owner finishes."""


def _running_task(session_id: str):
    with _RUNNING_LOCK:
        return _RUNNING.get(session_id)


def _on_loop(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _offer(q: asyncio.Queue, event: dict[str, Any]) -> None:
    """Queue *event*; a full queue (a reader 100 events behind) drops it."""
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        logger.debug("[research_session] a slow progress reader missed an event")


def _deliver(loop: asyncio.AbstractEventLoop, q: asyncio.Queue, event: dict[str, Any]) -> None:
    """Hand *event* to *q* on *loop*, whichever thread this runs on."""
    if _on_loop(loop):
        _offer(q, event)
        return
    try:
        loop.call_soon_threadsafe(_offer, q, event)
    except RuntimeError:
        # The loop has closed, and the reader with it.
        logger.debug("[research_session] progress reader's loop is closed")


def _publish(session_id: str, event: dict[str, Any]) -> None:
    """Send *event* to every subscriber of *session_id*."""
    with _SUBS_LOCK:
        subs = list(_SUBS.get(session_id, ()))
    for loop, q in subs:
        _deliver(loop, q, event)


def _cancel_on_its_loop(task: asyncio.Task) -> None:
    """Cancel *task* from any thread (``Task.cancel`` belongs to its loop)."""
    loop = task.get_loop()
    if _on_loop(loop):
        task.cancel()
        return
    try:
        loop.call_soon_threadsafe(task.cancel)
    except RuntimeError:
        # A closed loop has already ended the task.
        logger.debug("[research_session] run's loop is closed; nothing to cancel")


@dataclass
class ResearchSession:
    id: str
    topic: str
    depth: str = "deep"
    status: str = "pending"  # pending|running|cancelling|interrupted|partial|done|error|cancelled
    stage: str = "queued"
    message: str = ""
    log: list[str] = field(default_factory=list)
    report_path: str = ""
    summary: str = ""
    error: str = ""
    sources: int = 0
    max_sources: int = 8
    rubric_score: float | None = None
    rubric_ok: bool | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)
    archived: bool = False
    #: The space the session is (Spaces S1-16, always stamped); ``""`` for a
    #: session from before, which is the owner's.
    tenant_id: str = ""
    #: The account that started it (Spaces S1-16 follow-up, always stamped
    #: when a person is bound); ``""`` for a session from before or one
    #: Kazma started. Its author may change it (decision N32).
    asker: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _db_path() -> Path:
    from kazma_core.paths import data_dir

    return data_dir() / "research_sessions.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(str(_db_path()), check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS research_sessions (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            depth TEXT DEFAULT 'deep',
            status TEXT DEFAULT 'pending',
            stage TEXT DEFAULT 'queued',
            message TEXT DEFAULT '',
            log_json TEXT DEFAULT '[]',
            report_path TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            error TEXT DEFAULT '',
            sources INTEGER DEFAULT 0,
            max_sources INTEGER DEFAULT 8,
            rubric_score REAL,
            rubric_ok INTEGER,
            created_at REAL,
            updated_at REAL,
            meta_json TEXT DEFAULT '{}',
            archived INTEGER DEFAULT 0,
            tenant_id TEXT DEFAULT '',
            asker TEXT DEFAULT '',
            thread_ids TEXT DEFAULT ''
        )
        """
    )
    # Columns added after the table first shipped (pre-R4 databases).
    from kazma_core.db.sqlite_columns import add_missing_columns

    add_missing_columns(
        c,
        "research_sessions",
        (
            ("rubric_score", "REAL"),
            ("rubric_ok", "INTEGER"),
            ("archived", "INTEGER DEFAULT 0"),
            ("tenant_id", "TEXT DEFAULT ''"),
            ("asker", "TEXT DEFAULT ''"),
            ("thread_ids", "TEXT DEFAULT ''"),
        ),
    )
    c.commit()
    return c


def _row_to_session(r: sqlite3.Row) -> ResearchSession:
    try:
        log = json.loads(r["log_json"] or "[]")
    except Exception:
        log = []
    try:
        meta = json.loads(r["meta_json"] or "{}")
    except Exception:
        meta = {}
    keys = r.keys()
    rscore = None
    rok = None
    if "rubric_score" in keys and r["rubric_score"] is not None:
        try:
            rscore = float(r["rubric_score"])
        except (TypeError, ValueError):
            rscore = None
    if "rubric_ok" in keys and r["rubric_ok"] is not None:
        rok = bool(int(r["rubric_ok"]))
    rarch = False
    if "archived" in keys and r["archived"] is not None:
        try:
            rarch = bool(int(r["archived"]))
        except (TypeError, ValueError):
            rarch = False
    return ResearchSession(
        id=r["id"],
        topic=r["topic"] or "",
        depth=r["depth"] or "deep",
        status=r["status"] or "pending",
        stage=r["stage"] or "",
        message=r["message"] or "",
        log=list(log) if isinstance(log, list) else [],
        report_path=r["report_path"] or "",
        summary=r["summary"] or "",
        error=r["error"] or "",
        sources=int(r["sources"] or 0),
        max_sources=int(r["max_sources"] or 8),
        rubric_score=rscore,
        rubric_ok=rok,
        created_at=float(r["created_at"] or 0),
        updated_at=float(r["updated_at"] or 0),
        meta=meta if isinstance(meta, dict) else {},
        archived=rarch,
        tenant_id=str(r["tenant_id"] or "") if "tenant_id" in keys else "",
        asker=str(r["asker"] or "") if "asker" in keys else "",
    )


_MAX_SESSIONS = 256
_MAX_SESSION_BYTES = 128 * 1024**2
#: Snapshots of chat web searches (``record_chat_research``) have their own
#: allowance. They shared the research one, and on live 349 of 361 rows were
#: chat searches: Deep Research refused to start, "history is full", over a
#: history the user had never started (2026-10-31).
_MAX_CHAT_SNAPSHOTS = 1024
_MAX_CHAT_SNAPSHOT_BYTES = 32 * 1024**2
_ROW_BYTES = ("length(CAST(log_json AS BLOB))+length(CAST(summary AS BLOB))+length(CAST(meta_json AS BLOB))"
              "+length(CAST(error AS BLOB))+length(CAST(topic AS BLOB))")


def _allowance(chat: bool):
    """``(rows, bytes, which rows)`` of the allowance a row of this kind draws on."""
    if chat:
        return _MAX_CHAT_SNAPSHOTS, _MAX_CHAT_SNAPSHOT_BYTES, "depth = 'chat'"
    return _MAX_SESSIONS, _MAX_SESSION_BYTES, "COALESCE(depth, '') != 'chat'"


def _history_admission(connection, *, chat: bool = False):
    rows, budget, which = _allowance(chat)
    count, size = connection.execute(
        f"SELECT COUNT(*), COALESCE(SUM({_ROW_BYTES}),0) FROM research_sessions WHERE {which}").fetchone()
    if count >= rows or size >= budget - 2 * 1024**2:
        if chat:
            raise AcquisitionError(FetchErrorCode.LIMIT, f'Chat search history is full ({rows} snapshots / {budget // 1024**2}MiB); delete old chat research entries to keep recording them.')
        raise AcquisitionError(FetchErrorCode.LIMIT, f'Research history is full ({rows} sessions / {budget // 1024**2}MiB); remove settled history before starting more work. Archiving does not release storage.')


def _bounded_session_row(session):
    if len(session.topic) > 2000 or len(json.dumps(session.meta).encode('utf-8')) > 64 * 1024:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Research session topic or metadata exceeds its storage budget.')
    session.summary = session.summary[:200_000]
    session.log = [str(entry)[:1024] for entry in session.log[-200:]]
    session.error, session.message = session.error[:2000], session.message[:2000]


def _process_owner():
    return [os.getpid(), psutil.Process().create_time()]


def _reconcile_sessions(connection):
    from kazma_core.web_acquire.crawl_jobs import _alive

    rows = connection.execute("SELECT id, status, meta_json FROM research_sessions WHERE status IN ('pending','running','cancelling')").fetchmany(_MAX_SESSIONS + 1)
    changed = False
    for row in rows:
        meta = json.loads(row['meta_json'] or '{}')
        owner = meta.get('process_owner') if isinstance(meta, dict) else None
        if owner is None or not _alive(owner):
            message = 'Research process ended; inspect the saved job and explicitly resume. Uncertain effects are never replayed.'
            connection.execute("UPDATE research_sessions SET status='interrupted', stage='interrupted', message=?, updated_at=? WHERE id=? AND status=? AND meta_json=?",
                (message, time.time(), row['id'], row['status'], row['meta_json']))
            changed = True
    if changed:
        connection.commit()


def _claim_session(session_id, owner, root):
    connection = _conn()
    try:
        _reconcile_sessions(connection)
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM research_sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise AcquisitionError(FetchErrorCode.POLICY, 'Research session was not found.')
        session = _row_to_session(row)
        if session.status != 'interrupted':
            raise ResearchSessionBusyError('Only an interrupted research session may resume; live or terminal work was refused.')
        meta = dict(session.meta)
        if not meta.get('durable_job_id'):
            raise AcquisitionError(FetchErrorCode.POLICY, 'This older session has no durable job; inspect its report before deliberately starting a new run.')
        if meta.get('workspace') != str(root):
            raise AcquisitionError(FetchErrorCode.WORKSPACE, 'Switch to the original research workspace before resuming this session.')
        meta['process_owner'] = owner
        connection.execute("UPDATE research_sessions SET status='pending', stage='queued', error='', meta_json=?, updated_at=? WHERE id=?", (json.dumps(meta), time.time(), session_id))
        connection.commit()
        return _row_to_session(connection.execute('SELECT * FROM research_sessions WHERE id=?', (session_id,)).fetchone())
    finally:
        try:
            if connection.in_transaction:
                connection.rollback()
        finally:
            connection.close()


def create_session(
    topic: str,
    *,
    depth: str = "deep",
    max_sources: int = 8,
    meta: dict[str, Any] | None = None,
) -> ResearchSession:
    now = time.time()
    sid = f"rs_{uuid.uuid4().hex[:16]}"
    sess = ResearchSession(
        id=sid,
        topic=(topic or "").strip(),
        depth=(depth or "deep").strip().lower(),
        status="pending",
        stage="queued",
        message="Queued",
        max_sources=max(2, min(15, int(max_sources or 8))),
        created_at=now,
        updated_at=now,
        meta={**(meta or {}), "process_owner": _process_owner()},
        tenant_id=_session_space(),
        asker=_session_author(),
    )
    c = _conn()
    try:
        c.execute('BEGIN IMMEDIATE')
        _bounded_session_row(sess)
        _history_admission(c)
        c.execute(
            """
            INSERT INTO research_sessions
            (id, topic, depth, status, stage, message, log_json, report_path,
             summary, error, sources, max_sources, rubric_score, rubric_ok,
             created_at, updated_at, meta_json, archived, tenant_id, asker, thread_ids)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                sess.id,
                sess.topic,
                sess.depth,
                sess.status,
                sess.stage,
                sess.message,
                "[]",
                "",
                "",
                "",
                0,
                sess.max_sources,
                None,
                None,
                sess.created_at,
                sess.updated_at,
                json.dumps(sess.meta),
                0,
                sess.tenant_id,
                sess.asker,
                _with_chat(None),
            ),
        )
        c.commit()
    finally:
        try:
            if c.in_transaction:
                c.rollback()
        finally:
            c.close()
    return sess


def _chat_search_key(topic: str, space: str, asker: str = "") -> str:
    """The id part of a chat search's row: one row per topic, per space and,
    in a space with several people, per person (Spaces S1-16 and its
    follow-up). The owner's space -- one person -- keeps the ids it had: 12
    hex digits of the topic alone. Every other space's key starts with
    ``s`` (never a hex digit) and hashes the space, the person and the topic
    as one JSON list, so no topic of the owner's, and no other space's or
    person's, can give the same id."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    if space in ("", OWNER_SPACE_ID):
        return hashlib.md5(topic.encode(), usedforsecurity=False).hexdigest()[:12]
    named = json.dumps([space, asker, topic], ensure_ascii=False)
    return "s" + hashlib.sha256(named.encode()).hexdigest()[:16]


def _session_space() -> str:
    """The space a session made now belongs to (Spaces S1-16): the bound
    scope's, else the bound tenant, else ``""``. Once Spaces is enforced,
    work bound to no space makes no session: ``NoSpaceBound`` (it became the
    owner's, follow-up). A ContextVar read: no I/O."""
    from kazma_core.spaces.scope import current_scope, require_space_id, spaces_enforced
    from kazma_core.tenant_context import get_current_tenant_id

    if spaces_enforced():
        return require_space_id()
    bound = current_scope()
    return bound.space_id if bound is not None else (get_current_tenant_id() or "")


def _session_author() -> str:
    """The person a session made now is for, ``""`` when none is bound (a
    stamp, always written). A ContextVar read: no I/O."""
    from kazma_core.spaces.scope import bound_asker

    return bound_asker() or ""


#: How many chats a row names. A question asked in more than that is a row no
#: take-out removes.
_CHATS_NAMED = 200


def _with_chat(held: str | None) -> str:
    """The chats (threads) a session was made in, as its row keeps them, with
    the running one added; *held* None: a new row. ``""`` in the list is a chat
    that is not known -- a row from before 25 Nov 2026, a search outside a
    chat. Never shown. A ContextVar read: no I/O."""
    from kazma_core.safety.hitl import get_current_thread_id

    chats = [] if held is None else _chats(held)
    asked_in = str(get_current_thread_id() or "")
    if asked_in not in chats:
        chats.append(asked_in)
    return json.dumps(["", *chats[-_CHATS_NAMED:]] if len(chats) > _CHATS_NAMED else chats)


def _chats(held: str) -> list[str]:
    try:
        chats = json.loads(held or "")
    except ValueError:
        return [""]
    return [str(chat) for chat in chats] if isinstance(chats, list) else [""]


def forget_chat_searches(thread_id: str, *, space: str) -> int:
    """The web searches a chat made leave *space* with it (a chat taken out of
    a shared space, Spaces S2-12): the questions the person asked Kazma there,
    which any member read on the Research page. A search another chat made
    too stays, as that chat's; a report stays, a file of the space. Returns
    how many were removed. Idempotent. Blocking."""
    if not thread_id or not _db_path().exists():
        return 0
    removed = 0
    with closing(_conn()) as c, c:
        c.execute("BEGIN IMMEDIATE")
        rows = c.execute(
            "SELECT id, thread_ids FROM research_sessions WHERE depth = 'chat' AND tenant_id = ? "
            "AND instr(thread_ids, ?) > 0", (space, json.dumps(thread_id))).fetchall()
        for row in rows:
            others = [chat for chat in _chats(row["thread_ids"]) if chat != thread_id]
            if others:
                c.execute("UPDATE research_sessions SET thread_ids = ? WHERE id = ?", (json.dumps(others), row["id"]))
            else:
                removed += c.execute("DELETE FROM research_sessions WHERE id = ?", (row["id"],)).rowcount
    return removed


def session_to_change(session_id: str) -> tuple[ResearchSession | None, str]:
    """The session *session_id* when the caller may change it -- archive,
    restore, delete, cancel, resume -- and ``""``; else ``(None, code)``:
    ``not_found`` (unknown, or another space's), ``not_yours`` (a member
    who may only read it: decision N32, the author and the space's owner
    change shared research) or ``identity_unavailable``. Every route and
    tool that changes a session asks it. Blocking."""
    from kazma_core.spaces.identity import IdentityUnavailable
    from kazma_core.spaces.shared_work import NOT_YOURS, may_change

    sess = session_here(session_id)
    if sess is None:
        return None, "not_found"
    try:
        allowed = may_change(sess.tenant_id, sess.asker)
    except IdentityUnavailable:
        logger.warning("[research_session] who may change %s cannot be read; refused", session_id)
        return None, "identity_unavailable"
    return (sess, "") if allowed else (None, NOT_YOURS)


def _row_is_this_spaces(row: sqlite3.Row, space: str) -> bool:
    """Whether a chat search's existing row is this space's: always while
    Spaces is not enforced; once it is, a row of another space is a miss and
    is never written (follow-up). A row from before Spaces is the owner's."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced

    if not spaces_enforced():
        return True
    keys = row.keys()
    held = (str(row["tenant_id"] or "") if "tenant_id" in keys else "") or OWNER_SPACE_ID
    return held == (space or OWNER_SPACE_ID)


def _spaces_shown() -> tuple[str, ...] | None:
    """The tenants this request's space sees, or None for every session (an
    install that does not enforce Spaces). The owner's space also holds the
    sessions from before Spaces (no tenant). ``NoSpaceBound`` with nothing
    bound once enforced."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID, require_space_id, spaces_enforced

    if not spaces_enforced():
        return None
    here = require_space_id()
    return (here, "") if here == OWNER_SPACE_ID else (here,)


def session_here(session_id: str) -> ResearchSession | None:
    """The session *session_id* as the request's space may see it: None for
    an unknown one and, once Spaces is enforced, for another space's -- one
    answer for both (S1-16)."""
    sess = get_session(session_id)
    shown = _spaces_shown()
    if sess is None or (shown is not None and sess.tenant_id not in shown):
        return None
    return sess


def get_session(session_id: str) -> ResearchSession | None:
    c = _conn()
    try:
        _reconcile_sessions(c)
        r = c.execute(
            "SELECT * FROM research_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return _row_to_session(r) if r else None
    finally:
        c.close()


# A chat search: a row a chat tool's web search recorded (depth "chat"), or
# an older row without a known depth whose id says so -- the page's
# sessionTag rule (research.js).
_CHAT_SEARCH_SQL = (
    "(depth = 'chat' OR (COALESCE(depth, '') NOT IN ('brief', 'deep')"
    " AND id LIKE 'rs\\_chat\\_%' ESCAPE '\\'))"
)
SESSION_KINDS = {
    "research": f"NOT {_CHAT_SEARCH_SQL}",
    "search": _CHAT_SEARCH_SQL,
}


def list_sessions(
    *, limit: int = 50, archived: bool | None = False, kind: str | None = None
) -> list[ResearchSession]:
    """List sessions newest-first.

    Default excludes archived (the active list); ``archived=True`` returns
    only archived (the panel's Archived tab); ``archived=None`` returns
    everything. ``kind="research"`` keeps research runs (deep and brief),
    ``kind="search"`` the web searches the chat recorded; None both. A
    page of the newest 30 rows held 26 chat searches and four research
    runs (live 2026-10-31), so each kind is asked for on its own.
    """
    if kind is not None and kind not in SESSION_KINDS:
        raise ValueError(f"unknown session kind: {kind!r}")
    c = _conn()
    try:
        _reconcile_sessions(c)
        clauses: list[str] = []
        if archived is not None:
            # NULL (pre-migration rows / INSERT that omitted the column)
            # must not vanish from the active list (audit T-5).
            if archived:
                clauses.append("COALESCE(archived, 0) = 1")
            else:
                clauses.append("COALESCE(archived, 0) = 0")
        if kind is not None:
            clauses.append(SESSION_KINDS[kind])
        params: list[Any] = []
        shown = _spaces_shown()
        if shown is not None:
            # A space's list is its own (Spaces S1-16).
            clauses.append("COALESCE(tenant_id, '') IN (" + ",".join("?" * len(shown)) + ")")
            params.extend(shown)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = c.execute(
            f"""
            SELECT * FROM research_sessions
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, max(1, min(200, int(limit or 50)))),  # static WHERE, values bound
        ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        c.close()


def archive_session(session_id: str, archived: bool = True) -> ResearchSession | None:
    """Archive / unarchive a session (soft hide from the main list)."""
    return update_session(session_id, archived=archived)


def delete_session(session_id: str) -> bool:
    """Delete settled history; request cancellation and refuse active deletion."""
    with _RUNNING_LOCK:
        if session_id in _RUNNING:
            cancel_session(session_id)
            raise ResearchSessionBusyError(
                "Research cancellation is still in progress; retry deletion after cleanup."
            )
    sess = get_session(session_id)
    if sess is None:
        return False
    if sess.status in ("running", "pending", "cancelling"):
        cancel_session(session_id)
    with _SUBS_LOCK:
        _SUBS.pop(session_id, None)
    c = _conn()
    try:
        cur = c.execute("DELETE FROM research_sessions WHERE id=?", (session_id,))
        c.commit()
        return bool(cur.rowcount)
    finally:
        c.close()


def update_session(session_id: str, **fields: Any) -> ResearchSession | None:
    c = _conn()
    try:
        # Read and write under one SQLite writer transaction: a stale progress
        # writer cannot undo a cancellation or a terminal commit.
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM research_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return None
        sess = _row_to_session(row)
        requested = fields.get("status", sess.status)
        terminal = {"done", "partial", "error", "cancelled", "interrupted"}
        blocked = (sess.status in terminal and requested != sess.status) or (
            sess.status == "cancelling" and requested != "cancelled"
        )
        if blocked:
            fields = {k: v for k, v in fields.items() if k == "archived"}
        for k, v in fields.items():
            if hasattr(sess, k) and k not in ("id", "created_at"):
                setattr(sess, k, v)
        _bounded_session_row(sess)
        _rows, budget, which = _allowance(sess.depth == "chat")
        size = c.execute(f"SELECT COALESCE(SUM({_ROW_BYTES}),0) FROM research_sessions WHERE id != ? AND {which}", (session_id,)).fetchone()[0]
        incoming = len(json.dumps(sess.log).encode()) + len(sess.summary.encode()) + len(json.dumps(sess.meta).encode()) + len(sess.error.encode()) + len(sess.topic.encode())
        if size + incoming > budget:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Research session storage budget exhausted; retained history must be reviewed.')
        sess.updated_at = max(time.time(), sess.updated_at + 0.000001)
        c.execute(
            """
            UPDATE research_sessions SET
              topic=?, depth=?, status=?, stage=?, message=?,
              log_json=?, report_path=?, summary=?, error=?,
              sources=?, max_sources=?, rubric_score=?, rubric_ok=?,
              updated_at=?, meta_json=?, archived=?
            WHERE id=?
            """,
            (
                sess.topic,
                sess.depth,
                sess.status,
                sess.stage,
                sess.message,
                json.dumps([str(entry)[:1024] for entry in sess.log[-200:]]),
                sess.report_path,
                sess.summary,
                sess.error,
                sess.sources,
                sess.max_sources,
                sess.rubric_score,
                (None if sess.rubric_ok is None else (1 if sess.rubric_ok else 0)),
                sess.updated_at,
                json.dumps(sess.meta or {}),
                1 if sess.archived else 0,
                sess.id,
            ),
        )
        c.commit()
    finally:
        try:
            if c.in_transaction:
                c.rollback()
        finally:
            c.close()
    # Broadcast
    event = {
        "type": "progress",
        "session_id": sess.id,
        "status": sess.status,
        "stage": sess.stage,
        "message": sess.message,
        "sources": sess.sources,
        "report_path": sess.report_path,
        "rubric_score": sess.rubric_score,
        "rubric_ok": sess.rubric_ok,
        "updated_at": sess.updated_at,
    }
    _publish(sess.id, event)
    return sess


def subscribe_progress(
    session_id: str, *, loop: asyncio.AbstractEventLoop | None = None
) -> asyncio.Queue:
    """A queue of *session_id*'s progress events, read on *loop*.

    *loop* defaults to the running loop. Given explicitly, this may run in a
    worker thread, which keeps the snapshot read (SQLite) off the loop. The
    session as it stands is queued first, as a ``snapshot`` event.
    """
    loop = loop or asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    with _SUBS_LOCK:
        _SUBS.setdefault(session_id, []).append((loop, q))
    s = get_session(session_id)
    if s:
        _deliver(loop, q, {"type": "snapshot", "session": s.to_dict()})
    return q


def unsubscribe_progress(session_id: str, q: asyncio.Queue) -> None:
    with _SUBS_LOCK:
        kept = [(lp, qq) for lp, qq in _SUBS.get(session_id, ()) if qq is not q]
        if kept:
            _SUBS[session_id] = kept
        else:
            _SUBS.pop(session_id, None)


def cancel_session(session_id: str) -> ResearchSession | None:
    """Request cancellation; terminal cancellation acknowledges joined cleanup."""
    with _RUNNING_LOCK:
        sess = get_session(session_id)
        if not sess or sess.status in ("done", "partial", "error", "cancelled", "interrupted", "cancelling"):
            return sess
        task = _RUNNING.get(session_id)
        if task is not None:
            final = update_session(
                session_id, status="cancelling", stage="cancelling",
                message="Cancellation requested; waiting for cleanup", error="",
            )
            if not task.done() and session_id not in _SETTLING:
                _cancel_on_its_loop(task)
            return final
        owner = sess.meta.get('process_owner')
        if owner and owner[0] != os.getpid():
            from kazma_core.web_acquire.crawl_jobs import _alive
            if _alive(owner):
                raise ResearchSessionBusyError('This research session is owned by another live process; cancel it through that owner.')
        # No execution owner exists in this process (for example an unstarted
        # row). This does not resume work or repeat effects after a restart.
        return _complete_cancellation(session_id)


def _complete_cancellation(session_id: str) -> ResearchSession | None:
    final = update_session(
        session_id, status="cancelled", stage="cancelled",
        message="Cancelled", error="",
    )
    if final:
        _publish(session_id, {
            "type": "done", "session_id": session_id,
            "status": final.status, "session": final.to_dict(),
        })
    return final


@bounded_session(lambda session: _running_task(session.id))
async def start_deep_research(
    topic: str,
    *,
    depth: str = "deep",
    max_sources: int = 8,
    export_docx: bool = False,
    resume_session_id: str | None = None,
) -> ResearchSession:
    """Create session and run pipeline in background task.

    Every session-store call and the report scoring run in a worker
    thread: this runs on the event loop, and the progress callback fires
    for every pipeline message (AGENTS §35).
    """
    from kazma_core.workspace.binding import resolve_active_root
    root = await call(resolve_active_root)
    if resume_session_id is not None:
        sess = await call(_claim_session, resume_session_id, _process_owner(), root,
            on_cancel=lambda claimed: _complete_cancellation(claimed.id))
        export_docx = bool(sess.meta.get('export_docx'))
    else:
        sess = await call(create_session, topic, depth=depth, max_sources=max_sources,
            meta=dict(workspace=str(Path(root).resolve()), export_docx=export_docx),
            on_cancel=lambda created: update_session(created.id, status='cancelled', stage='cancelled', message='Cancelled before background dispatch'))
    if not sess.topic:
        await call(update_session, sess.id, status="error", error="topic required", stage="error")
        return await call(get_session, sess.id) or sess

    async def _run() -> None:
        import re
        from kazma_core.web_acquire.research_pipeline_job import pipeline_observer, completed_summary

        async def link(job):
            current = await call(get_session, sess.id)
            meta = dict(current.meta)
            meta.update(durable_job_id=job.job.job_id, workspace=str(job.root))
            linked = await call(update_session, sess.id, meta=meta)
            if linked.status != 'running' or linked.meta.get('durable_job_id') != job.job.job_id:
                raise asyncio.CancelledError
        observer_token = pipeline_observer.set(link)

        async def progress_cb(stage: str, message: str) -> None:
            s = await call(get_session, sess.id)
            log = list(s.log) if s else []
            log.append(f"[{stage}] {message}")
            fields: dict[str, Any] = {
                "status": "running",
                "stage": stage,
                "message": message,
                "log": log,
            }
            # Pipeline emits "Report ready: <path>" on done stage
            ready = re.search(r"Report ready:\s*(.+)$", message or "")
            if ready:
                fields["report_path"] = ready.group(1).strip()
            # "Fetching up to N pages…" / "Digesting N sources…"
            src_m = re.search(r"(\d+)\s+sources", message or "", re.I)
            if src_m:
                fields["sources"] = int(src_m.group(1))
            await call(update_session, sess.id, **fields)

        try:
            await call(update_session,
                sess.id, status="running", stage="start", message="Starting pipeline…"
            )
            from kazma_core.tools.research_pipeline import run_research_pipeline

            result = await call(completed_summary, sess.meta['durable_job_id'], root) if resume_session_id else None
            if result is None:
                result = await run_research_pipeline(
                    sess.topic,
                    depth=sess.depth,
                    max_sources=sess.max_sources,
                    progress_cb=progress_cb,
                    export_docx=export_docx,
                    **({'resume_job_id': sess.meta['durable_job_id']} if resume_session_id else {}),
                )
            report_path = ""
            sources = 0
            m = re.search(r"\*\*Report:\*\*\s*`([^`]+)`", result or "")
            if m:
                report_path = m.group(1)
            m2 = re.search(r"\*\*Sources acquired:\*\*\s*(\d+)", result or "")
            if m2:
                sources = int(m2.group(1))
            cur = await call(get_session, sess.id)
            if not report_path and cur and cur.report_path:
                report_path = cur.report_path
            if not sources and cur and cur.sources:
                sources = cur.sources
            # Parse rubric from summary line: **Rubric:** 85/100 (pass)
            rubric_score: float | None = None
            rubric_ok: bool | None = None
            m3 = re.search(
                r"\*\*Rubric:\*\*\s*([\d.]+)/100\s*\(([^)]+)\)", result or ""
            )
            if m3:
                try:
                    rubric_score = float(m3.group(1))
                except ValueError:
                    rubric_score = None
                rubric_ok = "pass" in (m3.group(2) or "").lower()
            # Prefer scoring the report file when available
            if report_path:
                try:
                    from kazma_core.tools.research_eval import score_report_file
                    from kazma_core.tools.research_pipeline import resolve_report_file

                    scored = None
                    target = await call(
                        resolve_report_file, report_path, stored=True
                    )
                    if target is not None:
                        scored = await call(score_report_file, target)
                    if scored is not None:
                        rubric_score = float(scored.score)
                        rubric_ok = bool(scored.ok)
                except Exception:
                    logger.debug(
                        "[research_session] rubric file score skipped", exc_info=True
                    )
            if (result or "").startswith("Error:"):
                log = list(cur.log) if cur else []
                log.append(result[:500])
                await call(update_session,
                    sess.id,
                    status="error",
                    stage="error",
                    message=(result or "")[:200],
                    error=(result or "")[:2000],
                    summary=(result or "")[:4000],
                    log=log,
                    rubric_score=rubric_score,
                    rubric_ok=rubric_ok,
                )
            else:
                partial = (result or '').startswith('# Deep research partial')
                await call(update_session,
                    sess.id,
                    status="partial" if partial else "done",
                    stage="partial" if partial else "done",
                    message="Partial; inspect missing or uncertain sources" if partial else "Complete",
                    summary=(result or "")[:8000],
                    report_path=report_path,
                    sources=sources,
                    rubric_score=rubric_score,
                    rubric_ok=rubric_ok,
                )
            final = await call(get_session, sess.id)
            if final and final.status == "cancelling":
                # Even a pipeline that suppresses CancelledError cannot turn a
                # previously accepted cancellation into a success.
                await call(_complete_cancellation, sess.id)
            else:
                _publish(sess.id, {
                    "type": "done", "session_id": sess.id,
                    "status": final.status if final else "error",
                    "session": final.to_dict() if final else None,
                })
        except asyncio.CancelledError:
            # Pipeline/native workers have joined before control reaches here.
            # call() also joins this terminal commit under repeated cancellation.
            await call(_complete_cancellation, sess.id)
            raise
        except Exception as exc:
            logger.exception("[research_session] pipeline failed")
            # Friendly short message for UI; full detail in error
            brief = str(exc)[:200]
            if "Error:" in brief:
                brief = brief.split("Error:", 1)[-1].strip()[:200]
            await call(update_session,
                sess.id,
                status="error",
                stage="error",
                error=str(exc)[:2000],
                message=f"Failed: {brief}" if brief else "Pipeline failed",
            )
            err_sess = await call(get_session, sess.id)
            if err_sess and err_sess.status == "cancelling":
                await call(_complete_cancellation, sess.id)
                return
            _publish(
                sess.id,
                {
                    "type": "error",
                    "session_id": sess.id,
                    "error": str(exc)[:500],
                    "session": err_sess.to_dict() if err_sess else None,
                },
            )
        finally:
            pipeline_observer.reset(observer_token)
            with _RUNNING_LOCK:
                _RUNNING.pop(sess.id, None)

    task = asyncio.create_task(_run())
    with _RUNNING_LOCK:
        _RUNNING[sess.id] = task

    def observed(completed):
        if not completed.cancelled() and completed.exception() is not None:
            logger.error('[research_session] background task ended before terminal persistence: %s',
                         type(completed.exception()).__name__)

    async def settle_unstarted():
        try:
            await call(_complete_cancellation, sess.id)
        finally:
            with _RUNNING_LOCK:
                _RUNNING.pop(sess.id, None)
                _SETTLING.discard(sess.id)

    def finished(completed):
        # Cancellation before the first coroutine step bypasses its try/finally.
        # Transfer ownership to a joined terminal writer; bounded_session keeps
        # its lease through this replacement task too.
        with _RUNNING_LOCK:
            if _RUNNING.get(sess.id) is completed:
                _SETTLING.add(sess.id)
                cleanup = asyncio.create_task(settle_unstarted())
                _RUNNING[sess.id] = cleanup
                cleanup.add_done_callback(observed)
        observed(completed)

    task.add_done_callback(finished)
    return sess


# Cap for chat-tool result snapshots. The old 500-char cap discarded the
# full output at WRITE time, so the research detail view could never show
# more than a teaser. 200K chars ≈ 50K tokens — generous but bounded.
_CHAT_RESULT_MAX = 200_000


def record_chat_research(
    topic: str,
    *,
    tool_name: str = "web_search",
    result_text: str = "",
    session_id: str | None = None,
    meta: dict[str, Any] | None = None,
    result_count: int | None = None,
) -> ResearchSession:
    """Record a research activity from chat tools into research_sessions.db.

    Ensures all chat research queries populate dynamically in the Research Tab list.
    Returns None (no row) when called inside :func:`suppress_chat_recording`
    (deep-pipeline sub-queries log into their parent session instead).
    *result_count* (the results the search returned) is kept in the row's
    meta as ``results`` with the snapshot it counts: the Research page says
    "web search · 8 results", never "1 source".
    """
    if _chat_record_suppressed.get():
        logger.debug(
            "[research_session] chat recording suppressed (pipeline sub-query): %s",
            (topic or "")[:60],
        )
        return None
    now = time.time()
    space = _session_space()
    author = _session_author()
    sid = session_id or (f"rs_chat_{_chat_search_key(topic, space, author)}" if topic
                         else f"rs_chat_{uuid.uuid4().hex[:12]}")
    c = _conn()
    try:
        c.execute('BEGIN IMMEDIATE')
        cur = c.execute("SELECT * FROM research_sessions WHERE id = ?", (sid,))
        row = cur.fetchone()
        if row and not _row_is_this_spaces(row, space):
            # Another space's row under this id: a miss, never written.
            sid, row = f"rs_chat_{uuid.uuid4().hex[:12]}", None
        if row:
            sess = _row_to_session(row)
            sess.updated_at = now
            sess.status = "done"
            sess.stage = "complete"
            sess.message = f"Research via {tool_name}"
            sess.sources = max(sess.sources, 1)
            entry = f"Tool {tool_name} executed for '{topic[:60]}'"
            if entry not in sess.log:
                sess.log.append(entry)
            if result_text:
                # Keep the richest snapshot — the same topic can be re-queried
                # with fuller results and the detail view/export should show
                # the most complete output.
                new_summary = result_text[:_CHAT_RESULT_MAX]
                if not sess.summary or len(new_summary) > len(sess.summary):
                    sess.summary = new_summary
                    if result_count is not None:
                        sess.meta = {**(sess.meta or {}), "results": int(result_count)}
            c.execute("UPDATE research_sessions SET thread_ids=? WHERE id=?", (_with_chat(row["thread_ids"]), sess.id))
            c.commit()
            update_session(sess.id, status=sess.status, stage=sess.stage, message=sess.message, sources=sess.sources, summary=sess.summary, meta=sess.meta)
            return sess
        else:
            sess = ResearchSession(
                id=sid,
                topic=topic[:120] if topic else f"Chat research ({tool_name})",
                depth="chat",
                status="done",
                stage="complete",
                message=f"Executed in chat ({tool_name})",
                log=[f"Tool {tool_name} executed for '{topic[:60]}'"],
                summary=result_text[:_CHAT_RESULT_MAX] if result_text else "",
                sources=1,
                created_at=now,
                updated_at=now,
                meta={
                    **(meta or {"source": "chat", "tool": tool_name}),
                    **({"results": int(result_count)} if result_count is not None else {}),
                },
                tenant_id=space,
                asker=author,
            )
            _bounded_session_row(sess)
            _history_admission(c, chat=True)
            c.execute(
                """
                INSERT INTO research_sessions
                (id, topic, depth, status, stage, message, log_json, report_path,
                 summary, error, sources, max_sources, rubric_score, rubric_ok,
                 created_at, updated_at, meta_json, archived, tenant_id, asker, thread_ids)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sess.id,
                    sess.topic,
                    sess.depth,
                    sess.status,
                    sess.stage,
                    sess.message,
                    json.dumps(sess.log),
                    sess.report_path,
                    sess.summary,
                    sess.error,
                    sess.sources,
                    sess.max_sources,
                    sess.rubric_score,
                    1 if sess.rubric_ok else (0 if sess.rubric_ok is False else None),
                    sess.created_at,
                    sess.updated_at,
                    json.dumps(sess.meta),
                    0,
                    sess.tenant_id,
                    sess.asker,
                    _with_chat(None),
                ),
            )
            c.commit()
            return sess
    finally:
        try:
            if c.in_transaction:
                c.rollback()
        finally:
            c.close()

