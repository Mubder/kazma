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

from kazma_core.db.fts5_purge import purge_deleted_rows
from kazma_core.memory.memory_stores import MEMORY_TABLES

logger = logging.getLogger(__name__)

__all__ = [
    "FORGOTTEN_TIER",
    "chat_keys",
    "chat_remembered",
    "forget_belief_source",
    "forget_chat",
    "forget_episode",
    "forget_mentions",
    "keep_turn_out",
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
            f"SELECT 1 FROM memory_forgotten WHERE tenant_id IN (?, 'default') AND session_key IN ({marks}) "
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
        f"WHERE tenant_id IN (?, 'default') AND session_key IN ({marks})",
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
    key: str, remembered: bool, *, tenant_id: str, conn: sqlite3.Connection | None = None,
    journal: bool = True,
) -> dict[str, Any]:
    """Keep the chat *key* out of memory from now on, or let it back in.

    Letting it back in affects new turns only: turns forgotten one by one stay
    forgotten, and nothing is written for the turns in between (turn reconcile
    writes them, like any turn memory missed). Journaled (``forget_journal``):
    a restored memory database gets the same answer.
    """
    keys = chat_keys(key)
    if not keys:
        return {"ok": False, "error": "chat required"}
    tid = tenant_id or "default"
    from kazma_core.memory import forget_journal

    seq = (forget_journal.append("remember" if remembered else "keep_out", tenant_id=tid, session_key=key)
           if journal else None)
    own = conn is None
    conn = conn or _open()
    try:
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
        forget_journal.mark_applied(conn, seq)
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
    purge_index: bool = True,
    journal: bool = True,
) -> dict[str, Any]:
    """Forget one memory: tombstone, ledger under every key of its chat, the
    facts its turn produced, every copy the memory store registry names
    (``memory_stores``), the mirrors. Idempotent.

    Journaled (``forget_journal``, Memory audits M08/M10): the entry is
    written before anything changes, every step below is re-runnable, and
    asking again -- or the journal's replay, or the maintenance pass -- runs
    them ALL (until 2026-10-13 asking again finished the facts only, and a
    forget cut short kept its summaries active). *journal* False is for a
    caller that journaled already (:func:`forget_chat`, the replay).

    *purge_index* False leaves the full-text indexes to a caller that forgets
    several memories and purges once (:func:`forget_chat`).

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
        session = str(row["session_id"] or "")
        keys = chat_keys(session)
        already = row["tier"] == FORGOTTEN_TIER
        sha = _ledger_sha(conn, eid) if already else question_sha(row["user_text"])
        seq = None
        if journal:
            from kazma_core.memory import forget_journal

            seq = forget_journal.append("turn", tenant_id=owner, session_key=session, turn_number=turn,
                                        question_sha=sha, episode_id=eid)
        out = _forget_turn(conn, eid=eid, owner=owner, keys=keys, turn=turn, sha=sha, by=by,
                           purge_index=purge_index, seq=seq)
        if already:
            out["already"] = True
        if by == "user" and journal:
            # The user's own Forget reaches the chat's Time Travel steps too:
            # each holds the whole conversation up to it, so every step from
            # the forgotten turn on goes (TT-05). A rewind leaves this to the
            # restore, which drops the steps after the restored one.
            out["snapshots_deleted"] = _forget_snapshots(keys or [session], turn=turn)
        return out
    finally:
        if own:
            conn.close()


def _forget_snapshots(keys: list[str], *, turn: int | None) -> int:
    """Delete the chat's Time Travel steps that hold what was forgotten: from
    *turn* on, or all of them (*turn* None). Never raises -- the memory forget
    is done; a step left behind is a WARNING (snapshots.db is not backed up,
    so nothing restores one later)."""
    from kazma_core.time_travel import SnapshotStoreError, forget_thread_snapshots, forget_turn_snapshots

    try:
        if turn is None:
            return forget_thread_snapshots(list(keys))
        return forget_turn_snapshots(list(keys), int(turn)) if int(turn) > 0 else 0
    except (SnapshotStoreError, sqlite3.Error, OSError):
        logger.warning("[forget] the Time Travel steps of %s were NOT deleted", ",".join(keys), exc_info=True)
        return -1


def _ledger_sha(conn: sqlite3.Connection, eid: str) -> str:
    """The question hash a forgotten memory's ledger rows carry ("" when none:
    a retired duplicate, or a forget from before the hash)."""
    row = conn.execute(
        "SELECT question_sha FROM memory_forgotten WHERE episode_id = ? AND turn_number > 0 LIMIT 1", (eid,)
    ).fetchone()
    return str(row[0]) if row else ""


def _forget_turn(
    conn: sqlite3.Connection, *, eid: str, owner: str, keys: list[str], turn: int, sha: str, by: str,
    purge_index: bool, seq: int | None,
) -> dict[str, Any]:
    """Every step of forgetting one turn; each re-runnable."""
    from kazma_core.memory import forget_journal
    from kazma_core.memory.topic_summaries import on_turns_forgotten

    now = time.time()
    marks = ",".join("?" for _ in keys) if keys else "''"
    # Every copy of the turn still held: the live write and turn reconcile use
    # different keys of the chat, and a legacy restore may hold another.
    alive = conn.execute("SELECT 1 FROM episodes WHERE id = ? AND tier != ?", (eid, FORGOTTEN_TIER)).fetchone()
    copies = [
        str(r["id"])
        for r in conn.execute(
            f"SELECT id, user_text FROM episodes WHERE tenant_id = ? AND turn_number = ? "
            f"AND tier != ? AND session_id IN ({marks})",
            (owner, turn, FORGOTTEN_TIER, *keys),
        ).fetchall()
        if sha and question_sha(r["user_text"]) == sha
    ] if keys else []
    # And what the agent noted with its memory tools during that turn.
    notes = _chat_notes(conn, tenant_id=owner, keys=keys, turn=turn)
    ids = list(dict.fromkeys(([eid] if alive else []) + copies + notes))

    if keys and turn > 0 and sha:
        conn.executemany(
            "INSERT OR IGNORE INTO memory_forgotten "
            "(tenant_id, session_key, turn_number, question_sha, episode_id, forgotten_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(owner, k, turn, sha, eid, now) for k in keys],
        )
    texts = _memory_texts(conn, ids)
    for one in ids:
        _tombstone(conn, one, now=now, by=by)
    _scrub_legacy_copies(conn, list(dict.fromkeys([eid, *ids])), texts)
    facts = _forget_turn_facts(conn, tenant_id=owner, keys=keys, turn=turn, now=now)
    _forget_archived_facts(conn, tenant_id=owner, keys=keys, turn=turn, now=now)
    # Every memory of the turn now forgotten -- also copies an earlier forget
    # tombstoned, if it was cut short before their summaries were emptied.
    gone = list(dict.fromkeys([eid, *ids, *_forgotten_of_turn(conn, owner=owner, keys=keys, turn=turn)]))
    # A weekly summary written from the turn keeps nothing of it: emptied
    # now, written again without it (plan C2). One already waiting to be
    # written again is not touched: its rewrite reads the turns as they are
    # then (topic_summaries._store, M05).
    summaries = on_turns_forgotten(conn, gone, only_active=True)
    if purge_index:
        purge_search_index(conn)
    conn.commit()

    from kazma_core.memory.state_backend import remirror_episode_by_id

    for one in gone:
        remirror_episode_by_id(conn, one)
    remote = [_delete_remote_vector(conn, one, owner) for one in gone]
    remote += _after_facts(conn, owner=owner, changed=facts, keys=keys, turn=turn)
    # Applied only once EVERY step ran: a step that raises after the commit
    # above leaves the entry pending, and the next pass runs them all again.
    forget_journal.mark_applied(conn, seq)
    conn.commit()
    return {
        "ok": True,
        "episode_id": eid,
        "copies": len(ids),
        "facts_forgotten": len(facts),
        "summaries_emptied": len(summaries),
        "chat_keys": keys,
        "remote_vector_deleted": all(remote),
    }


def _forgotten_of_turn(conn: sqlite3.Connection, *, owner: str, keys: list[str], turn: int) -> list[str]:
    """The forgotten memories of a chat's turn, and of its notes."""
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    ids = [str(r[0]) for r in conn.execute(
        f"SELECT id FROM episodes WHERE tenant_id = ? AND turn_number = ? AND tier = ? "
        f"AND session_id IN ({marks})",
        (owner, turn, FORGOTTEN_TIER, *keys),
    )]
    ids += [str(r[0]) for r in conn.execute(
        "SELECT id FROM episodes WHERE session_id = ? AND tier = ? AND tenant_id = ? AND json_valid(metadata_json) "
        f"AND json_extract(metadata_json, '$.chat') IN ({marks}) "
        "AND json_extract(metadata_json, '$.chat_turn') = ?",
        (_NOTES_SESSION, FORGOTTEN_TIER, owner, *keys, int(turn)),
    )]
    return ids


def replay_turn(conn: sqlite3.Connection, entry: Any) -> None:
    """Apply a journaled forget of one turn to *conn* (a restored copy may
    hold the turn, its ledger rows, or neither). Raises when a step fails."""
    keys = chat_keys(entry.session_key) or ([entry.session_key] if entry.session_key else [])
    owner = entry.tenant_id or "default"
    turn = int(entry.turn_number or 0)
    if keys and turn > 0 and entry.question_sha:
        conn.executemany(
            "INSERT OR IGNORE INTO memory_forgotten "
            "(tenant_id, session_key, turn_number, question_sha, episode_id, forgotten_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(owner, k, turn, entry.question_sha, entry.episode_id or None, entry.at) for k in keys],
        )
    held = conn.execute("SELECT id FROM episodes WHERE id = ?", (entry.episode_id,)).fetchone() \
        if entry.episode_id else None
    target = str(held[0]) if held else None
    if target is None and keys and entry.question_sha:
        marks = ",".join("?" for _ in keys)
        for r in conn.execute(
            f"SELECT id, user_text FROM episodes WHERE tenant_id = ? AND turn_number = ? "
            f"AND session_id IN ({marks})",
            (owner, turn, *keys),
        ).fetchall():
            if question_sha(r[1]) == entry.question_sha:
                target = str(r[0])
                break
    if target is not None:
        _forget_turn(conn, eid=target, owner=owner, keys=keys, turn=turn, sha=entry.question_sha,
                     by="journal", purge_index=True, seq=None)
        return
    # No memory of the turn in this copy: the agent's notes from it and its
    # facts may still be.
    notes = _chat_notes(conn, tenant_id=owner, keys=keys, turn=turn) if keys and turn > 0 else []
    if notes:
        _forget_turn(conn, eid=notes[0], owner=owner, keys=keys, turn=turn, sha=entry.question_sha,
                     by="journal", purge_index=True, seq=None)
        return
    now = time.time()
    facts = _forget_turn_facts(conn, tenant_id=owner, keys=keys, turn=turn, now=now)
    _forget_archived_facts(conn, tenant_id=owner, keys=keys, turn=turn, now=now)
    conn.commit()
    _after_facts(conn, owner=owner, changed=facts, keys=keys, turn=turn)


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


#: ``metadata.source`` of the Knowledge Library excerpts the supervisor copied
#: into a chat's memory until 2026-10-13 (Memory audit M04).
KNOWLEDGE_PROMOTION_SOURCES = ("knowledge_library_promote", "kb_promote")


def retire_knowledge_promotions(conn: sqlite3.Connection | None = None, *, limit: int = 500) -> dict[str, Any]:
    """Retire the Knowledge Library excerpts copied into chat memory.

    Until 2026-10-13 every turn that injected library chunks also stored up to
    two of them in the chat's memory, as ``user_text="[Knowledge: ...] ..."``
    -- crawled third-party text recalled as "Relevant History" and grouped
    into weekly summaries as if the user had said it (Memory audit M04). The
    copy is gone; the library still holds the text and is searched as it is.
    Each copy is emptied like a retired duplicate (no ledger row: the user
    forgot nothing), its summaries rebuilt, its mirrors and remote vector
    updated. A maintenance sweep; with nothing left, one SELECT.
    """
    own = conn is None
    conn = conn or _open()
    try:
        marks = ",".join("?" for _ in KNOWLEDGE_PROMOTION_SOURCES)
        rows = conn.execute(
            f"SELECT id, tenant_id FROM episodes WHERE tier != ? AND json_valid(metadata_json) "
            f"AND json_extract(metadata_json, '$.source') IN ({marks}) LIMIT ?",
            (FORGOTTEN_TIER, *KNOWLEDGE_PROMOTION_SOURCES, int(limit)),
        ).fetchall()
        if not rows:
            return {"retired": 0}
        ids = [str(r[0]) for r in rows]
        now = time.time()
        for eid in ids:
            _tombstone(conn, eid, now=now, by="knowledge_promotion_retired")
        from kazma_core.memory.topic_summaries import on_turns_forgotten

        summaries = on_turns_forgotten(conn, ids)
        purge_search_index(conn)
        conn.commit()
        from kazma_core.memory.state_backend import remirror_episodes

        remirror_episodes(conn, ids)
        remote = [_delete_remote_vector(conn, str(r[0]), str(r[1] or "default")) for r in rows]
        logger.info("[forget] retired %d Knowledge Library copies from chat memory", len(ids))
        return {"retired": len(ids), "summaries_emptied": len(summaries), "remote_vector_deleted": all(remote)}
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
    # Keep only what says where the memory came from; nothing it said. A
    # note keeps the chat and turn it was saved in: a forget cut short finds
    # it again by them (Memory audit M08).
    stamp = {
        "source": meta.get("source") if isinstance(meta, dict) else None,
        **{k: meta[k] for k in ("chat", "chat_turn") if isinstance(meta, dict) and k in meta},
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


#: What a forgotten fact keeps of its metadata: where it came from, never
#: what it said (an operator edit kept the earlier value under
#: ``operator_edit.previous``; Memory audit M01, 2026-10-13).
_FACT_PROVENANCE = ("source", "chat", "chat_turn", "forgotten")

#: What a redacted audit field says instead of the value.
REDACTED = "[redacted: forgotten]"


def _provenance_only(meta: Any, *, now: float) -> dict[str, Any]:
    kept = {k: meta[k] for k in _FACT_PROVENANCE if isinstance(meta, dict) and k in meta}
    kept.setdefault("forgotten", {"at": now})
    return kept


def _forget_fact_rows(conn: sqlite3.Connection, rows: list[Any], *, now: float) -> list[str]:
    """No longer current, without their value, vector or edit history.

    Returns the ids it changed. A fact forgotten before 2026-10-13 kept its
    vector and its metadata; asking again finishes it (and returns it)."""
    from kazma_core.memory.hygiene import invalidate_belief

    changed: list[str] = []
    for r in rows:
        tenant_id = str(r["tenant_id"] or "default")
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        kept = _provenance_only(meta, now=now)
        done = conn.execute(
            "SELECT 1 FROM beliefs WHERE id = ? AND object = '' AND embedding IS NULL "
            "AND metadata_json = ?",
            (r["id"], json.dumps(kept)),
        ).fetchone()
        if done is not None:
            continue
        if r["invalidated_at"] is None and r["valid_until"] is None:
            invalidate_belief(r["id"], conn=conn, now=now, tenant_id=tenant_id)
        conn.execute(
            "UPDATE beliefs SET object = '', embedding = NULL, metadata_json = ? WHERE id = ?",
            (json.dumps(kept), r["id"]),
        )
        changed.append(str(r["id"]))
    return changed


def _forget_archived_facts(
    conn: sqlite3.Connection, *, tenant_id: str | None, keys: list[str], turn: int | None, now: float,
) -> list[str]:
    """The archived copies (``beliefs_archive``: superseded long ago, or an
    operator's "Archive invalidated") of the facts the forgotten turn -- or,
    with *turn* None, the chat -- produced: kept as a row, without the value,
    vector or metadata. Returns the ids it changed."""
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    sql = (
        "SELECT id, original_belief_json FROM beliefs_archive WHERE json_valid(original_belief_json) "
        f"AND json_extract(original_belief_json, '$.source_session') IN ({marks})"
    )
    params: list[Any] = [*keys]
    if turn is not None:
        sql += " AND json_extract(original_belief_json, '$.source_turn') = ?"
        params.append(int(turn))
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    changed: list[str] = []
    for r in conn.execute(sql, params).fetchall():
        try:
            original = json.loads(r[1])
        except (TypeError, ValueError):
            continue
        meta = original.get("metadata_json")
        try:
            meta = json.loads(meta) if isinstance(meta, str) else meta
        except (TypeError, ValueError):
            meta = {}
        scrubbed = {k: v for k, v in original.items() if k not in ("object", "embedding", "metadata_json")}
        scrubbed["object"] = ""
        scrubbed["metadata_json"] = json.dumps(_provenance_only(meta, now=now))
        blob = json.dumps(scrubbed, ensure_ascii=False, default=str)
        if blob != r[1]:
            conn.execute("UPDATE beliefs_archive SET original_belief_json = ? WHERE id = ?", (blob, r[0]))
            changed.append(str(r[0]))
    return changed


#: The full-text indexes over memory text (``memory_stores``: erase "index").
#: Each is external-content: a trigger keeps it in step with its table.
_FTS_INDEXES = tuple(e.name for e in MEMORY_TABLES if e.erase == "index")


def purge_search_index(conn: sqlite3.Connection) -> None:
    """Rewrite each full-text index without the words of deleted rows.

    FTS5 records a removed row as a delete marker in a new segment; the old
    segment, the forgotten words in it, stays in ``*_fts_data`` until a merge
    happens to reach it (Memory audit M01, 2026-10-13). ``optimize`` merges
    the whole index into one segment, which drops them -- except when every
    segment sat on one level below the top, where FTS5 keeps every marker,
    word included: then the index is rebuilt from its table
    (:func:`kazma_core.db.fts5_purge.purge_deleted_rows`, found by the
    Knowledge soak, KB-Q01, 2026-10-15). It runs in the caller's transaction.
    """
    for name in _FTS_INDEXES:
        try:
            purge_deleted_rows(conn, name)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise


def _redacted(text: str | None) -> str | None:
    if text is None:
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return json.dumps({"redacted": "forgotten", "sha256": digest})


def _scrub_ops(fact_ids: list[str], *, keys: list[str], turn: int | None) -> dict[str, int]:
    """The operations database's copies: the audit log keeps each event and
    loses the forgotten values (their hash stays, so an event still matches
    its own record); a queued turn the user forgot loses its text."""
    import os

    from kazma_core.paths import memory_ops_db

    path = str(memory_ops_db())
    if not os.path.exists(path):
        return {"audit": 0, "queue": 0}
    ops = sqlite3.connect(path, check_same_thread=False, timeout=30)
    try:
        audit = _redact_audit(ops, fact_ids)
        queue = _scrub_queue(ops, keys=keys, turn=turn)
        ops.commit()
        return {"audit": audit, "queue": queue}
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return {"audit": 0, "queue": 0}
        raise
    finally:
        ops.close()


def _redact_audit(ops: sqlite3.Connection, fact_ids: list[str]) -> int:
    """Rows about a forgotten fact lose its value: its own events whole
    (reason and both states), another fact's event the side that named it --
    a supersede records the fact it replaced in ``state_before_json``."""
    if not fact_ids:
        return 0
    marks = ",".join("?" for _ in fact_ids)
    n = 0
    for column in ("state_before_json", "state_after_json"):
        rows = ops.execute(
            f"SELECT id, {column} FROM memory_audit_log WHERE json_valid({column}) "
            f"AND json_extract({column}, '$.id') IN ({marks})",
            fact_ids,
        ).fetchall()
        for r in rows:
            ops.execute(f"UPDATE memory_audit_log SET {column} = ? WHERE id = ?", (_redacted(r[1]), r[0]))
            n += 1
    rows = ops.execute(
        f"SELECT id, reason, state_before_json, state_after_json FROM memory_audit_log "
        f"WHERE target_id IN ({marks}) AND reason != ?",
        (*fact_ids, REDACTED),
    ).fetchall()
    for r in rows:
        before = r[2] if (r[2] or "").startswith('{"redacted"') else _redacted(r[2])
        after = r[3] if (r[3] or "").startswith('{"redacted"') else _redacted(r[3])
        ops.execute(
            "UPDATE memory_audit_log SET reason = ?, state_before_json = ?, state_after_json = ? WHERE id = ?",
            (REDACTED, before, after, r[0]),
        )
        n += 1
    return n


#: Queued tasks that carry a turn's text (``consolidator._defer_turn``).
_TURN_TEXT_TASKS = ("post_turn_memory",)


def _scrub_queue(ops: sqlite3.Connection, *, keys: list[str], turn: int | None) -> int:
    """A queued turn (pending or kept after it ran) loses its question and
    answer; the ledger already refuses its write."""
    if not keys:
        return 0
    marks = ",".join("?" for _ in keys)
    kinds = ",".join("?" for _ in _TURN_TEXT_TASKS)
    sql = (
        f"SELECT id, payload_json FROM memory_task_queue WHERE task_type IN ({kinds}) "
        f"AND json_valid(payload_json) AND json_extract(payload_json, '$.session_id') IN ({marks})"
    )
    params: list[Any] = [*_TURN_TEXT_TASKS, *keys]
    if turn is not None:
        sql += " AND json_extract(payload_json, '$.turn') = ?"
        params.append(int(turn))
    n = 0
    for r in ops.execute(sql, params).fetchall():
        payload = json.loads(r[1])
        if not (payload.get("user_text") or payload.get("assistant_text")):
            continue
        payload.update(user_text="", assistant_text="", forgotten=True)
        ops.execute("UPDATE memory_task_queue SET payload_json = ? WHERE id = ?", (json.dumps(payload), r[0]))
        n += 1
    return n


def _remirror_facts(conn: sqlite3.Connection, fact_ids: list[str]) -> None:
    from kazma_core.memory.state_backend import remirror_belief_by_id

    for bid in fact_ids:
        remirror_belief_by_id(conn, bid)


def _memory_texts(conn: sqlite3.Connection, ids: list[str]) -> list[tuple[str, str, str]]:
    """``(tenant, question, answer)`` of each memory, read before its tombstone
    empties it: a legacy table may hold the same text under another id."""
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    return [
        (str(r[0] or "default"), str(r[1] or ""), str(r[2] or ""))
        for r in conn.execute(
            f"SELECT tenant_id, user_text, assistant_text FROM episodes WHERE id IN ({marks})", ids
        ).fetchall()
    ]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


#: The shortest text a legacy truncation is matched by (shorter is too
#: likely to be a phrase two memories share).
_LEGACY_MATCH_MIN = 40


def _scrub_legacy_copies(
    conn: sqlite3.Connection, ids: list[str], texts: list[tuple[str, str, str]]
) -> int:
    """The tables a one-off 2026-08 operation left (``memory/legacy_tables.py``):
    ``episodes_archive`` keeps a restored memory's text under its id, or under
    another id with the same question; ``entities_archive`` keeps 200-character
    "memory_chunk" truncations of memories. Nothing reads them, but they are
    copies: a forgotten memory leaves none (Memory audit M01, 2026-10-13)."""
    n = 0
    episode_cols = _table_columns(conn, "episodes_archive")
    if episode_cols and ids:
        blank = [c for c in ("user_text", "assistant_text", "summary_text") if c in episode_cols]
        sets = ", ".join([f"{c} = ''" for c in blank] + (["embedding = NULL"] if "embedding" in episode_cols else []))
        marks = ",".join("?" for _ in ids)
        if sets:
            n += conn.execute(f"UPDATE episodes_archive SET {sets} WHERE id IN ({marks})", ids).rowcount
            if "user_text" in episode_cols and "tenant_id" in episode_cols:
                for tenant, question, _answer in texts:
                    if question.strip():
                        n += conn.execute(
                            f"UPDATE episodes_archive SET {sets} WHERE tenant_id = ? AND user_text = ?",
                            (tenant, question),
                        ).rowcount
    entity_cols = _table_columns(conn, "entities_archive")
    if entity_cols and texts:
        searched = [c for c in ("name", "metadata_json", "aliases_json") if c in entity_cols]
        pieces = [
            part[:120].strip()
            for _t, question, answer in texts
            for part in (question, answer)
            if len(part.strip()) >= _LEGACY_MATCH_MIN
        ]
        for piece in dict.fromkeys(pieces):
            where = " OR ".join(f"instr({c}, ?) > 0" for c in searched)
            clear = ", ".join(
                f"{c} = " + ("'{}'" if c == "metadata_json" else "'[]'" if c == "aliases_json" else "''")
                for c in searched
            )
            if where:
                n += conn.execute(
                    f"UPDATE entities_archive SET {clear} WHERE {where}", tuple(piece for _ in searched)
                ).rowcount
    return n


def _source_fact_ids(
    conn: sqlite3.Connection, *, tenant_id: str | None, keys: list[str], turn: int | None
) -> list[str]:
    """Every fact (current, history or archived) the turn -- or, with *turn*
    None, the chat -- produced."""
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    live_sql = f"SELECT id FROM beliefs WHERE source_session IN ({marks})"
    arch_sql = (
        "SELECT id FROM beliefs_archive WHERE json_valid(original_belief_json) "
        f"AND json_extract(original_belief_json, '$.source_session') IN ({marks})"
    )
    live_params: list[Any] = [*keys]
    arch_params: list[Any] = [*keys]
    if turn is not None:
        live_sql += " AND source_turn = ?"
        arch_sql += " AND json_extract(original_belief_json, '$.source_turn') = ?"
        live_params.append(int(turn))
        arch_params.append(int(turn))
    if tenant_id is not None:
        live_sql += " AND tenant_id = ?"
        arch_sql += " AND tenant_id = ?"
        live_params.append(tenant_id)
        arch_params.append(tenant_id)
    ids = [str(r[0]) for r in conn.execute(live_sql, live_params).fetchall()]
    ids += [str(r[0]) for r in conn.execute(arch_sql, arch_params).fetchall()]
    return list(dict.fromkeys(ids))


def _after_facts(
    conn: sqlite3.Connection, *, owner: str | None, changed: list[str], keys: list[str],
    turn: int | None,
) -> list[bool]:
    """After the commit: the mirrors, the remote vectors, the operations
    database. Every fact the turn or chat produced is asked about, not only
    the ones changed now -- a forget from before 2026-10-13 left these."""
    _remirror_facts(conn, changed)
    every = _source_fact_ids(conn, tenant_id=owner, keys=keys, turn=turn)
    tenants = {
        str(r[0]): str(r[1] or "default")
        for r in conn.execute(
            f"SELECT id, tenant_id FROM beliefs WHERE id IN ({','.join('?' for _ in every)})", every
        )
    } if every else {}
    remote = [_delete_remote_vector(conn, bid, tenants.get(bid, owner or "default")) for bid in every]
    _scrub_ops(every, keys=keys, turn=turn)
    return remote


def _delete_remote_vector(conn: sqlite3.Connection, eid: str, tenant_id: str) -> bool:
    """The vector a remote index holds for the memory. The local copy went with
    the tombstone (``embedding = NULL``)."""
    from kazma_core.memory import forget_journal
    from kazma_core.memory.backends import get_remote_vector_backend

    del conn
    # The remote index only: the local backend's delete re-wrote the row and
    # committed the forget's connection (Memory audit M15).
    backend = get_remote_vector_backend()
    if backend is None:
        return True  # vectors are local only: the tombstone took this one
    try:
        done = bool(backend.delete(eid, tenant_id=tenant_id))
    except (RuntimeError, OSError, sqlite3.Error, ValueError):
        # failover=raise with the remote down, or the remote call failing:
        # the memory is forgotten here, the remote copy is not yet.
        logger.debug("[forget] remote delete of %s failed", eid, exc_info=True)
        done = False
    if not done:
        # Retried every maintenance pass until it is gone (Memory audit M08:
        # the log line said "can be retried" and nothing retried it).
        logger.warning("[forget] remote vector of %s not deleted yet; retried each pass", eid)
        forget_journal.queue_remote_delete(eid, tenant_id)
    return done


def forget_belief_source(belief_id: str, *, tenant_id: str) -> dict[str, Any]:
    """Forget the conversation turn a fact came from, as the Memory page's
    Forget does (:func:`forget_episode`, ``by="user"``), and every memory
    that repeats a distinctive value of it.

    For a user who asks in chat to forget something they said: invalidating
    the fact alone left the turn a memory, so recall found it at once and the
    next turn that repeated it learned the fact again (live 2026-10-19). The
    turn is the fact's ``source_session`` / ``source_turn``, under every key
    of its chat. Kazma's own later answers that quoted the value ("Your test
    codeword is BLUE-HERON-42") kept it too, and a new chat recalled one: a
    value that names one thing (:func:`_distinctive_value`: one token with a
    digit, ``-`` or ``_``) is forgotten wherever a memory of the tenant holds
    it. A plain word ("London") is not swept: it would take every turn that
    mentions it. A fact with neither a source turn nor such a value says so.
    """
    bid = (belief_id or "").strip()
    if not bid:
        return {"ok": False, "error": "belief_id required"}
    caller = tenant_id or "default"
    conn = _open()
    try:
        row = conn.execute(
            "SELECT tenant_id, source_session, source_turn, object FROM beliefs WHERE id = ?", (bid,),
        ).fetchone()
        if row is None or (caller != "default" and str(row["tenant_id"] or "default") != caller):
            return {"ok": False, "error": "not_found"}
        owner = str(row["tenant_id"] or "default")
        session, turn = str(row["source_session"] or ""), row["source_turn"]
        source: list[str] = []
        if session and turn is not None:
            keys = chat_keys(session) or [session]
            marks = ",".join("?" * len(keys))
            source = [str(r["id"]) for r in conn.execute(
                f"SELECT id FROM episodes WHERE session_id IN ({marks}) AND turn_number = ?",  # nosec B608 -- placeholders only
                (*keys, int(turn)),
            ).fetchall()]
        value = _distinctive_value(str(row["object"] or ""))
        repeats = _episodes_holding(conn, value, tenant_id=owner) if value else []
    finally:
        conn.close()
    episodes = list(dict.fromkeys([*source, *repeats]))
    if not episodes:
        return {"ok": False, "error": "no_source_turn"}
    results = [forget_episode(eid, tenant_id=caller, by="user") for eid in episodes]
    return {"ok": all(r.get("ok", True) for r in results), "episodes_forgotten": episodes,
            "source_turn": source, "repeated_value": [e for e in episodes if e not in source],
            "chat": session, "turn": int(turn) if turn is not None else None}


#: The shortest text :func:`forget_mentions` takes: shorter matches half the
#: memory store ("ok", "the").
MENTION_MIN_CHARS = 4


def keep_turn_out(session_key: str, turn: int, question: str, *, tenant_id: str) -> bool:
    """Keep a turn not yet written out of memory: the ledger row its writer
    asks first (:func:`refuses_write`), under every key of its chat.

    For the turn in which the user asks to forget something: written after
    the forget, "Forget every memory that mentions BLUE-HERON-42" became a
    memory holding the codeword (live 2026-10-19). False when there is no
    chat turn to name."""
    sha = question_sha(question)
    if not session_key or int(turn or 0) <= 0 or not sha:
        return False
    keys = chat_keys(session_key) or [session_key]
    conn = _open()
    try:
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO memory_forgotten "
                "(tenant_id, session_key, turn_number, question_sha, episode_id, forgotten_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(tenant_id or "default", k, int(turn), sha, "", time.time()) for k in keys],
            )
    finally:
        conn.close()
    return True


def forget_mentions(text: str, *, tenant_id: str) -> dict[str, Any]:
    """Forget every memory and current fact of the tenant that holds *text*
    (any case): the user's "forget everything that mentions X".

    The agent could only forget what ``memory_search`` showed it, and search
    ranks the few best matches: asked to forget every memory mentioning a
    codeword, it forgot the 2 of 7 it was shown and reported the job done
    (live 2026-10-19), and the next new chat answered with the codeword.
    Each memory goes through :func:`forget_episode` (``by="user"``), each fact
    through ``hygiene.invalidate_belief``.
    """
    needle = (text or "").strip()
    if len(needle) < MENTION_MIN_CHARS:
        return {"ok": False, "error": "text_too_short", "min_chars": MENTION_MIN_CHARS}
    caller = tenant_id or "default"
    from kazma_core.db.sql_patterns import like_literal

    conn = _open()
    try:
        episodes = _episodes_holding(conn, needle, tenant_id=caller)
        pattern = f"%{like_literal(needle)}%"
        facts = [str(r["id"]) for r in conn.execute(
            "SELECT id FROM beliefs WHERE COALESCE(tenant_id, 'default') = ? "
            "AND valid_until IS NULL AND invalidated_at IS NULL AND object LIKE ? ESCAPE '!'",
            (caller, pattern),
        ).fetchall()]
    finally:
        conn.close()
    from kazma_core.skills.self_improvement import forget_text

    lessons = forget_text(needle)
    if not episodes and not facts and not lessons:
        return {"ok": False, "error": "nothing_holds_it"}
    from kazma_core.memory.hygiene import invalidate_belief

    fact_results = [invalidate_belief(bid, remove_graph=True, tenant_id=caller) for bid in facts]
    memory_results = [forget_episode(eid, tenant_id=caller, by="user") for eid in episodes]
    logger.info("[forget] mentions: %d memor%s and %d fact(s) forgotten for tenant %s",
                len(episodes), "y" if len(episodes) == 1 else "ies", len(facts), caller)
    return {
        "ok": all(r.get("ok", True) for r in [*fact_results, *memory_results]),
        "memories_forgotten": episodes, "facts_invalidated": facts, "lessons_removed": lessons,
    }


def _distinctive_value(value: str) -> str:
    """*value* when it names one thing -- one token of 6+ characters holding a
    digit, ``-`` or ``_`` (a codeword, an account or order number, a phone
    number) -- else ``""``."""
    value = (value or "").strip()
    if len(value) < 6 or any(ch.isspace() for ch in value):
        return ""
    return value if any(ch.isdigit() or ch in "-_" for ch in value) else ""


def _episodes_holding(conn: sqlite3.Connection, value: str, *, tenant_id: str) -> list[str]:
    """The tenant's remembered episodes whose text holds *value* (any case)."""
    from kazma_core.db.sql_patterns import like_literal

    pattern = f"%{like_literal(value)}%"
    return [str(r["id"]) for r in conn.execute(
        "SELECT id FROM episodes WHERE COALESCE(tenant_id, 'default') = ? AND tier != ? "
        "AND (user_text LIKE ? ESCAPE '!' OR assistant_text LIKE ? ESCAPE '!' "
        "OR summary_text LIKE ? ESCAPE '!')",
        (tenant_id, FORGOTTEN_TIER, pattern, pattern, pattern),
    ).fetchall()]


def forget_chat(
    key: str, *, tenant_id: str, conn: sqlite3.Connection | None = None, journal: bool = True,
) -> dict[str, Any]:
    """Forget every memory the chat *key* left, under either of its keys.
    Journaled as one entry (``forget_journal``)."""
    keys = chat_keys(key)
    if not keys:
        return {"ok": False, "error": "chat required"}
    seq = None
    if journal:
        from kazma_core.memory import forget_journal

        seq = forget_journal.append("chat", tenant_id=tenant_id or "default", session_key=key)
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
            facts += int(
                forget_episode(eid, tenant_id=caller, conn=conn, purge_index=False, journal=False)
                .get("facts_forgotten") or 0
            )
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
        note_texts = _memory_texts(conn, notes)
        for note in notes:
            _tombstone(conn, note, now=now, by="user")
        _scrub_legacy_copies(conn, notes, note_texts)
        chat_facts = _forget_chat_facts(conn, tenant_id=owner, keys=keys, now=now)
        _forget_archived_facts(conn, tenant_id=owner, keys=keys, turn=None, now=now)
        if notes:
            from kazma_core.memory.topic_summaries import on_turns_forgotten

            on_turns_forgotten(conn, notes)
        purge_search_index(conn)
        conn.commit()
        if notes:
            from kazma_core.memory.state_backend import remirror_episodes

            remirror_episodes(conn, notes)
            for note in notes:
                _delete_remote_vector(conn, note, note_tenants.get(note, caller))
        _after_facts(conn, owner=owner, changed=chat_facts, keys=keys, turn=None)
        from kazma_core.memory import forget_journal

        forget_journal.mark_applied(conn, seq)
        conn.commit()
        # Forgetting the chat takes its Time Travel steps too (TT-05).
        snapshots = _forget_snapshots(keys, turn=None)
        return {"ok": True, "forgotten": len(ids) + len(notes),
                "facts_forgotten": facts + len(chat_facts), "chat_keys": keys,
                "snapshots_deleted": snapshots}
    finally:
        if own:
            conn.close()


def _first_rewound_turn(messages: list[Any]) -> int:
    """The first turn a chat state left as *messages* no longer holds an
    answer to: its user messages, counted, when the last one is unanswered;
    the next turn otherwise (``consolidator.user_turn_index``'s counting)."""
    from kazma_core.memory.consolidator import user_turn_index

    def role(m: Any) -> str:
        return str((m.get("role") if isinstance(m, dict) else getattr(m, "type", "")) or "")

    count = user_turn_index(messages)
    last_user = max((i for i, m in enumerate(messages or []) if role(m) in ("user", "human")), default=-1)
    answered = any(role(m) in ("assistant", "ai") for m in (messages or [])[last_user + 1:])
    return count + 1 if answered or last_user < 0 else count


def _forget_rewound_turns(
    thread_id: str, old_messages: list[Any], *, from_turn: int, tenant_id: str = "default",
) -> dict[str, Any]:
    """Forget what memory holds of a chat's turns from *from_turn* on: undo,
    edit, a replay restore and abort took them back (Memory audit M09).

    By position, every memory of those turns goes -- the episodes, the
    agent's notes, the facts (``source_turn``) -- under every key of the chat
    and every tenant that holds them; the questions the old state asked
    (*old_messages*) get ledger rows too, journaled, so a deferred write, the
    deep pass and turn reconcile (the transcript still holds the old turn)
    do not write them again. Until 2026-10-13 a rewind left them all, and
    after ``/edit`` both answers were memories of the same turn.
    """
    from kazma_core.memory import forget_journal
    from kazma_core.memory.consolidator import extract_turn_texts

    keys = chat_keys(thread_id) or ([thread_id] if thread_id else [])
    if not keys or from_turn < 1:
        return {"ok": False, "error": "chat required"}
    conn = _open()
    try:
        marks = ",".join("?" for _ in keys)
        tenants = {tenant_id or "default"} | {
            str(r[0] or "default") for r in conn.execute(
                f"SELECT DISTINCT tenant_id FROM episodes WHERE session_id IN ({marks}) AND turn_number >= ?",
                (*keys, int(from_turn)),
            )
        }
        # The questions of the rewound turns, as memory records them.
        questions: dict[int, str] = {}
        seen = 0
        for i, m in enumerate(old_messages or []):
            r = m.get("role") if isinstance(m, dict) else getattr(m, "type", "")
            if r in ("user", "human"):
                seen += 1
                if seen >= from_turn:
                    questions[seen] = extract_turn_texts(list(old_messages)[: i + 1])[0]
        stored = {int(r[0]) for r in conn.execute(
            f"SELECT DISTINCT turn_number FROM episodes WHERE session_id IN ({marks}) AND turn_number >= ?",
            (*keys, int(from_turn)),
        )}
        stored |= {int(r[0]) for r in conn.execute(
            f"SELECT DISTINCT source_turn FROM beliefs WHERE source_session IN ({marks}) AND source_turn >= ?",
            (*keys, int(from_turn)),
        )}
        turns = 0
        for turn in sorted(stored | set(questions)):
            for owner in sorted(tenants):
                sha = question_sha(questions.get(turn)) if turn in questions else ""
                seq = forget_journal.append("turn", tenant_id=owner, session_key=thread_id, turn_number=turn,
                                            question_sha=sha)
                for (eid,) in conn.execute(
                    f"SELECT id FROM episodes WHERE tenant_id = ? AND session_id IN ({marks}) "
                    "AND turn_number = ? AND tier != ?",
                    (owner, *keys, turn, FORGOTTEN_TIER),
                ).fetchall():
                    forget_episode(str(eid), tenant_id=owner, conn=conn, journal=False, by="rewind")
                entry = forget_journal.Entry(seq, time.time(), "turn", owner, thread_id, turn, sha, "", "")
                replay_turn(conn, entry)
                forget_journal.mark_applied(conn, seq)
                conn.commit()
            turns += 1
        if turns:
            logger.info("[forget] rewind of %s took back %d turn(s) from memory", thread_id, turns)
        return {"ok": True, "turns": turns}
    finally:
        conn.close()


async def rewind_memory(
    thread_id: str, old_messages: list[Any], new_messages: list[Any], *,
    tenant_id: str = "default", from_turn: int | None = None,
) -> dict[str, Any]:
    """What every chat-state rewrite (undo, edit, replay restore, abort) calls
    after it writes: the turns it took back leave memory too
    (:func:`_forget_rewound_turns`, in a thread). *from_turn* when the rewrite
    replaced a turn it kept (``/edit``); else the first turn *new_messages*
    holds no answer to. Never raises: the rewrite already happened."""
    import asyncio

    turn = from_turn if from_turn is not None else _first_rewound_turn(new_messages)
    try:
        return await asyncio.to_thread(_forget_rewound_turns, thread_id, list(old_messages or []),
                                       from_turn=turn, tenant_id=tenant_id or "default")
    except (sqlite3.Error, OSError, RuntimeError, ValueError, TypeError):
        logger.warning("[forget] the rewind of %s could not take its turns back from memory; "
                       "the maintenance pass applies the journal", thread_id, exc_info=True)
        return {"ok": False}


def enforce_ledger(conn: sqlite3.Connection, *, limit: int = 200) -> dict[str, int]:
    """Forget every stored memory the ledger refuses (Memory audit M08).

    The writer checks the ledger before it inserts; a forget or a keep-out
    that lands in the window, or a restore, could still leave a row the
    ledger refuses, and nothing applied the ledger to rows already written:

    - a memory of a turn the user forgot (same chat, turn and question);
    - a memory written into a chat while it was kept out (written after the
      keep-out);
    - a memory of a chat whose past the user forgot in the last day (the
      race window), written before that forget;
    - a weekly summary still active though a turn it was written from is
      forgotten.
    """
    from kazma_core.memory import forget_journal
    from kazma_core.memory.topic_summaries import on_turns_forgotten

    targets: dict[str, str] = {}
    for eid, tenant, text, sha in conn.execute(
        "SELECT e.id, e.tenant_id, e.user_text, f.question_sha FROM memory_forgotten f "
        "JOIN episodes e ON e.tenant_id = f.tenant_id AND e.session_id = f.session_key "
        "AND e.turn_number = f.turn_number WHERE f.turn_number > 0 AND e.tier != ?",
        (FORGOTTEN_TIER,),
    ).fetchall():
        if question_sha(text) == sha:
            targets[str(eid)] = str(tenant or "default")
    for eid, tenant in conn.execute(
        "SELECT e.id, e.tenant_id FROM memory_forgotten f JOIN episodes e ON e.tenant_id = f.tenant_id "
        "AND e.session_id = f.session_key WHERE f.turn_number = 0 AND f.question_sha = '' "
        "AND e.tier != ? AND e.created_at >= f.forgotten_at",
        (FORGOTTEN_TIER,),
    ).fetchall():
        targets.setdefault(str(eid), str(tenant or "default"))
    for entry in forget_journal.recent_chat_forgets():
        keys = chat_keys(entry.session_key) or [entry.session_key]
        marks = ",".join("?" for _ in keys)
        for (eid,) in conn.execute(
            f"SELECT id FROM episodes WHERE tenant_id = ? AND session_id IN ({marks}) AND tier != ? "
            "AND created_at <= ?",
            (entry.tenant_id, *keys, FORGOTTEN_TIER, entry.at),
        ).fetchall():
            targets.setdefault(str(eid), entry.tenant_id)
    forgotten = 0
    for eid, tenant in list(targets.items())[:limit]:
        if forget_episode(eid, tenant_id=tenant, conn=conn, journal=False, by="ledger").get("ok"):
            forgotten += 1
    stale = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT src.episode_id FROM memory_summaries s JOIN memory_summary_sources src "
        "ON src.summary_id = s.id JOIN episodes e ON e.id = src.episode_id "
        "WHERE s.status = 'active' AND e.tier = ?",
        (FORGOTTEN_TIER,),
    )]
    summaries = on_turns_forgotten(conn, stale, only_active=True) if stale else []
    conn.commit()
    if forgotten or summaries:
        logger.warning("[forget] the ledger refused %d stored memorie(s) and %d active summarie(s): applied",
                       forgotten, len(summaries))
    return {"memories": forgotten, "summaries": len(summaries)}


def converge(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The maintenance pass "forget convergence" (Memory audits M08/M10): the
    journal's pending entries, the ledger over stored rows, remote deletes."""
    from kazma_core.memory import forget_journal

    own = conn is None
    conn = conn or _open()
    try:
        journal = forget_journal.apply_pending(conn)
        ledger = enforce_ledger(conn)
        remote = forget_journal.retry_remote_deletes(conn)
        return {"journal": journal, "ledger": ledger, "remote": remote}
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
