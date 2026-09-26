"""Kazma's conversation history, read from wherever it is actually stored.

Two memory paths read old conversations: the past-chats fallback
(:mod:`kazma_core.memory.transcript_recall`, a search) and the recovery of
memories an old archive rule erased (:mod:`kazma_core.memory.rehydrate`,
one conversation's turns and the notes the agent saved). Both read:

* **the chat store the web UI writes** -- Postgres ``kazma_chat_sessions``
  when the database backend is Postgres, else SQLite ``chat_sessions.db`` --
  and beside it ``chat_sessions_spool.db``, where a save the primary refused
  waits (``kazma_ui.session_spool``, AGENTS.md §36);
* **the LangGraph checkpoints**, whose ``messages`` channel keeps every
  version of a thread's messages (Postgres ``checkpoint_blobs``, or the
  ``checkpoints`` table of the SQLite saver).

kazma-core may not import kazma-ui's SessionManager (layering), so this reads
the tables directly and read-only. Until 2026-09-26 the fallback opened only
the SQLite file -- on a Postgres install a leftover from before the switch
(the live one held 5 sessions from July, against 258 in Postgres) -- and
scored only the 400 most recent matches, with no tenant filter.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from kazma_core.db.pg_helpers import store_errors
from kazma_core.paths import chat_spool_db

logger = logging.getLogger(__name__)

__all__ = [
    "STORE_ERRORS",
    "checkpoint_message_versions",
    "conversations_for",
    "normalize_message",
    "search_sessions",
    "sessions_changed_since",
    "threads_containing",
]

#: What reading a chat or checkpoint store can raise (``pg_helpers.store_errors``).
STORE_ERRORS = store_errors()

#: What decoding one stored checkpoint version can raise: bad bytes, or a
#: message class that has changed since it was written.
_DECODE_ERRORS = (ValueError, TypeError, KeyError, AttributeError, ImportError)

_ROLES = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


def normalize_message(m: Any) -> dict[str, Any]:
    """A chat message as a dict with ``role`` and ``content`` (and
    ``tool_calls`` when it has any), from a dict or a LangChain message."""
    if isinstance(m, dict):
        return m
    role = str(getattr(m, "type", "") or "")
    out: dict[str, Any] = {"role": _ROLES.get(role, role), "content": getattr(m, "content", "")}
    calls = getattr(m, "tool_calls", None)
    if calls:
        out["tool_calls"] = list(calls)
    return out


def _never_created(exc: BaseException) -> bool:
    """A store whose table was never created is an empty store, not an
    unreadable one: an install that has not used checkpoints yet has no
    history to offer, and that is an answer, not an outage."""
    if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc):
        return True
    try:
        from psycopg import errors as pg_errors
    except ImportError:
        return False
    return isinstance(exc, pg_errors.UndefinedTable)


def _postgres() -> bool:
    try:
        from kazma_core.db.pg_helpers import use_postgres

        return bool(use_postgres())
    except ImportError:
        return False


def _sessions_path(sqlite_path: Path | None) -> Path:
    if sqlite_path is not None:
        return Path(sqlite_path)
    from kazma_core.paths import data_dir

    return Path(data_dir()) / "chat_sessions.db"


def _json(val: Any, default: Any) -> Any:
    if val is None:
        return default
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except (TypeError, ValueError):
        return default


def _ro(path: Path) -> sqlite3.Connection | None:
    if not Path(path).exists():
        return None
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _spooled(sessions_path: Path) -> list[dict[str, Any]]:
    """Every spooled session payload (a save the primary refused), newest first.

    No spool file is an empty spool; a spool that cannot be read raises.
    """
    conn = _ro(chat_spool_db(sessions_path))
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT tenant_id, session_id, payload FROM spool ORDER BY spooled_at DESC"
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        payload = _json(r["payload"], {})
        if isinstance(payload, dict):
            out.append(
                {
                    **payload,
                    "tenant_id": str(r["tenant_id"]),
                    "session_id": str(r["session_id"]),
                }
            )
    return out


# ── One conversation ───────────────────────────────────────────────────────


def conversations_for(
    key: str, *, sqlite_path: Path | None = None, strict: bool = False
) -> list[dict[str, Any]]:
    """Every stored copy of the conversation *key* names, as a session id or
    a thread id: ``[{"session_id", "thread_id", "messages"}]``.

    The primary store's row and a spooled copy are both returned -- a caller
    looking for one turn wants every version. Any tenant: a caller that
    matters must verify what it finds (memory recovery checks a SHA-256).
    A store that cannot be read gives ``[]``, or raises when *strict*.
    """
    if not key:
        return []
    out: list[dict[str, Any]] = []
    sessions = _sessions_path(sqlite_path)
    try:
        for s in _spooled(sessions):
            if key in (s.get("session_id"), s.get("thread_id")):
                out.append(_conversation(s))
        if sqlite_path is None and _postgres():
            from kazma_core.db.pg_helpers import get_pool

            rows = get_pool().execute(
                "SELECT session_id, thread_id, messages FROM kazma_chat_sessions "
                "WHERE session_id = %s OR thread_id = %s",
                [key, key],
            )
        else:
            conn = _ro(sessions)
            if conn is None:
                return out
            try:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT session_id, thread_id, messages FROM sessions "
                        "WHERE session_id = ? OR thread_id = ?",
                        (key, key),
                    ).fetchall()
                ]
            finally:
                conn.close()
        out.extend(_conversation(r) for r in rows)
    except STORE_ERRORS as exc:
        if _never_created(exc):
            return out
        if strict:
            raise
        logger.debug("[chat_history] conversation %s unreadable", key, exc_info=True)
    return out


def sessions_changed_since(
    after: tuple[str, str] = ("", ""),
    *,
    limit: int = 100,
    sqlite_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Sessions updated after the cursor *after* = ``(updated_at, session_id)``,
    oldest first, at most *limit*: ``{tenant_id, session_id, thread_id,
    created_at, updated_at, messages}``. The cursor is composite so sessions
    sharing an ``updated_at`` across a page boundary are not skipped; ``("",
    "")`` starts from the beginning (rows without an ``updated_at`` first). A
    session with a newer copy in the save spool is returned as that copy.
    Raises when a store cannot be read -- the caller keeps its cursor.
    """
    stamp, key = after
    sessions = _sessions_path(sqlite_path)
    try:
        if sqlite_path is None and _postgres():
            from kazma_core.db.pg_helpers import get_pool

            rows = get_pool().execute(
                "SELECT tenant_id, session_id, thread_id, created_at, updated_at, messages "
                "FROM kazma_chat_sessions WHERE COALESCE(updated_at, '') > %s "
                "OR (COALESCE(updated_at, '') = %s AND session_id > %s) "
                "ORDER BY COALESCE(updated_at, ''), session_id LIMIT %s",
                [stamp, stamp, key, int(limit)],
            )
        else:
            conn = _ro(sessions)
            if conn is None:
                rows = []
            else:
                try:
                    rows = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT tenant_id, session_id, thread_id, created_at, updated_at, "
                            "messages FROM sessions WHERE COALESCE(updated_at, '') > ? "
                            "OR (COALESCE(updated_at, '') = ? AND session_id > ?) "
                            "ORDER BY COALESCE(updated_at, ''), session_id LIMIT ?",
                            (stamp, stamp, key, int(limit)),
                        ).fetchall()
                    ]
                finally:
                    conn.close()
    except STORE_ERRORS as exc:
        if not _never_created(exc):
            raise
        rows = []
    spooled = {(s["tenant_id"], s["session_id"]): s for s in _spooled(sessions)}
    out: list[dict[str, Any]] = []
    for r in rows:
        copy = spooled.get((str(r.get("tenant_id") or ""), str(r.get("session_id") or "")))
        if copy is not None and str(copy.get("updated_at") or "") >= str(r.get("updated_at") or ""):
            r = {**r, "messages": copy.get("messages") or []}
        msgs = _json(r.get("messages"), [])
        out.append({
            "tenant_id": str(r.get("tenant_id") or "default"),
            "session_id": str(r.get("session_id") or ""),
            "thread_id": str(r.get("thread_id") or ""),
            "created_at": str(r.get("created_at") or ""),
            "updated_at": str(r.get("updated_at") or ""),
            "messages": msgs if isinstance(msgs, list) else [],
        })
    return out


def _conversation(row: dict[str, Any]) -> dict[str, Any]:
    msgs = _json(row.get("messages"), [])
    return {
        "session_id": str(row.get("session_id") or ""),
        "thread_id": str(row.get("thread_id") or ""),
        "messages": msgs if isinstance(msgs, list) else [],
    }


# ── Checkpoint history ─────────────────────────────────────────────────────


def checkpoint_message_versions(
    thread_id: str, *, containing: bytes | None = None, strict: bool = False
) -> list[list[dict[str, Any]]]:
    """Every stored version of a LangGraph thread's ``messages`` channel.

    *containing* keeps only versions whose serialized bytes include that
    sequence -- a filter the database applies, so a caller looking for one
    kind of message does not decode a thread's whole history. A version that
    will not decode is skipped; a store that cannot be read gives ``[]``, or
    raises when *strict*.
    """
    if not thread_id:
        return []
    try:
        from kazma_core.checkpoint_serde import kazma_checkpoint_serde

        serde = kazma_checkpoint_serde()
        versions: list[list[dict[str, Any]]] = []
        if _postgres():
            from kazma_core.db.pg_helpers import get_pool

            sql = (
                "SELECT type, blob FROM checkpoint_blobs "
                "WHERE thread_id = %s AND channel = 'messages' AND blob IS NOT NULL"
            )
            params: list[Any] = [thread_id]
            if containing:
                sql += " AND position(%s::bytea in blob) > 0"
                params.append(containing)
            for r in get_pool().execute(sql, params):
                msgs = _decode(serde, str(r["type"]), bytes(r["blob"]))
                if isinstance(msgs, list):
                    versions.append([normalize_message(m) for m in msgs])
            return versions
        from kazma_core.paths import checkpoints_db

        conn = _ro(Path(checkpoints_db()))
        if conn is None:
            return []
        try:
            sql = "SELECT type, checkpoint FROM checkpoints WHERE thread_id = ?"
            args: list[Any] = [thread_id]
            if containing:
                sql += " AND instr(checkpoint, ?) > 0"
                args.append(containing)
            for typ, blob in conn.execute(sql, args):
                ckpt = _decode(serde, str(typ), bytes(blob))
                msgs = ((ckpt or {}).get("channel_values") or {}).get("messages") if isinstance(
                    ckpt, dict
                ) else None
                if isinstance(msgs, list):
                    versions.append([normalize_message(m) for m in msgs])
        finally:
            conn.close()
        return versions
    except STORE_ERRORS as exc:
        if _never_created(exc):
            return []
        if strict:
            raise
        logger.debug("[chat_history] checkpoints of %s unreadable", thread_id, exc_info=True)
        return []


def _decode(serde: Any, typ: str, blob: bytes) -> Any:
    try:
        return serde.loads_typed((typ, blob))
    except _DECODE_ERRORS:  # one undecodable version is skipped, not fatal
        logger.debug("[chat_history] undecodable checkpoint version skipped", exc_info=True)
        return None


def threads_containing(mark: bytes, *, after: str = "", strict: bool = False) -> list[str]:
    """Thread ids, sorted, whose message history contains *mark*, from *after* on.

    One scan of the checkpoint store (the database does the matching); a
    caller walking the result can stop and resume from the last id it did.
    A store that cannot be read gives ``[]``, or raises when *strict*.
    """
    if not mark:
        return []
    try:
        if _postgres():
            from kazma_core.db.pg_helpers import get_pool

            rows = get_pool().execute(
                "SELECT DISTINCT thread_id FROM checkpoint_blobs WHERE channel = 'messages' "
                "AND thread_id > %s AND position(%s::bytea in blob) > 0 ORDER BY thread_id",
                [after, mark],
            )
            return [str(r["thread_id"]) for r in rows]
        from kazma_core.paths import checkpoints_db

        conn = _ro(Path(checkpoints_db()))
        if conn is None:
            return []
        try:
            return [
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id > ? "
                    "AND instr(checkpoint, ?) > 0 ORDER BY thread_id",
                    (after, mark),
                )
            ]
        finally:
            conn.close()
    except STORE_ERRORS as exc:
        if _never_created(exc):
            return []
        if strict:
            raise
        logger.debug("[chat_history] checkpoint scan failed", exc_info=True)
        return []


# ── Search across sessions ─────────────────────────────────────────────────


def _score(title: str, messages: str, words: list[str]) -> int:
    """The SQL score below, for a spooled copy: 5 for a title hit, plus the
    term's occurrences in the messages, at most 4."""
    lt, lm = title.lower(), messages.lower()
    return sum((5 if w in lt else 0) + min(4, lm.count(w)) for w in words)


def search_sessions(
    terms: list[str],
    *,
    tenant_id: str = "default",
    exclude_session_id: str | None = None,
    limit: int = 3,
    sqlite_path: Path | None = None,
) -> list[dict[str, Any]]:
    """The tenant's sessions matching *terms*, best first, over EVERY session.

    Score per term: 5 when the title has it, plus its occurrences in the
    messages (at most 4) -- computed in SQL, so the whole history is ranked
    and only the best rows come back. A spooled copy (a save the database
    refused) is scored the same way and replaces the stored row when it
    scores higher. Rows: ``session_id, thread_id, title, created_at,
    messages`` (text) and ``score``. *sqlite_path* searches that SQLite chat
    file whatever the backend.
    """
    words = [t.lower() for t in terms if t]
    if not words:
        return []
    try:
        sessions = _sessions_path(sqlite_path)
        spooled = [
            s
            for s in _spooled(sessions)
            if s.get("tenant_id") == tenant_id and s.get("session_id") != exclude_session_id
        ]
        fetch = int(limit) + len(spooled)
        if sqlite_path is None and _postgres():
            rows = _search_postgres(words, tenant_id, exclude_session_id, fetch)
        else:
            rows = _search_sqlite(sessions, words, tenant_id, exclude_session_id, fetch)
        best = {str(r["session_id"]): r for r in rows}
        for s in spooled:
            text = json.dumps(s.get("messages") or [], ensure_ascii=False)
            title = str(s.get("title") or "")
            score = _score(title, text, words)
            sid = str(s["session_id"])
            if score and score > int((best.get(sid) or {}).get("score") or 0):
                best[sid] = {
                    "session_id": sid,
                    "thread_id": str(s.get("thread_id") or ""),
                    "title": title,
                    "created_at": str(s.get("created_at") or ""),
                    "messages": text,
                    "score": score,
                }
        ranked = sorted(
            best.values(),
            key=lambda r: (int(r.get("score") or 0), str(r.get("created_at") or "")),
            reverse=True,
        )
        return ranked[: max(1, int(limit))]
    except STORE_ERRORS:  # the fallback is best-effort; an unreadable store finds nothing
        logger.debug("[chat_history] session search failed", exc_info=True)
        return []


def _search_sqlite(
    path: Path, words: list[str], tenant_id: str, exclude: str | None, limit: int
) -> list[dict[str, Any]]:
    conn = _ro(Path(path))
    if conn is None:
        return []
    score_parts: list[str] = []
    score_params: list[Any] = []
    match_parts: list[str] = []
    match_params: list[Any] = []
    for w in words:
        score_parts.append(
            "(CASE WHEN instr(lower(title), ?) > 0 THEN 5 ELSE 0 END"
            " + min(4, (length(lower(messages)) - length(replace(lower(messages), ?, ''))) / ?))"
        )
        score_params.extend([w, w, len(w)])
        match_parts.append("(lower(title) LIKE ? OR lower(messages) LIKE ?)")
        match_params.extend([f"%{w}%", f"%{w}%"])
    where = f"tenant_id = ? AND ({' OR '.join(match_parts)})"
    params: list[Any] = [*score_params, tenant_id, *match_params]
    if exclude:
        where += " AND session_id != ?"
        params.append(exclude)
    try:
        rows = conn.execute(
            f"SELECT session_id, thread_id, title, created_at, messages, "
            f"({' + '.join(score_parts)}) AS score FROM sessions WHERE {where} "
            "ORDER BY score DESC, created_at DESC LIMIT ?",
            [*params, int(limit)],
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _search_postgres(
    words: list[str], tenant_id: str, exclude: str | None, limit: int
) -> list[dict[str, Any]]:
    from kazma_core.db.pg_helpers import get_pool

    score_parts: list[str] = []
    score_params: list[Any] = []
    match_parts: list[str] = []
    match_params: list[Any] = []
    for w in words:
        score_parts.append(
            "(CASE WHEN position(%s in lower(title)) > 0 THEN 5 ELSE 0 END"
            " + LEAST(4, (length(lower(messages::text))"
            " - length(replace(lower(messages::text), %s, ''))) / %s))"
        )
        score_params.extend([w, w, len(w)])
        match_parts.append("(lower(title) LIKE %s OR lower(messages::text) LIKE %s)")
        match_params.extend([f"%{w}%", f"%{w}%"])
    where = f"tenant_id = %s AND ({' OR '.join(match_parts)})"
    params: list[Any] = [*score_params, tenant_id, *match_params]
    if exclude:
        where += " AND session_id != %s"
        params.append(exclude)
    rows = get_pool().execute(
        f"SELECT session_id, thread_id, title, created_at, messages::text AS messages, "
        f"({' + '.join(score_parts)}) AS score FROM kazma_chat_sessions WHERE {where} "
        "ORDER BY score DESC, created_at DESC LIMIT %s",
        [*params, int(limit)],
    )
    return [dict(r) for r in rows]
