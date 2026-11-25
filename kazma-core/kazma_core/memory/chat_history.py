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

import functools
import itertools
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.db.pg_helpers import store_errors
from kazma_core.errors import described_error
from kazma_core.paths import chat_spool_db
from kazma_core.spaces.scope import OWNER_SPACE_ID, space_named_or_bound, space_of_record

logger = logging.getLogger(__name__)

__all__ = [
    "STORE_ERRORS",
    "chat_ids",
    "checkpoint_message_versions",
    "conversations_for",
    "epoch_of",
    "normalize_message",
    "said_since",
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


def epoch_of(value: Any) -> float | None:
    """Epoch seconds of the chat store's ISO timestamps; None when absent."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.timestamp()


def said_since(messages: Any, since: float | None) -> list[Any]:
    """The messages of the turns asked at or after *since*: what was said in
    this space of a chat that was copied here from another one at that moment
    (Spaces S2-12, ``threads.copied_threads``). A turn whose question has no
    time is left out: it is not known to have been said here. *since* None:
    every message."""
    items = list(messages or [])
    if since is None:
        return items
    kept: list[Any] = []
    here = False
    for item in items:
        msg = normalize_message(item)
        if msg.get("role") == "user":
            at = epoch_of(msg.get("ts"))
            here = at is not None and at >= since
        if here:
            kept.append(item)
    return kept


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
    a thread id: ``[{"session_id", "thread_id", "tenant_id", "messages"}]``.

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
                "SELECT session_id, thread_id, tenant_id, messages FROM kazma_chat_sessions "
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
                        "SELECT session_id, thread_id, tenant_id, messages FROM sessions "
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


def _id_rows(keys: list[str], sqlite_path: Path | None) -> list[dict[str, Any]]:
    """``session_id`` and ``thread_id`` of every stored copy (the spool and the
    primary store) that one of *keys* names as either id -- never the
    messages. Raises what the store raises."""
    wanted = set(keys)
    sessions = _sessions_path(sqlite_path)
    rows: list[dict[str, Any]] = [
        {"session_id": s.get("session_id"), "thread_id": s.get("thread_id")}
        for s in _spooled(sessions)
        if s.get("session_id") in wanted or s.get("thread_id") in wanted
    ]
    for start in range(0, len(keys), 400):
        part = keys[start: start + 400]
        if sqlite_path is None and _postgres():
            from kazma_core.db.pg_helpers import get_pool

            rows.extend(get_pool().execute(
                "SELECT session_id, thread_id FROM kazma_chat_sessions "
                "WHERE session_id = ANY(%s) OR thread_id = ANY(%s)",
                [part, part],
            ))
            continue
        conn = _ro(sessions)
        if conn is None:
            break
        try:
            marks = ",".join("?" for _ in part)
            rows.extend(dict(r) for r in conn.execute(
                f"SELECT session_id, thread_id FROM sessions "
                f"WHERE session_id IN ({marks}) OR thread_id IN ({marks})",
                (*part, *part),
            ).fetchall())
        finally:
            conn.close()
    return rows


def ids_of(key: str, *, sqlite_path: Path | None = None) -> list[str]:
    """Every id the chat *key* names is stored under, *key* first: its session
    id and its thread id (a web chat's differ). Just *key* when the store holds
    no copy of it or cannot be read."""
    out = [key] if key else []
    if not key:
        return out
    try:
        rows = _id_rows([key], sqlite_path)
    except STORE_ERRORS as exc:
        if not _never_created(exc):
            logger.debug("[chat_history] ids of %s unreadable", key, exc_info=True)
        return out
    for r in rows:
        for k in (r.get("session_id"), r.get("thread_id")):
            if k and str(k) not in out:
                out.append(str(k))
    return out


def chat_ids(keys: Iterable[str], *, sqlite_path: Path | None = None) -> dict[str, str]:
    """The conversation each key names: ``key -> its session id``.

    A chat is stored under a session id and a thread id, and memory writers
    use either (``forget.chat_keys``), so one conversation can reach memory
    under two keys. A key the store does not hold -- a deleted chat, the
    agent's notes, a probe -- maps to itself, and so does every key when the
    store cannot be read: the worst case is one chat counted as two.
    """
    wanted = sorted({str(k) for k in keys if k})
    out = {k: k for k in wanted}
    if not wanted:
        return out
    try:
        rows = _id_rows(wanted, sqlite_path)
    except STORE_ERRORS as exc:
        if not _never_created(exc):
            logger.debug("[chat_history] chat ids unreadable -- each key is its own chat", exc_info=True)
        return out
    for r in rows:
        session, thread = str(r.get("session_id") or ""), str(r.get("thread_id") or "")
        if not session:
            continue
        for k in (session, thread):
            if k in out:
                out[k] = session
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
            "tenant_id": space_of_record(r.get("tenant_id")),
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
        # Whose chat it is: a reader that writes what it finds into memory
        # keeps to the row's own tenant (memory/text_fill.py).
        "tenant_id": str(row.get("tenant_id") or OWNER_SPACE_ID),  # none: the owner's (N19)
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

#: How many chats' texts a search holds at once: they are fetched in rank
#: order, this many at a time, until enough of them are confirmed.
_TEXTS_AT_ONCE = 15
#: How many chats' texts one search reads at most: 3,000 that were each read
#: and refused took 3 to 10 seconds of a turn.
_TEXTS_READ = 500
#: A word longer than this is looked for as it stands: nobody spells a token,
#: a path or a key another way, and a pattern grows with its word (a 16,000
#: character one is refused by Postgres, and six of 10,000 took 8 seconds).
LONGEST_SPELLED = 64


@dataclass(frozen=True, slots=True)
class _Word:
    """One spelling a search asks for: a substring of the lower-cased text, or
    -- a word the fold writes more than one way (``documents.arabic``) -- a
    pattern over the text as it stands."""

    text: str
    pattern: str = ""

    def count(self, text: str, lowered: str) -> int:
        """How often it is there, at most 4 (what the score counts)."""
        if not self.pattern:
            return min(4, lowered.count(self.text))
        return sum(1 for _ in itertools.islice(_compiled(self.pattern).finditer(text), 4))


@functools.lru_cache(maxsize=256)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


def _words(term: str) -> list[_Word]:
    """How *term* is looked for. Plain English letters have one spelling; a
    letter of another script, and a digit, may be written in several; a letter
    only the fold reads is also looked for as it stands."""
    from kazma_core.documents.arabic import spelling_pattern

    as_it_stands = _Word(term.lower())
    if len(term) > LONGEST_SPELLED or (term.isascii() and not any(c.isdigit() for c in term)):
        return [as_it_stands]
    spelled = _Word(term.lower(), spelling_pattern(term))
    return [spelled, as_it_stands] if _has_folded_otherwise(term) else [spelled]


def _asked(terms: Iterable[str | Iterable[str]]) -> list[list[_Word]]:
    """Each term's words: a term is one word, or several spellings of one."""
    asked = []
    for term in terms:
        variants = [term] if isinstance(term, str) else term
        words = {(w.pattern or w.text): w for v in variants if v for w in _words(v)}
        if words:
            asked.append(list(words.values()))
    return asked


def _points(title: str, messages: str, asked: list[list[_Word]]) -> list[int]:
    """The SQL below for a spooled copy: what each term scores."""
    lt, lm = title.lower(), messages.lower()
    return [sum((5 if w.count(title, lt) else 0) + w.count(messages, lm) for w in words) for words in asked]


def _has_folded_otherwise(*texts: str) -> bool:
    """Whether a text holds a letter only the fold reads
    (``documents.arabic.folded_otherwise``)."""
    from kazma_core.documents.arabic import folded_otherwise

    return any(not t.isascii() and _compiled(folded_otherwise()).search(t) for t in texts)


def search_sessions(
    terms: Iterable[str | Iterable[str]],
    *,
    tenant_id: str | None = None,
    exclude_session_id: str | None = None,
    limit: int = 3,
    sqlite_path: Path | None = None,
    at_least: int = 1,
    confirm: Callable[[dict[str, Any]], Any] | None = None,
) -> list[Any]:
    """The tenant's sessions matching *terms*, best first, over EVERY session.

    A term is a word, or several spellings of one; a session matches when it
    holds *at_least* of them. Score per spelling: 5 when the title has it,
    plus its occurrences in the messages (at most 4) -- computed where the
    chats are, so the whole history is ranked and no text is read to rank it.
    A spooled copy (a save the database refused) is scored the same way and
    replaces the stored row when it scores higher. The best first, then the
    newest.

    A spelling does not read every letter (``arabic.folded_otherwise``: a
    ligature, "ß", a full-width digit), so a session whose text has one is
    ranked when it holds any term, after those that hold enough: *confirm*
    reads it with the fold.

    *confirm* is asked about each session in rank order and answers what to
    return for it, or None to pass it over: the search goes on until *limit*
    are confirmed or none are left. Rows cut off before they were checked hid
    the chat that held the answer behind 15 that only shared its letters (25
    Nov 2026). It reads :data:`_TEXTS_READ` of them at most. Rows:
    ``session_id, thread_id, title, created_at, messages`` (text) and
    ``score``. *sqlite_path* searches that SQLite chat file whatever the
    backend. A store that cannot be searched finds nothing, and is said.
    """
    tenant_id = space_named_or_bound(tenant_id)
    asked = _asked(terms)
    if not asked:
        return []
    try:
        return _confirmed(
            _ranked(asked, tenant_id, exclude_session_id, sqlite_path, at_least),
            tenant_id, sqlite_path, confirm or (lambda row: row), max(1, int(limit)),
        )
    except STORE_ERRORS as exc:  # the fallback is best-effort; an unreadable store finds nothing
        if not _never_created(exc):
            described_error("[chat_history] the past chats could not be searched", exc, level=logging.WARNING)
        return []


def _ranked(
    asked: list[list[_Word]], tenant_id: str, exclude: str | None, sqlite_path: Path | None, at_least: int
) -> list[dict[str, Any]]:
    """Every matching session, best first; only a spooled one has its text."""
    sessions = _sessions_path(sqlite_path)
    if sqlite_path is None and _postgres():
        rows = _search_postgres(asked, tenant_id, exclude)
    else:
        rows = _search_sqlite(sessions, asked, tenant_id, exclude)
    best = {str(r["session_id"]): r for r in rows}
    for s in _spooled(sessions):
        sid = str(s["session_id"])
        if s.get("tenant_id") != tenant_id or sid == exclude:
            continue
        text = json.dumps(s.get("messages") or [], ensure_ascii=False)
        title = str(s.get("title") or "")
        points = _points(title, text, asked)
        if any(points) and sum(points) > int((best.get(sid) or {}).get("score") or 0):
            best[sid] = {
                "session_id": sid,
                "thread_id": str(s.get("thread_id") or ""),
                "title": title,
                "created_at": str(s.get("created_at") or ""),
                "messages": text,
                "score": sum(points),
                "there": sum(map(bool, points)),
                "otherwise": _has_folded_otherwise(title, text),
            }
    return sorted(
        (r for r in best.values() if r["there"] >= at_least or r["otherwise"]),
        key=lambda r: (r["there"] >= at_least, int(r["score"]), str(r.get("created_at") or "")),
        reverse=True,
    )


def _confirmed(
    ranked: list[dict[str, Any]],
    tenant_id: str,
    sqlite_path: Path | None,
    confirm: Callable[[dict[str, Any]], Any],
    limit: int,
) -> list[Any]:
    """What *confirm* answers for the sessions in rank order, *limit* at most."""
    found: list[Any] = []
    for start in range(0, min(len(ranked), _TEXTS_READ), _TEXTS_AT_ONCE):
        part = ranked[start: min(start + _TEXTS_AT_ONCE, _TEXTS_READ)]
        texts = _texts_of([str(r["session_id"]) for r in part if "messages" not in r], tenant_id, sqlite_path)
        for row in part:
            text = row.get("messages", texts.get(str(row["session_id"])))
            if text is None:
                continue  # deleted since it was ranked
            answer = confirm({**row, "messages": text})
            if answer is not None:
                found.append(answer)
                if len(found) >= limit:
                    return found
    if len(ranked) > _TEXTS_READ:
        logger.info("[chat_history] a search read the %d best of %d past chats that may hold its words",
                    _TEXTS_READ, len(ranked))
    return found


def _texts_of(session_ids: list[str], tenant_id: str, sqlite_path: Path | None) -> dict[str, str]:
    """The stored messages (text) of those sessions of *tenant_id*."""
    if not session_ids:
        return {}
    if sqlite_path is None and _postgres():
        from kazma_core.db.pg_helpers import get_pool

        rows = get_pool().execute(
            "SELECT session_id, messages::text AS messages FROM kazma_chat_sessions "
            "WHERE tenant_id = %s AND session_id = ANY(%s)",
            [tenant_id, session_ids],
        )
        return {str(r["session_id"]): str(r["messages"] or "") for r in rows}
    conn = _ro(_sessions_path(sqlite_path))
    if conn is None:
        return {}
    try:
        marks = ",".join("?" for _ in session_ids)
        return {
            str(r["session_id"]): str(r["messages"] or "")
            for r in conn.execute(
                f"SELECT session_id, messages FROM sessions WHERE tenant_id = ? AND session_id IN ({marks})",
                [tenant_id, *session_ids],
            )
        }
    finally:
        conn.close()


def _sql_times(text: str | None, pattern: str) -> int:
    """SQLite's count of a spelling pattern (at most 4)."""
    return _Word("", pattern).count(text or "", "")


def _ranking(
    asked: list[list[_Word]], *, plain: tuple[str, str, str], spelled: tuple[str, str, str], text: str
) -> tuple[str, str, list[Any], list[Any]]:
    """(each term's points as columns p0, p1, ..., the condition that any term
    is there, their parameters), in one dialect.

    *plain* and *spelled*: what a substring and a pattern score in the title
    and in the messages, and how one is tested on a column."""
    points: list[str] = []
    point_params: list[Any] = []
    there: list[str] = []
    there_params: list[Any] = []
    for at, words in enumerate(asked):
        terms: list[str] = []
        for w in words:
            title, messages, test = spelled if w.pattern else plain
            terms.append(f"({title} + {messages})")
            point_params += [w.pattern] * 2 if w.pattern else [w.text, w.text, len(w.text)]
            there += [test.format("title"), test.format(text)]
            there_params += [w.pattern or w.text] * 2
        points.append(f"({' + '.join(terms)}) AS p{at}")
    return (", ".join(points), f"({' OR '.join(there)})", point_params, there_params)


def _ranked_row(row: Any, terms: int) -> dict[str, Any]:
    """A row of the search below, with its score and how many terms it holds."""
    points = [int(row[f"p{at}"] or 0) for at in range(terms)]
    return {
        **{k: row[k] for k in ("session_id", "thread_id", "title", "created_at")},
        "score": sum(points), "there": sum(map(bool, points)), "otherwise": bool(row["otherwise"]),
    }


def _search_sqlite(
    path: Path, asked: list[list[_Word]], tenant_id: str, exclude: str | None
) -> list[dict[str, Any]]:
    conn = _ro(Path(path))
    if conn is None:
        return []
    points, there, point_params, there_params = _ranking(
        asked,
        plain=("(CASE WHEN instr(lower(title), ?) > 0 THEN 5 ELSE 0 END)",
               "min(4, (length(lower(messages)) - length(replace(lower(messages), ?, ''))) / ?)",
               "instr(lower({}), ?) > 0"),
        spelled=("(CASE WHEN kazma_times(title, ?) > 0 THEN 5 ELSE 0 END)", "kazma_times(messages, ?)",
                 "kazma_times({}, ?) > 0"),
        text="messages",
    )
    where = f"tenant_id = ? AND {there}"
    params: list[Any] = [*point_params, tenant_id, *there_params]
    if exclude:
        where += " AND session_id != ?"
        params.append(exclude)
    try:
        conn.create_function("kazma_times", 2, _sql_times, deterministic=True)
        conn.create_function("kazma_otherwise", 2, lambda title, text: _has_folded_otherwise(title or "", text or ""),
                             deterministic=True)
        rows = conn.execute(
            "SELECT session_id, thread_id, title, created_at, kazma_otherwise(title, messages) AS otherwise, "
            f"{points} FROM sessions WHERE {where}",
            params,
        ).fetchall()
    finally:
        conn.close()
    return [_ranked_row(r, len(asked)) for r in rows]


def _search_postgres(
    asked: list[list[_Word]], tenant_id: str, exclude: str | None
) -> list[dict[str, Any]]:
    from kazma_core.db.pg_helpers import get_pool
    from kazma_core.documents.arabic import folded_otherwise

    points, there, point_params, there_params = _ranking(
        asked,
        plain=("(CASE WHEN position(%s in lower(title)) > 0 THEN 5 ELSE 0 END)",
               "LEAST(4, (length(lower(messages::text))"
               " - length(replace(lower(messages::text), %s, ''))) / %s)",
               "position(%s in lower({})) > 0"),
        spelled=("(CASE WHEN title ~ %s THEN 5 ELSE 0 END)",
                 "(SELECT LEAST(4, count(*)) FROM regexp_matches(messages::text, %s, 'g'))",
                 "{} ~ %s"),
        text="messages::text",
    )
    where = f"tenant_id = %s AND {there}"
    otherwise = folded_otherwise()
    params: list[Any] = [otherwise, otherwise, *point_params, tenant_id, *there_params]
    if exclude:
        where += " AND session_id != %s"
        params.append(exclude)
    rows = get_pool().execute(
        "SELECT session_id, thread_id, title, created_at, (title ~ %s OR messages::text ~ %s) AS otherwise, "
        f"{points} FROM kazma_chat_sessions WHERE {where}",
        params,
    )
    return [_ranked_row(r, len(asked)) for r in rows]
