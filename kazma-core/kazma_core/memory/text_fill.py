"""Bring back the text a memory lost to the old cut, from the chat that still has it.

Until 2026-11-19 every episode writer kept the first 4,000 characters of a
turn's question and of its answer (``dual_write``, ``turn_reconcile``;
``memory/limits.py``). Memories are kept whole since; this maintenance pass
("memory text fill") fills the rows written before from the chat store and
its save spool (``memory/chat_history.conversations_for``), where the turn is
still whole:

* **Which rows.** A turn's memory (``e_`` id) whose question or answer is
  exactly :data:`~kazma_core.memory.limits.LEGACY_TEXT_CUT` characters long --
  what the old cut left -- that is neither forgotten (tier) nor a tombstone
  (no text), and that no earlier pass marked.
* **What fills it.** The chat the row names, in the row's own tenant (a
  copy of another space's chat is never read into it), each turn read the
  two ways memory has extracted turns (``rehydrate._turn_texts``). A text
  fills the row only when the row holds exactly its start -- what the old
  writer kept -- AND the row's id is that turn's (``dual_write.episode_ids``:
  the chat, the turn number, the question's start). One such text, or the row
  is left as it is: an edited chat, another turn, or a guess is never
  written.
* **The user's word first.** A turn the user forgot, or a chat kept out of
  memory (``forget.refuses_write``), is never filled; the ledger is asked
  inside the write's own transaction.
* **One write.** Compare-and-set on the text read and on the tier: a forget
  or another write that landed meanwhile wins. The row keeps its id, time,
  tenant and tier; the full-text index and the Postgres mirror's outbox
  follow through their triggers; the meaning vector is written again only
  when the text it is made from changed (``episode_text.embed_text``).
* **Said.** Each row is marked once in its metadata (``text_fill``):
  ``filled``, ``whole`` (the chat's text is that long: it was never cut) or
  ``unavailable`` (the chat no longer holds the turn, or holds it edited);
  memory health counts them (:func:`text_fill_counts`).

A swarm result (``ep_`` ids, cut at 8,000) and a summary (cut at 2,000) have
no copy in a chat to fill from; health counts them as left.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from kazma_core.memory.chat_history import STORE_ERRORS
from kazma_core.memory.limits import LEGACY_BRIDGE_CUT, LEGACY_SUMMARY_CUT, LEGACY_TEXT_CUT
from kazma_core.spaces.row_work import install_statements
from kazma_core.spaces.scope import OWNER_SPACE_ID

logger = logging.getLogger(__name__)

__all__ = ["CUT_SQL", "TEXT_FILL_VERSION", "run_text_fill_pass", "text_fill_counts"]

TEXT_FILL_VERSION = 1

#: A turn's memory the old cut may have shortened, not forgotten and not yet
#: looked at by this pass.
CUT_SQL = (
    "substr(id, 1, 2) = 'e_' AND tier != 'forgotten' "
    f"AND (length(user_text) = {LEGACY_TEXT_CUT} OR length(assistant_text) = {LEGACY_TEXT_CUT})"
)
_UNMARKED_SQL = (
    "json_extract(CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{}' END, "
    "'$.text_fill') IS NULL"
)
_PAGE = 50


def _meta(row: Any) -> dict[str, Any]:
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _row_tenant(row: Any) -> str:
    """The space a memory row is in: a row with no tenant is the owner's
    (Spaces decision N19)."""
    return str(row["tenant_id"] or OWNER_SPACE_ID)


def _candidates(row: Any, conversations: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """The whole ``(question, answer)`` pairs of the chat that are this row's
    turn: the row holds exactly their start and its id is theirs."""
    from kazma_core.memory.dual_write import episode_ids
    from kazma_core.memory.rehydrate import _turn_texts

    tenant = _row_tenant(row)
    session, turn = str(row["session_id"] or ""), int(row["turn_number"] or 0)
    stored_q, stored_a = str(row["user_text"] or ""), str(row["assistant_text"] or "")
    out: set[tuple[str, str]] = set()
    for conv in conversations:
        if str(conv.get("tenant_id") or tenant) != tenant:
            continue  # another space's copy of the chat: never read into this one
        for question, answer in _turn_texts(conv.get("messages") or []):
            if question[:LEGACY_TEXT_CUT] != stored_q or answer[:LEGACY_TEXT_CUT] != stored_a:
                continue
            content = (question or answer).strip()
            if str(row["id"]) not in episode_ids(session, turn, content, tenant):
                continue
            out.add((question, answer))
    return out


def _mark(conn: sqlite3.Connection, row: Any, state: str) -> None:
    """Record this pass's verdict on the row, once, merged into its metadata."""
    mark = json.dumps({"version": TEXT_FILL_VERSION, "state": state, "at": round(time.time(), 3)})
    conn.execute(
        "UPDATE episodes SET metadata_json = json_set(CASE WHEN json_valid(metadata_json) "
        "THEN metadata_json ELSE '{}' END, '$.text_fill', json(?)) "
        "WHERE id = ? AND tenant_id = ? AND tier != 'forgotten'",
        (mark, row["id"], row["tenant_id"]),
    )


@contextmanager
def _write(conn: sqlite3.Connection) -> Iterator[None]:
    """One write transaction (``BEGIN IMMEDIATE``) on the pass's autocommit
    connection. The body commits what it means to keep; anything left open --
    an early return, an error -- is rolled back on the way out."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        if conn.in_transaction:
            conn.rollback()


def _fill_one(conn: sqlite3.Connection, row: Any) -> str:
    """Fill one row; returns its verdict (``filled``, ``whole``, ``unavailable``,
    ``kept_out`` or ``changed``)."""
    from kazma_core.memory.chat_history import conversations_for
    from kazma_core.memory.episode_text import embed_text
    from kazma_core.memory.forget import chat_keys, refuses_write

    session = str(row["session_id"] or "")
    try:
        conversations = conversations_for(session, strict=True) if session else []
    except STORE_ERRORS:
        return "waiting"  # the chat store cannot be read now: never a verdict
    found = _candidates(row, conversations)
    if len(found) != 1:
        with _write(conn):
            _mark(conn, row, "unavailable")
            conn.commit()
        return "unavailable"
    question, answer = next(iter(found))
    stored_q, stored_a = str(row["user_text"] or ""), str(row["assistant_text"] or "")
    if question == stored_q and answer == stored_a:
        state = "whole"
    else:
        state = "filled"
    keys = chat_keys(session)  # a chat-store read: before the write lock
    with _write(conn):
        if refuses_write(conn, tenant_id=_row_tenant(row), session_id=session,
                         turn_number=int(row["turn_number"] or 0), user_text=question, keys=keys):
            return "kept_out"  # nothing written: _write rolls back
        if state == "filled":
            cur = conn.execute(
                "UPDATE episodes SET user_text = ?, assistant_text = ? "
                "WHERE id = ? AND tenant_id = ? AND user_text = ? AND assistant_text = ? "
                "AND tier != 'forgotten'",
                (question, answer, row["id"], row["tenant_id"], stored_q, stored_a),
            )
            if cur.rowcount != 1:
                return "changed"  # a forget or another write landed first: it wins
        _mark(conn, row, state)
        conn.commit()
    if state == "filled":
        old_embed = embed_text(stored_q, stored_a, row["summary_text"])
        new_embed = embed_text(question, answer, row["summary_text"])
        if new_embed != old_embed:
            _write_vector(conn, row, question, answer, new_embed)
    return state


def _write_vector(conn: sqlite3.Connection, row: Any, question: str, answer: str, text: str) -> None:
    """The row's vector made again, once, from the text it is now made from:
    computed with no lock held, written only while the row still holds the
    filled text and the vector it had (``vector_write``)."""
    from kazma_core.memory.vector_write import encode_vector, index_remotely, write_local_vector

    vec = encode_vector(text)
    if not vec:
        return  # no embedder now: the vector repair pass re-embeds it
    still = "user_text = ? AND assistant_text = ? AND tier != 'forgotten'"
    wrote = write_local_vector(conn, "episodes", str(row["id"]), vec, still_sql=still,
                               still_params=(question, answer), replacing=row["embedding"], replace=True)
    if wrote:

        def _still_held() -> bool:
            return conn.execute(
                f"SELECT 1 FROM episodes WHERE id = ? AND tenant_id = ? AND {still}",
                (row["id"], row["tenant_id"], question, answer),
            ).fetchone() is not None

        index_remotely(str(row["id"]), vec, tenant_id=_row_tenant(row),
                       meta={"session_id": row["session_id"], "tier": row["tier"]}, still_held=_still_held)


@install_statements("the text fill reads every space's chats that still hold a turn whole")
def run_text_fill_pass(*, time_budget_s: float = 60.0) -> dict[str, int]:
    """One pass over the primary memory database (the 15-minute maintenance
    cadence). Returns what it did to each row it looked at."""
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.paths import primary_memory_db

    db = primary_memory_db()
    report = {"filled": 0, "whole": 0, "unavailable": 0, "kept_out": 0, "changed": 0, "waiting": 0}
    if not db or not os.path.isfile(db):
        return report
    deadline = time.monotonic() + time_budget_s
    conn = sqlite3.connect(db, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        apply_sqlite_pragmas(conn, busy_timeout=15000)
        after = ""
        while time.monotonic() < deadline:
            rows = conn.execute(
                "SELECT id, tenant_id, session_id, turn_number, user_text, assistant_text, summary_text, "
                f"tier, embedding, metadata_json FROM episodes WHERE {CUT_SQL} AND {_UNMARKED_SQL} "
                "AND id > ? ORDER BY id LIMIT ?",
                (after, _PAGE),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                after = str(row["id"])
                if time.monotonic() >= deadline:
                    break
                state = _fill_one(conn, row)
                report[state] = report.get(state, 0) + 1
    except sqlite3.OperationalError as exc:
        if "no such" not in str(exc).lower():
            raise  # locked or busy: the maintenance runner counts and reports it (Memory audit M20)
        logger.debug("[text_fill] memory database not ready", exc_info=True)
    finally:
        conn.close()
    if report["filled"]:
        logger.info("[memory] text fill: %d memories got back the text the old cut left out "
                    "(%d were never cut, %d have no whole copy left, %d are kept out of memory)",
                    report["filled"], report["whole"], report["unavailable"], report["kept_out"])
    return report


def text_fill_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """For memory health: the memories this pass ``filled``, found ``whole``
    or could not fill (``unavailable``), those it has not looked at yet
    (``waiting``), and the cut ones with no chat to fill from
    (``swarm_results``, ``summaries``)."""
    out = {"filled": 0, "whole": 0, "unavailable": 0, "waiting": 0, "swarm_results": 0, "summaries": 0}
    state_sql = (
        "json_extract(CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{}' END, "
        "'$.text_fill.state')"
    )
    for state, n in conn.execute(
        f"SELECT {state_sql}, COUNT(*) FROM episodes WHERE {state_sql} IS NOT NULL GROUP BY 1"
    ):
        if state in out:
            out[state] = int(n)
    out["waiting"] = int(conn.execute(
        f"SELECT COUNT(*) FROM episodes WHERE {CUT_SQL} AND {_UNMARKED_SQL}"
    ).fetchone()[0])
    out["swarm_results"] = int(conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE substr(id, 1, 3) = 'ep_' AND tier != 'forgotten' "
        "AND (length(user_text) = ? OR length(summary_text) = ?)",
        (LEGACY_BRIDGE_CUT, LEGACY_BRIDGE_CUT),
    ).fetchone()[0])
    out["summaries"] = int(conn.execute(
        "SELECT COUNT(*) FROM episodes WHERE substr(id, 1, 2) = 'e_' AND tier != 'forgotten' "
        "AND length(summary_text) = ?",
        (LEGACY_SUMMARY_CUT,),
    ).fetchone()[0])
    return out
