"""Every conversation turn reaches long-term memory -- reconciled from the chat store.

The chat store (Postgres ``kazma_chat_sessions`` or SQLite ``chat_sessions.db``)
holds every conversation Kazma has had. Long-term memory holds one episode per
turn, written when the turn closes (``kazma_ui.turn_runtime.close_turn`` ->
``consolidator.remember_turn``). When that write does not happen, the turn is
missing from memory for good: from 2026-08-08 to 2026-09-26 no web chat turn
was written at all (the web never called it; 877 turns on the live install),
and 127 gateway turns were lost to a full extraction pool, a crash between the
reply and the write, or an id collision.

This pass makes memory converge on the chat store. Every 15 minutes it reads
the sessions changed since its cursor and writes an episode for each turn that
has none, stamped with the turn's own time (``dual_write.mirror_episode``, the
one episode writer). A turn younger than :data:`_SETTLE_S` is left to the live
pipeline. The episodes are searchable by words and meaning like any other.

Then the turns it wrote get their facts, as a live turn does: the heuristic
pass at once and the LLM deep pass queued (``micro_consolidation``, at most
:data:`_DEEP_PER_PASS` a pass), both at the turn's own time. Until 2026-09-27
it wrote episodes only, because facts were ordered by when they were written
and replaying an old "I live in X" would have overwritten a newer "I moved to
Y"; ``mutate_belief`` now records an older statement as history (W1). A
cursor on the episode's rowid picks up every episode this pass has written,
whatever the turn's own time.

A turn is already in memory when an episode of the same conversation has the
same question -- counted, so a question asked twice needs two -- or when an
erased row's stub starts with it (``rehydrate`` owns those).

The cursors live in the memory database (``memory_reconcile_state``,
2026-10-14), beside what they describe. In the settings store, a memory
database restored from a backup met a cursor that had already passed the
chats changed since the backup: their newer turns were never memories again
(the restore drill, ``tests/test_memory_restore_drill.py``). A restored copy
now carries the cursor of its own moment. A database without the row (an
older build's, or an older backup) reads every chat once: a turn already in
memory is skipped, so the rescan only adds what is missing.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from kazma_core.memory.chat_history import STORE_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["STATE_KEY", "read_state", "run_turn_reconcile_pass"]

#: ConfigStore key: the last pass's report (memory health shows it). The
#: cursors are in the memory database (:func:`read_state`).
STATE_KEY = "memory.v2.turn_reconcile"

#: A turn younger than this belongs to the live pipeline (close_turn hands it
#: to memory within seconds); reconciling it too would race that write.
_SETTLE_S = 600.0
_PAGE = 100
_TEXT_CAP = 4000
#: LLM deep passes queued per 15-minute pass, so ~1,000 reconciled turns reach
#: the model over hours, never in one burst.
_DEEP_PER_PASS = 60


def _epoch(value: Any) -> float | None:
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


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _turns(messages: list[Any]) -> list[tuple[int, str, str, float | None]]:
    """``(turn index, question, answer, when)`` of every turn with a question:
    the answer AFTER it (``consolidator.extract_turn_texts``), the index as
    ``consolidator.user_turn_index`` counts it. Slash commands are skipped,
    as the live pipeline skips them."""
    from kazma_core.memory.chat_history import normalize_message
    from kazma_core.memory.consolidator import extract_turn_texts, message_text

    msgs = [normalize_message(m) for m in messages or []]
    out: list[tuple[int, str, str, float | None]] = []
    index = 0
    last_question = ""
    when: float | None = None
    for end in range(1, len(msgs) + 1):
        msg = msgs[end - 1]
        if msg.get("role") == "user":
            index += 1
            last_question = message_text(msg.get("content"))
            when = _epoch(msg.get("ts"))
        closes = end == len(msgs) or msgs[end].get("role") == "user"
        if not (index and closes and last_question):
            continue  # no question yet, mid-turn, or an attachment-only message
        question, answer = extract_turn_texts(msgs[:end])
        if question and not question.startswith("/"):
            out.append((index, question, answer, when))
    return out


def _in_memory(conn: sqlite3.Connection, tenant: str, keys: list[str]) -> tuple[Counter, list[str]]:
    """The conversation's episode questions (counted) and erased-row stubs."""
    marks = ",".join("?" for _ in keys)
    texts: Counter = Counter()
    stubs: list[str] = []
    for user, summary in conn.execute(
        f"SELECT user_text, summary_text FROM episodes WHERE tenant_id = ? AND session_id IN ({marks})",
        [tenant, *keys],
    ):
        if user is not None:
            texts[str(user).strip()] += 1
        elif summary:
            stubs.append(str(summary))
    return texts, stubs


def _reconcile_session(
    conn: sqlite3.Connection, row: dict[str, Any], settled_before: float
) -> tuple[int, int]:
    """Write the session's missing settled turns. Returns (written, still young)."""
    from kazma_core.memory.dual_write import mirror_episode

    thread = row["thread_id"] or row["session_id"]
    keys = list(dict.fromkeys(k for k in (thread, row["session_id"]) if k))
    texts, stubs = _in_memory(conn, row["tenant_id"], keys)
    fallback = _epoch(row["updated_at"]) or _epoch(row["created_at"])
    written = young = 0
    for index, question, answer, when in _turns(row["messages"]):
        stored = question[:_TEXT_CAP].strip()
        if texts[stored] > 0:
            texts[stored] -= 1
            continue
        head = question[:200].strip(" ")
        stub = next((s for s in stubs if s.startswith(head)), None)
        if stub is not None:
            stubs.remove(stub)
            continue
        at = when if when is not None else fallback
        if at is None:
            at = settled_before - 1.0  # a legacy row with no time at all: settled
        if at >= settled_before:
            young += 1  # the live pipeline's to write; checked again next pass
            continue
        if mirror_episode(
            session_id=thread,
            turn_number=index,
            user_text=question[:_TEXT_CAP],
            assistant_text=answer[:_TEXT_CAP],
            tenant_id=row["tenant_id"],
            tier="episodic",
            source="turn_reconcile",
            created_at=at,
        ):
            written += 1
    return written, young


def _reconcile(
    conn: sqlite3.Connection, after: tuple[str, str], deadline: float, now: float
) -> dict[str, Any]:
    from kazma_core.memory.chat_history import sessions_changed_since

    settled_before = now - _SETTLE_S
    settled_iso = _iso(settled_before)
    report: dict[str, Any] = {"sessions": 0, "turns_written": 0, "turns_waiting": 0,
                              "done": False}
    cursor = scan = after
    frozen = False  # an unsettled session was met: the cursor stays before it
    while time.monotonic() < deadline:
        rows = sessions_changed_since(scan, limit=_PAGE)
        if not rows:
            report["done"] = True
            break
        for row in rows:
            if time.monotonic() >= deadline:
                break
            written, young = _reconcile_session(conn, row, settled_before)
            report["sessions"] += 1
            report["turns_written"] += written
            report["turns_waiting"] += young
            scan = (row["updated_at"], row["session_id"])
            # The cursor passes only a run of settled sessions: one still
            # changing (or with a turn too young) is read again next pass.
            if young or row["updated_at"] >= settled_iso:
                frozen = True
            elif not frozen:
                cursor = scan
    report["after"] = list(cursor)
    return report


#: Passes a turn's fact extraction may fail before it is set aside (Memory
#: audit M27): one turn that always failed kept the cursor before it, and no
#: later reconciled turn ever had its facts extracted.
_FACT_ATTEMPTS = 3


def _set_aside(conn: sqlite3.Connection, episode_id: str, error: str) -> None:
    """Mark the episode's facts as failed (``metadata.facts_failed``): the
    memory stays recallable; only its facts are not extracted."""
    conn.execute(
        "UPDATE episodes SET metadata_json = json_set(COALESCE(metadata_json, '{}'), '$.facts_failed', ?) "
        "WHERE id = ?", (error[:300], episode_id))
    conn.commit()


def _extract_facts(
    conn: sqlite3.Connection, after_rowid: int, deadline: float,
    attempts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Facts for the episodes this pass wrote, oldest write first.

    Same two passes as a live turn (``consolidator._run_turn_memory``), at
    the turn's own time. A turn whose extraction fails keeps the cursor before
    it and the next pass tries again; after ``_FACT_ATTEMPTS`` passes it is set
    aside (``metadata.facts_failed``, a WARNING naming it) and the cursor moves
    on. *attempts* (episode id -> failed passes) carries over between passes in
    the reconcile state. A locked database is an outage, never a verdict: it
    raises to the maintenance runner and counts nothing.
    """
    from kazma_core.memory.consolidator import _v2_extract_sync
    from kazma_core.memory.task_queue import enqueue_task

    attempts = dict(attempts or {})
    report: dict[str, Any] = {"facts_turns": 0, "deep_queued": 0, "facts_after": after_rowid,
                              "facts_done": False, "fact_attempts": attempts, "facts_set_aside": 0}
    rows = conn.execute(
        """SELECT rowid, id, session_id, turn_number, user_text, assistant_text, tenant_id,
                  created_at
           FROM episodes
           WHERE rowid > ? AND json_extract(metadata_json, '$.source') = 'turn_reconcile'
           ORDER BY rowid LIMIT ?""",
        (after_rowid, _DEEP_PER_PASS),
    ).fetchall()
    for rowid, episode_id, session, turn, user, answer, tenant, at in rows:
        if time.monotonic() >= deadline:
            return report
        messages = [{"role": "user", "content": user or ""}]
        if answer:
            messages.append({"role": "assistant", "content": answer})
        try:
            _v2_extract_sync(messages, session_id=session, turn=turn, tenant_id=tenant, now=at)
        except sqlite3.OperationalError:
            raise  # locked or busy: an outage, counted by the runner (Memory audit M20)
        except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
            failed = attempts.get(str(episode_id), 0) + 1
            error = f"{type(exc).__name__}: {exc}"
            if failed < _FACT_ATTEMPTS:
                attempts[str(episode_id)] = failed
                logger.info("[memory] facts of reconciled turn %s failed (pass %d of %d): %s",
                            episode_id, failed, _FACT_ATTEMPTS, error)
                return report  # the cursor stays before it
            attempts.pop(str(episode_id), None)
            _set_aside(conn, str(episode_id), error)
            logger.warning("[memory] facts of reconciled turn %s set aside after %d failed passes: %s",
                           episode_id, failed, error)
            report["facts_set_aside"] += 1
            report["facts_after"] = rowid
            continue
        attempts.pop(str(episode_id), None)
        if enqueue_task("micro_consolidation", {"episode_id": episode_id}):
            report["deep_queued"] += 1
        report["facts_turns"] += 1
        report["facts_after"] = rowid
    report["facts_done"] = len(rows) < _DEEP_PER_PASS
    return report


def _open_primary() -> sqlite3.Connection | None:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import primary_memory_db

    db = primary_memory_db()
    if not db or not os.path.isfile(db):
        return None
    from kazma_core.memory.schema_v2 import RECONCILE_STATE_DDL

    conn = sqlite3.connect(db, timeout=15)
    apply_sqlite_pragmas(conn, busy_timeout=15000)
    conn.executescript(RECONCILE_STATE_DDL)  # a database an older build left
    return conn


def read_state(conn: sqlite3.Connection | None) -> dict[str, Any]:
    """The cursors this memory database was reconciled up to: ``after``
    (``(updated_at, session_id)`` of the chat store), ``facts_after`` (an
    episode rowid) and ``fact_attempts``. Without a row -- an older build's
    database, or one restored from an older backup -- every chat is read
    again (``after`` empty), and the facts cursor goes on from where the
    settings store had it (where it lived until 2026-10-14), never past this
    database's newest episode: a restored copy's rebuilt turns come after
    it. With no cursor anywhere, from the start (a fact written twice adds
    nothing)."""
    own = conn is None
    conn = conn or _open_primary()
    if conn is None:
        return {"after": ["", ""], "facts_after": 0, "fact_attempts": {}}
    try:
        row = conn.execute("SELECT state_json FROM memory_reconcile_state WHERE id = 1").fetchone()
        if row is not None:
            state = json.loads(row[0] or "{}")
            if isinstance(state, dict):
                return {"after": list(state.get("after") or ["", ""]),
                        "facts_after": int(state.get("facts_after") or 0),
                        "fact_attempts": dict(state.get("fact_attempts") or {})}
        newest = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM episodes").fetchone()[0])
        legacy = _legacy_facts_after()
        return {"after": ["", ""], "facts_after": min(legacy, newest) if legacy is not None else 0,
                "fact_attempts": {}}
    finally:
        if own:
            conn.close()


def _legacy_facts_after() -> int | None:
    """The facts cursor an older build kept in the settings store, if any."""
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_helpers import store_errors

    try:
        state = get_config_store().get(STATE_KEY)
    except store_errors():
        return None
    if isinstance(state, dict) and state.get("facts_after") is not None:
        try:
            return int(state["facts_after"])
        except (TypeError, ValueError):
            return None
    return None


def _write_state(conn: sqlite3.Connection | None, state: dict[str, Any]) -> None:
    """Record the cursors in this memory database (one row)."""
    own = conn is None
    conn = conn or _open_primary()
    if conn is None:
        return
    try:
        value = json.dumps({"after": list(state.get("after") or ["", ""]),
                            "facts_after": int(state.get("facts_after") or 0),
                            "fact_attempts": dict(state.get("fact_attempts") or {})})
        with conn:
            conn.execute(
                "INSERT INTO memory_reconcile_state (id, state_json, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                (value, time.time()),
            )
    finally:
        if own:
            conn.close()


def run_turn_reconcile_pass(*, time_budget_s: float = 60.0) -> dict[str, Any]:
    """One reconcile pass (15-minute cadence). The cursors are kept in the
    memory database (:func:`read_state`); the report under :data:`STATE_KEY`.
    Returns the report (``{}`` without a database)."""
    from kazma_core.config_store import get_config_store

    conn = _open_primary()
    if conn is None:
        return {}
    started = time.monotonic()
    deadline = started + max(0.0, float(time_budget_s))
    try:
        state = read_state(conn)
        after = tuple(state["after"])
        facts_after = state["facts_after"]
        fact_attempts = state["fact_attempts"]
        report = _reconcile(conn, after, deadline, time.time())
        report.update(_extract_facts(conn, facts_after, deadline, fact_attempts))
        if (list(after) != report["after"] or report["facts_after"] != facts_after
                or report["fact_attempts"] != fact_attempts):
            _write_state(conn, report)
    except STORE_ERRORS:
        logger.warning("[memory] conversation history unreadable; turn reconcile waits", exc_info=True)
        return {"error": "chat store unreadable"}
    finally:
        conn.close()
    report["duration_s"] = round(time.monotonic() - started, 3)
    if report["turns_written"] or report["facts_turns"] or report["facts_set_aside"]:
        get_config_store().set(STATE_KEY, {"last": {**report, "at": time.time()}}, category="memory")
    if report["turns_written"]:
        logger.info(
            "[memory] %d conversation turns from %d sessions added to memory from the chat "
            "history%s", report["turns_written"], report["sessions"],
            "" if report["done"] else " (more next pass)",
        )
    if report["facts_turns"]:
        logger.info(
            "[memory] facts extracted from %d reconciled turns at their own time; %d queued "
            "for the deep pass%s", report["facts_turns"], report["deep_queued"],
            "" if report["facts_done"] else " (more next pass)",
        )
    return report
