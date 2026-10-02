"""Forgetting: a memory the user takes back, and chats kept out of memory.

Plan U1 (``docs/plans/MEMORY_NOTHING_LOST_PLAN.md`` §5.6). "Nothing lost"
guards memories against accidents. A user who asks Kazma to forget something
has decided otherwise, and that has to hold against every path that rebuilds
memory from the chat store: turn reconcile writes an episode for each stored
turn without one, the recovery pass refills an episode whose text is gone,
and the past-chats search reads the chat store itself.

So forgetting never deletes the row:

- the episode becomes a tombstone: question, answer and summary emptied
  (``''`` -- NULL text is what recovery treats as erased), vector dropped,
  tier :data:`FORGOTTEN_TIER` (not recallable, never archived or promoted),
  ``metadata.forgotten`` stamped;
- the turn goes into the ledger (``memory_forgotten``) under every key of its
  chat -- a chat has a session id and a thread id and writers use either --
  and ``dual_write.mirror_episode``, the one episode writer, refuses a
  ledgered turn before it writes anything;
- the facts that turn produced stop being current and lose their value;
- the Postgres mirror gets the tombstone and the remote vector index loses
  the vector.

"Don't remember this chat" is a ledger row with turn 0 and no question: the
writer refuses every turn of the chat, the post-turn worker extracts no facts
from it, and the past-chats search leaves the chat out.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "FORGOTTEN_TIER",
    "chat_keys",
    "chat_remembered",
    "forget_chat",
    "forget_episode",
    "forgotten_in_chat",
    "forgotten_turns",
    "question_sha",
    "refuses_write",
    "retire_copy",
    "set_chat_remembered",
]

#: The tier of a forgotten episode. Not in ``vector_engine.RECALLABLE_TIERS``,
#: and no tier rule (``macro_sleep``) moves it.
FORGOTTEN_TIER = "forgotten"

#: How much of a question an episode keeps (``dual_write`` / ``turn_reconcile``):
#: the ledger hashes the stored form, so both sides hash the same text.
_TEXT_CAP = 4000


def question_sha(text: str | None) -> str:
    """The ledger's key for a turn's question: its stored form, hashed."""
    stored = (text or "")[:_TEXT_CAP].strip()
    return hashlib.sha256(stored.encode("utf-8")).hexdigest()[:32]


def chat_keys(key: str) -> list[str]:
    """Every id the chat *key* is stored under: its session id and its thread id.

    Writers use either (the live path the session's, turn reconcile the
    thread's), so the ledger names both. The key alone when the chat store has
    no copy -- a deleted chat, a chat before its first message, or one the
    store cannot read. Reads the ids only, never the messages: the episode
    writer asks on every write.
    """
    from kazma_core.memory.chat_history import ids_of

    return ids_of(key)


_schema_ready: set[str] = set()
_schema_lock = threading.Lock()


def _open() -> sqlite3.Connection:
    """The memory database, schema ensured once per file per process (the
    post-turn worker asks :func:`chat_remembered` on every turn)."""
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import primary_memory_db

    path = str(primary_memory_db())
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    with _schema_lock:
        if path not in _schema_ready:
            from kazma_core.memory.schema_v2 import ensure_primary_schema

            ensure_primary_schema(conn)
            _schema_ready.add(path)
            return conn
    apply_sqlite_pragmas(conn)
    return conn


def _ledger_rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any]) -> list[Any]:
    """Read the ledger; a database from before it existed has no entries."""
    try:
        return conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise


def refuses_write(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    session_id: str,
    turn_number: int,
    user_text: str | None,
    keys: Iterable[str] | None = None,
) -> bool:
    """True when the ledger keeps this turn out of memory: the user forgot it,
    or keeps its whole chat out -- under ANY id the chat is stored under.

    A web chat has two ids, and before its first message the store knows only
    the session id, so a chat kept out of memory then has its ledger row under
    that id alone; turn reconcile writes under the thread id. Asking for the
    one key a writer holds let a chat kept out from the start reach memory,
    facts and all (live 2026-10-02). *keys* when the caller has them
    (``chat_keys``, a store read it should make outside its own locks);
    resolved here otherwise.
    """
    if not session_id:
        return False
    names = list(dict.fromkeys([session_id, *(chat_keys(session_id) if keys is None else keys)]))
    marks = ",".join("?" for _ in names)
    return bool(
        _ledger_rows(
            conn,
            f"SELECT 1 FROM memory_forgotten WHERE tenant_id = ? AND session_key IN ({marks}) "
            "AND ((turn_number = 0 AND question_sha = '') "
            "OR (turn_number = ? AND question_sha = ?)) LIMIT 1",
            (tenant_id or "default", *names, int(turn_number or 0), question_sha(user_text)),
        )
    )


def forgotten_turns(
    conn: sqlite3.Connection, *, tenant_id: str, keys: Iterable[str]
) -> tuple[bool, set[tuple[int, str]]]:
    """``(whole chat kept out, {(turn, question hash), ...} forgotten)`` for the
    chat named by *keys*."""
    keys = [k for k in keys if k]
    if not keys:
        return False, set()
    marks = ",".join("?" for _ in keys)
    rows = _ledger_rows(
        conn,
        f"SELECT turn_number, question_sha FROM memory_forgotten "
        f"WHERE tenant_id = ? AND session_key IN ({marks})",
        (tenant_id or "default", *keys),
    )
    whole = any(int(r[0]) == 0 and not r[1] for r in rows)
    return whole, {(int(r[0]), str(r[1])) for r in rows if r[1]}


def forgotten_in_chat(tenant_id: str, keys: Iterable[str]) -> tuple[bool, set[tuple[int, str]]]:
    """:func:`forgotten_turns` on a connection of its own (readers outside
    the memory writers: the past-chats search)."""
    conn = _open()
    try:
        return forgotten_turns(conn, tenant_id=tenant_id, keys=keys)
    finally:
        conn.close()


def chat_remembered(key: str, *, tenant_id: str, conn: sqlite3.Connection | None = None) -> bool:
    """False when the user keeps the chat *key* out of memory."""
    own = conn is None
    conn = conn or _open()
    try:
        whole, _turns = forgotten_turns(conn, tenant_id=tenant_id, keys=chat_keys(key))
        return not whole
    finally:
        if own:
            conn.close()


def set_chat_remembered(
    key: str, remembered: bool, *, tenant_id: str, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """Keep the chat *key* out of memory from now on, or let it back in.

    Letting it back in affects new turns only: turns forgotten one by one stay
    forgotten, and nothing is written for the turns in between (turn reconcile
    writes them, like any turn memory missed).
    """
    keys = chat_keys(key)
    if not keys:
        return {"ok": False, "error": "chat required"}
    own = conn is None
    conn = conn or _open()
    try:
        tid = tenant_id or "default"
        if remembered:
            marks = ",".join("?" for _ in keys)
            conn.execute(
                f"DELETE FROM memory_forgotten WHERE tenant_id = ? AND turn_number = 0 "
                f"AND question_sha = '' AND session_key IN ({marks})",
                (tid, *keys),
            )
        else:
            now = time.time()
            conn.executemany(
                "INSERT OR IGNORE INTO memory_forgotten "
                "(tenant_id, session_key, turn_number, question_sha, episode_id, forgotten_at) "
                "VALUES (?, ?, 0, '', NULL, ?)",
                [(tid, k, now) for k in keys],
            )
        conn.commit()
        return {"ok": True, "remembered": bool(remembered), "chat_keys": keys}
    finally:
        if own:
            conn.close()


def forget_episode(
    episode_id: str,
    *,
    tenant_id: str,
    conn: sqlite3.Connection | None = None,
    by: str = "user",
) -> dict[str, Any]:
    """Forget one memory: tombstone, ledger under every key of its chat, the
    facts its turn produced, the mirrors. Idempotent.

    *tenant_id* is the caller's: another tenant's memory reads as not found;
    the install's own tenant (``"default"``) may forget any (the rule of
    ``routes_direct._shared._tenant_clause``). The ledger is written under
    the MEMORY's tenant -- the one its future writes would carry.
    """
    eid = (episode_id or "").strip()
    if not eid:
        return {"ok": False, "error": "episode_id required"}
    own = conn is None
    conn = conn or _open()
    try:
        row = conn.execute(
            "SELECT id, tenant_id, session_id, turn_number, user_text, tier, metadata_json "
            "FROM episodes WHERE id = ?",
            (eid,),
        ).fetchone()
        caller = tenant_id or "default"
        if row is None or (caller != "default" and str(row["tenant_id"] or "default") != caller):
            return {"ok": False, "error": "not_found"}

        owner = str(row["tenant_id"] or "default")
        turn = int(row["turn_number"] or 0)
        keys = chat_keys(str(row["session_id"] or ""))
        now = time.time()
        if row["tier"] == FORGOTTEN_TIER:
            # Done before -- but ``invalidate_belief`` commits as it goes, so
            # a forget cut short may have left facts to finish.
            facts = _forget_turn_facts(conn, tenant_id=owner, keys=keys, turn=turn, now=now)
            conn.commit()
            _remirror_facts(conn, facts)
            return {"ok": True, "episode_id": eid, "already": True, "facts_forgotten": len(facts)}

        sha = question_sha(row["user_text"])
        # Every copy of the turn goes: the live write and turn reconcile use
        # different keys of the chat, and a legacy restore may hold another.
        marks = ",".join("?" for _ in keys)
        copies = [
            str(r["id"])
            for r in conn.execute(
                f"SELECT id, user_text FROM episodes WHERE tenant_id = ? AND turn_number = ? "
                f"AND tier != ? AND session_id IN ({marks})",
                (owner, turn, FORGOTTEN_TIER, *keys),
            ).fetchall()
            if question_sha(r["user_text"]) == sha
        ]
        # And what the agent noted with its memory tools during that turn.
        notes = _chat_notes(conn, tenant_id=owner, keys=keys, turn=turn)
        ids = list(dict.fromkeys([eid, *copies, *notes]))

        conn.executemany(
            "INSERT OR IGNORE INTO memory_forgotten "
            "(tenant_id, session_key, turn_number, question_sha, episode_id, forgotten_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(owner, k, turn, sha, eid, now) for k in keys],
        )
        for one in ids:
            _tombstone(conn, one, now=now, by=by)
        facts = _forget_turn_facts(conn, tenant_id=owner, keys=keys, turn=turn, now=now)
        # A weekly summary written from the turn keeps nothing of it: emptied
        # now, written again without it (plan C2).
        from kazma_core.memory.topic_summaries import on_turns_forgotten

        summaries = on_turns_forgotten(conn, ids)
        conn.commit()

        from kazma_core.memory.state_backend import remirror_episode_by_id

        for one in ids:
            remirror_episode_by_id(conn, one)
        _remirror_facts(conn, facts)
        remote = [_delete_remote_vector(conn, one, owner) for one in ids]
        return {
            "ok": True,
            "episode_id": eid,
            "copies": len(ids),
            "facts_forgotten": len(facts),
            "summaries_emptied": len(summaries),
            "chat_keys": keys,
            "remote_vector_deleted": all(remote),
        }
    finally:
        if own:
            conn.close()


def retire_copy(
    copy_id: str, *, original_id: str, conn: sqlite3.Connection | None = None
) -> dict[str, Any]:
    """Retire a memory that another one holds in full: a V1 migration copy of
    a turn memory holds (``legacy_tables.legacy_copies``, 181 on the live
    install on 2026-09-27; recall could show one turn twice, the copy's
    answer cut at 300 characters).

    The copy is emptied like a forgotten memory -- text and vector gone, tier
    ``forgotten`` so no recall, repair or recovery pass reads it, the id kept
    so the legacy restore never puts it back -- but it is not a forget: the
    turn stays remembered through the original, so no ledger entry, and the
    facts stay. Refused unless the original is present and remembered.
    Idempotent.
    """
    own = conn is None
    conn = conn or _open()
    try:
        copy = conn.execute(
            "SELECT id, tenant_id, tier FROM episodes WHERE id = ?", (copy_id,)
        ).fetchone()
        original = conn.execute(
            "SELECT tier, user_text FROM episodes WHERE id = ?", (original_id,)
        ).fetchone()
        if copy is None:
            return {"ok": False, "error": "not_found"}
        if copy["tier"] == FORGOTTEN_TIER:
            return {"ok": True, "episode_id": copy_id, "already": True}
        if original is None or original["tier"] == FORGOTTEN_TIER or not (original["user_text"] or "").strip():
            return {"ok": False, "error": "original_not_held"}
        now = time.time()
        _tombstone(conn, copy_id, now=now, by="duplicate", note={"duplicate_of": original_id})
        from kazma_core.memory.topic_summaries import on_turns_forgotten

        summaries = on_turns_forgotten(conn, [copy_id])
        conn.commit()
        from kazma_core.memory.state_backend import remirror_episode_by_id

        remirror_episode_by_id(conn, copy_id)
        remote = _delete_remote_vector(conn, copy_id, str(copy["tenant_id"] or "default"))
        return {"ok": True, "episode_id": copy_id, "duplicate_of": original_id,
                "summaries_emptied": len(summaries), "remote_vector_deleted": remote}
    finally:
        if own:
            conn.close()


def _tombstone(
    conn: sqlite3.Connection, eid: str, *, now: float, by: str, note: dict[str, Any] | None = None
) -> None:
    """Empty one episode's texts and vector; keep its id, chat, turn and time."""
    row = conn.execute("SELECT metadata_json FROM episodes WHERE id = ?", (eid,)).fetchone()
    try:
        meta = json.loads((row[0] if row else None) or "{}")
    except (TypeError, ValueError):
        meta = {}
    # Keep only what says where the memory came from; nothing it said.
    stamp = {
        "source": meta.get("source") if isinstance(meta, dict) else None,
        "forgotten": {"at": now, "by": by, **(note or {})},
    }
    conn.execute(
        "UPDATE episodes SET user_text = '', assistant_text = '', summary_text = '', "
        "embedding = NULL, tier = ?, metadata_json = ? WHERE id = ?",
        (FORGOTTEN_TIER, json.dumps(stamp), eid),
    )


def _forget_turn_facts(
    conn: sqlite3.Connection, *, tenant_id: str, keys: list[str], turn: int, now: float
) -> list[str]:
    """The facts a forgotten turn produced (``source_session`` / ``source_turn``):
    no longer current, and without their value. Skips those done already.
    Returns the ids it changed."""
    if not keys or turn <= 0:
        return []
    marks = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT id, tenant_id, invalidated_at, valid_until, metadata_json FROM beliefs "
        f"WHERE tenant_id = ? AND source_turn = ? AND source_session IN ({marks})",
        (tenant_id, turn, *keys),
    ).fetchall()
    return _forget_fact_rows(conn, rows, now=now)


def _forget_chat_facts(
    conn: sqlite3.Connection, *, tenant_id: str | None, keys: list[str], now: float
) -> list[str]:
    """Every fact naming the chat *keys* as its source, whatever its turn --
    one a memory tool stored in a turn that left no memory of its own.
    *tenant_id* None: any tenant."""
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    sql = (
        f"SELECT id, tenant_id, invalidated_at, valid_until, metadata_json FROM beliefs "
        f"WHERE source_session IN ({marks})"
    )
    params: list[Any] = [*keys]
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    return _forget_fact_rows(conn, conn.execute(sql, params).fetchall(), now=now)


def _forget_fact_rows(conn: sqlite3.Connection, rows: list[Any], *, now: float) -> list[str]:
    """No longer current, and without their value. Skips those done already."""
    from kazma_core.memory.hygiene import invalidate_belief

    changed: list[str] = []
    for r in rows:
        tenant_id = str(r["tenant_id"] or "default")
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if "forgotten" in meta:
            continue
        if r["invalidated_at"] is None and r["valid_until"] is None:
            invalidate_belief(r["id"], conn=conn, now=now, tenant_id=tenant_id)
        meta["forgotten"] = {"at": now}
        conn.execute(
            "UPDATE beliefs SET object = '', metadata_json = ? WHERE id = ?",
            (json.dumps(meta), r["id"]),
        )
        changed.append(str(r["id"]))
    return changed


def _remirror_facts(conn: sqlite3.Connection, fact_ids: list[str]) -> None:
    from kazma_core.memory.state_backend import remirror_belief_by_id

    for bid in fact_ids:
        remirror_belief_by_id(conn, bid)


def _delete_remote_vector(conn: sqlite3.Connection, eid: str, tenant_id: str) -> bool:
    """The vector a remote index holds for the memory. The local copy went with
    the tombstone (``embedding = NULL``)."""
    from kazma_core.memory.backends import get_vector_backend

    try:
        backend = get_vector_backend(conn)
    except RuntimeError:
        # failover=raise with the remote down: the memory is forgotten here,
        # the remote copy is not. Said, so it can be retried.
        logger.warning("[forget] remote vector index unreachable; %s kept there", eid)
        return False
    return bool(backend.delete(eid, tenant_id=tenant_id))


def forget_chat(key: str, *, tenant_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Forget every memory the chat *key* left, under either of its keys."""
    keys = chat_keys(key)
    if not keys:
        return {"ok": False, "error": "chat required"}
    own = conn is None
    conn = conn or _open()
    try:
        caller = tenant_id or "default"
        marks = ",".join("?" for _ in keys)
        scope = "" if caller == "default" else " AND tenant_id = ?"
        ids = [
            str(r[0])
            for r in conn.execute(
                f"SELECT id FROM episodes WHERE session_id IN ({marks}) AND tier != ?{scope}",
                (*keys, FORGOTTEN_TIER, *(() if caller == "default" else (caller,))),
            ).fetchall()
        ]
        facts = 0
        for eid in ids:
            facts += int(forget_episode(eid, tenant_id=caller, conn=conn).get("facts_forgotten") or 0)
        # What the agent noted during the chat in turns that left no memory of
        # their own, and every fact naming the chat whatever its turn.
        owner = None if caller == "default" else caller
        notes = _chat_notes(conn, tenant_id=owner, keys=keys)
        note_tenants = {
            str(r[0]): str(r[1] or "default")
            for r in conn.execute(
                f"SELECT id, tenant_id FROM episodes WHERE id IN ({','.join('?' for _ in notes)})", notes
            )
        } if notes else {}
        now = time.time()
        for note in notes:
            _tombstone(conn, note, now=now, by="user")
        chat_facts = _forget_chat_facts(conn, tenant_id=owner, keys=keys, now=now)
        if notes:
            from kazma_core.memory.topic_summaries import on_turns_forgotten

            on_turns_forgotten(conn, notes)
        conn.commit()
        if notes:
            from kazma_core.memory.state_backend import remirror_episodes

            remirror_episodes(conn, notes)
            for note in notes:
                _delete_remote_vector(conn, note, note_tenants.get(note, caller))
        _remirror_facts(conn, chat_facts)
        return {"ok": True, "forgotten": len(ids) + len(notes),
                "facts_forgotten": facts + len(chat_facts), "chat_keys": keys}
    finally:
        if own:
            conn.close()


#: The session the agent's saved notes live in (the memory_store tool).
_NOTES_SESSION = "memory_store"


def _chat_notes(
    conn: sqlite3.Connection, *, tenant_id: str | None, keys: list[str], turn: int | None = None
) -> list[str]:
    """The agent's notes the memory tools wrote during the chat *keys* (in its
    *turn*, when given): each names the chat (``metadata.chat`` /
    ``chat_turn``, 2026-09-27). *tenant_id* None: any tenant."""
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    sql = (
        "SELECT id FROM episodes WHERE session_id = ? AND tier != ? AND json_valid(metadata_json) "
        f"AND json_extract(metadata_json, '$.chat') IN ({marks})"
    )
    params: list[Any] = [_NOTES_SESSION, FORGOTTEN_TIER, *keys]
    if turn is not None:
        sql += " AND json_extract(metadata_json, '$.chat_turn') = ?"
        params.append(int(turn))
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    return [str(r[0]) for r in conn.execute(sql, params).fetchall()]
