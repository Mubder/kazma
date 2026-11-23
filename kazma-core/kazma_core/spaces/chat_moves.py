"""Moving one's own chat between one's own space and a shared one (Spaces
S2-12): the rules, the journal of moves under way, the daily bounds, the line
a shared space keeps, and the rows that name the chat beside a space.

"Private means in my personal space": a person moves their own chat from
their personal space into a shared space they are in ("Share with..."), or
takes it back out ("Take out"). Decided (the plan's S2-12 row, the memory
session's answer of 2026-11-23):

* the spaces' rule is the document move's (``spaces.moves.move_rule``):
  Spaces enforced, a member of both spaces, one the person's own personal
  space and the other a shared one. The chat is the person's own: they
  opened it (its thread's record);
* a chat other members wrote in cannot be taken out -- their words are the
  shared space's (:func:`others_wrote`); the refusal offers "New private
  chat" instead;
* taking a chat out leaves the shared space a line -- who took it out, when,
  the title it had -- which is not a chat and which only the move writes
  (:func:`record_taken_out`, :func:`taken_out_chats`);
* moves are bounded (the rebuild of a chat's memory is Kazma's own work, about
  4K tokens a turn): at most :data:`MOVES_PER_DAY` a person and one a chat in
  :data:`DAY_S` (:func:`bound_refusal`), beside the route's rate limit;
* a moved chat is in exactly one space after the move, in every store. Its
  thread id stays: what is kept by thread alone follows the thread's record,
  which the move changes in one statement (``threads.move_thread``). What
  carries a space column beside the thread is moved by
  :func:`move_thread_rows` (:data:`MOVED_COLUMNS`); what stays with the
  space it was made in, and why, is :data:`STAYS`; the chat store, the
  checkpoints and memory are moved by their own code (``kazma_ui.chat_move``,
  ``memory.chat_move``).

Every move is journaled before it starts (:data:`MOVES_KEY`, runtime state)
with the stage it reached, and removed when it ends; one a stop cut off is
settled at the next start (``kazma_ui.chat_move.settle_chat_moves``). Ids
only, never a word of the chat.

Blocking (settings, SQLite, the identity store): call it off the event loop.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from kazma_core.spaces import moves
from kazma_core.spaces.scope import OWNER_SPACE_ID

__all__ = [
    "DAY_S",
    "MOVED_BY_THEIR_CODE",
    "MOVED_COLUMNS",
    "MOVES_KEY",
    "MOVES_PER_DAY",
    "STAYS",
    "ChatMoveRefused",
    "bound_refusal",
    "claim_move",
    "is_chat_app_chat",
    "move_refusal",
    "move_thread_rows",
    "moves_under_way",
    "others_wrote",
    "record_move",
    "record_taken_out",
    "release_move",
    "settle_move",
    "taken_out_chats",
]

logger = logging.getLogger(__name__)

#: The chat moves under way, by attempt: ``thread``, ``session``, ``source``,
#: ``target``, ``by``, ``taken_out``, ``title``, ``stage`` (``copying``,
#: ``switched``), ``memory``, ``line``, ``at``. Ids, the title the line will
#: keep, never a word of the chat.
MOVES_KEY = "system.chats.moves"

#: How many chats one person may move in :data:`DAY_S` (the memory session's
#: decision: a chat's memory is rebuilt where it goes).
MOVES_PER_DAY = 3
#: The window the bounds count in, and how often one chat may move.
DAY_S = 86400.0

#: Every table that names a chat's thread beside a space column of its own:
#: the move gives the thread's rows the target's space. ``(store, table) ->
#: space column``.
MOVED_COLUMNS: dict[tuple[str, str], str] = {
    # The drafts and the scratchpad the chat's turns kept (agent.artifacts).
    ("agent_artifacts.db", "agent_artifacts"): "tenant_id",
    # The chat's plan (agent.task_ledger).
    ("task_ledgers.db", "task_ledgers"): "tenant_id",
    # The chat's held lessons and confirmations (safety.commitment).
    ("memory_ops.db", "commitments"): "tenant_id",
    # The chat's decided approval gates (the move refuses a pending one).
    ("hitl_gates.db", "hitl_gates"): "tenant_id",
    # The reminders and tasks the person booked from the chat: theirs.
    ("cron.db", "cron_jobs"): "tenant_id",
    # The chat apps' delivery context of the thread (the gateway's store).
    ("sessions.db", "sessions"): "tenant_id",
}

#: Tables that name a thread beside a space column and stay where they were
#: made, with why. A new one goes here, in :data:`MOVED_COLUMNS` or in
#: :data:`MOVED_BY_THEIR_CODE` (``tests/test_chat_moves.py``).
STAYS: dict[tuple[str, str], str] = {
    ("x_scheduled.db", "x_scheduled_posts"):
        "a post booked through the space's own X account: it goes out from that account",
    ("x_publications.db", "x_thread_events"): "a thread of posts on X, not a chat",
    ("sandbox_emails.db", "messages"): "a mail thread of the space's practice mailbox, not a chat",
    ("llm_calls.db", "llm_calls"): "the spend ledger: counts and money, never a word; the space spent it",
    ("x_replies.db", "x_thread_permissions"): "a conversation on X the reply bot answers",
    ("activity.db", "activity"): "what happened in the space, by whom: the space's own record",
    ("thread_scopes.db", "chat_taken_out"): "the line the shared space keeps when a chat is taken out",
}

#: Tables that name a thread beside a space column and follow the chat by
#: their own code, not by :func:`move_thread_rows`, with how.
MOVED_BY_THEIR_CODE: dict[tuple[str, str], str] = {
    ("thread_scopes.db", "thread_scopes"):
        "the thread's record: the move changes it in one statement (threads.move_thread)",
    ("chat_sessions.db", "sessions"):
        "the chat row: copied into the target, then removed from the source (kazma_ui.chat_move)",
    ("chat_sessions_test.db", "sessions"): "the chat store's file in test mode, moved as above",
    ("chat_sessions_spool.db", "spool"):
        "a session the database refused, kept until its next accepted save: the session manager "
        "writes it with the row it copies and discards it with the row it removes (kazma_ui.chat_move)",
    ("chat_sessions_test_*.db", "spool"): "the spool's file in test mode, moved as above",
    ("memory_state.db", "episodes"):
        "a chat's memories: rebuilt in the space it moved to, retired where it was (memory.chat_move)",
}

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS chat_taken_out (
    space_id  TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    title     TEXT NOT NULL,
    taken_by  TEXT NOT NULL,
    taken_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_taken_out_space ON chat_taken_out(space_id, taken_at);
CREATE TABLE IF NOT EXISTS chat_moves_done (
    thread_id  TEXT NOT NULL,
    moved_by   TEXT NOT NULL,
    from_space TEXT NOT NULL,
    to_space   TEXT NOT NULL,
    moved_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_moves_done_by ON chat_moves_done(moved_by, moved_at);
CREATE INDEX IF NOT EXISTS idx_chat_moves_done_thread ON chat_moves_done(thread_id, moved_at);
"""

_TITLE_MAX = 200
_KEPT_PER_SPACE = 500


class ChatMoveRefused(ValueError):
    """A move that is not the person's to make, or cannot be made now;
    nothing moved."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def move_refusal(*, from_space: str, to_space: str, by: str) -> tuple[ChatMoveRefused | None, bool]:
    """The spaces' rule (``spaces.moves.move_rule``). Returns the refusal or
    None, and whether the move takes the chat out of a shared space."""
    refused, taken_out = moves.move_rule(from_space=from_space, to_space=to_space, by=by)
    if refused is not None:
        return ChatMoveRefused(refused.code, refused.status, str(refused)), False
    return None, taken_out


def is_chat_app_chat(*ids: str) -> bool:
    """Whether a chat is a chat app's (Telegram, Discord, Slack, WhatsApp):
    its thread is named by the gateway (``gw-<platform>-<sender>-...``) and
    recomputed from each message, so moved it would answer in no space and
    lock the chat app out (the chat move review's H6). It is not moved."""
    return any(str(i or "").startswith("gw-") for i in ids)


def others_wrote(messages: list[Mapping[str, Any]], by: str) -> bool:
    """Whether anyone but *by* wrote in the chat: a user message whose author
    is another account, or none (in a shared space every message names its
    author, ``reply_sink.authored``; one that names none is not known to be
    theirs). Kazma's own answers are no one's."""
    for message in messages:
        if str(message.get("role") or "") != "user":
            continue
        if str(message.get("author") or "") != by:
            return True
    return False


# ── The journal of moves under way ───────────────────────────────────────


def _held(current: Any) -> dict[str, dict[str, Any]]:
    return ({str(k): dict(v) for k, v in current.items() if isinstance(v, Mapping)}
            if isinstance(current, Mapping) else {})


def moves_under_way() -> dict[str, dict[str, Any]]:
    """Every move a stop may have cut off, by attempt."""
    from kazma_core.config_store import get_config_store

    return _held(get_config_store().get(MOVES_KEY))


def record_move(attempt: str, fields: dict[str, Any]) -> None:
    """Record (or update) a move under way. A new one is refused while
    another move of the same chat is recorded (``move_in_progress``)."""
    from kazma_core.config_store import get_config_store

    def update(current: Any) -> dict[str, Any]:
        held = _held(current)
        entry = held.get(attempt) or {}
        if not entry:
            for other in held.values():
                if other.get("thread") == fields.get("thread"):
                    raise ChatMoveRefused("move_in_progress", 409,
                                          "This chat is already being moved. Try again in a moment.")
        entry.update(fields)
        held[attempt] = entry
        return held

    get_config_store().atomic_update(MOVES_KEY, update, category="system")


def settle_move(attempt: str) -> None:
    """The move *attempt* ended (done, or put back): its entry goes."""
    from kazma_core.config_store import get_config_store

    def update(current: Any) -> dict[str, Any]:
        held = _held(current)
        held.pop(attempt, None)
        return held

    get_config_store().atomic_update(MOVES_KEY, update, category="system")


# ── This store's two tables ──────────────────────────────────────────────


def _connection() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.spaces.threads import thread_scopes_db

    conn = sqlite3.connect(thread_scopes_db(), timeout=5.0, isolation_level=None)
    apply_sqlite_pragmas(conn)
    conn.executescript(_SCHEMA)
    return conn


def _bound_refusal_in(conn: sqlite3.Connection, thread_id: str, by: str, since: float) -> ChatMoveRefused | None:
    mine = int(conn.execute("SELECT COUNT(*) FROM chat_moves_done WHERE moved_by = ? AND moved_at > ?",
                            (by, since)).fetchone()[0])
    this = conn.execute("SELECT 1 FROM chat_moves_done WHERE thread_id = ? AND moved_at > ? LIMIT 1",
                        (thread_id, since)).fetchone()
    if this is not None:
        return ChatMoveRefused("moved_today", 429, "This chat was moved in the last day. Try again tomorrow.")
    if mine >= MOVES_PER_DAY:
        return ChatMoveRefused("moves_today", 429,
                               f"You have moved {MOVES_PER_DAY} chats in the last day. Try again tomorrow.")
    return None


def bound_refusal(thread_id: str, by: str, *, now: float | None = None) -> ChatMoveRefused | None:
    """Why *by* may not move the chat *thread_id* now, by the daily bounds:
    :data:`MOVES_PER_DAY` moves of theirs in :data:`DAY_S` (``moves_today``),
    or this chat moved in that window (``moved_today``). None when they may.
    What the dialog shows; the move itself claims its place
    (:func:`claim_move`)."""
    since = (time.time() if now is None else float(now)) - DAY_S
    with closing(_connection()) as conn:
        return _bound_refusal_in(conn, thread_id, by, since)


def claim_move(*, thread_id: str, by: str, from_space: str, to_space: str) -> float:
    """Count a move toward the daily bounds, checked and recorded in one
    write transaction (the chat move review's M4: two moves at once both
    passed a check made before either was recorded). Raises the bound's
    :class:`ChatMoveRefused`; returns the claim's time, which
    :func:`release_move` takes back when the move is put back."""
    now = time.time()
    with closing(_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            refused = _bound_refusal_in(conn, thread_id, by, now - DAY_S)
            if refused is not None:
                raise refused
            conn.execute(
                "INSERT INTO chat_moves_done (thread_id, moved_by, from_space, to_space, moved_at) "
                "VALUES (?, ?, ?, ?, ?)", (thread_id, by, from_space, to_space, now))
            conn.execute("DELETE FROM chat_moves_done WHERE moved_at < ?", (now - 2 * DAY_S,))
            conn.execute("COMMIT")
            committed = True
        finally:
            if not committed:
                conn.execute("ROLLBACK")
    return now


def release_move(*, thread_id: str, by: str, at: float) -> None:
    """A claimed move that was put back (the chat stayed where it was): it
    no longer counts toward the bounds."""
    with closing(_connection()) as conn:
        conn.execute("DELETE FROM chat_moves_done WHERE thread_id = ? AND moved_by = ? AND moved_at = ?",
                     (thread_id, by, float(at)))


def record_taken_out(*, space_id: str, thread_id: str, title: str, by: str) -> None:
    """The line the shared space *space_id* keeps for a chat its maker took
    out: written only by the move (``kazma_ui.chat_move``). The newest
    :data:`_KEPT_PER_SPACE` are kept."""
    with closing(_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO chat_taken_out (space_id, thread_id, title, taken_by, taken_at) VALUES (?, ?, ?, ?, ?)",
            (space_id, thread_id, str(title or "")[:_TITLE_MAX], by,
             datetime.now(UTC).isoformat(timespec="seconds")),
        )
        conn.execute(
            "DELETE FROM chat_taken_out WHERE space_id = ? AND rowid NOT IN ("
            "SELECT rowid FROM chat_taken_out WHERE space_id = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?)",
            (space_id, space_id, _KEPT_PER_SPACE),
        )
        conn.execute("COMMIT")


def taken_out_chats(space_id: str, *, limit: int = 50) -> list[dict[str, str]]:
    """The chats taken out of *space_id*, newest first: who, when, the title
    each had. Never a word of the chat."""
    with closing(_connection()) as conn:
        rows = conn.execute(
            "SELECT title, taken_by, taken_at FROM chat_taken_out WHERE space_id = ? "
            "ORDER BY taken_at DESC, rowid DESC LIMIT ?",
            (space_id, max(1, min(int(limit), 200))),
        ).fetchall()
    return [{"title": str(r[0]), "by": str(r[1]), "at": str(r[2])} for r in rows]


# ── The rows that name the chat's thread beside a space ──────────────────


def move_thread_rows(thread_id: str, *, from_space: str, to_space: str) -> dict[str, int]:
    """Give every row of :data:`MOVED_COLUMNS` that names *thread_id* in
    *from_space* the space *to_space*. Idempotent: run again, a row already
    moved is left. A store that is not there holds nothing. Returns the rows
    moved per table."""
    from kazma_core.spaces import space_data

    moved: dict[str, int] = {}
    stores = dict(space_data.store_paths())
    for (store, table), column in MOVED_COLUMNS.items():
        path = stores.get(store)
        if path is None:
            continue
        with closing(sqlite3.connect(str(path), timeout=10.0, isolation_level=None)) as conn:
            has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not has:
                continue
            # The owner's space also holds the rows written before Spaces,
            # which name no space (N19).
            count = conn.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE thread_id = ? AND ("{column}" = ? '  # nosec B608 - the declared table and column
                f'OR (? AND ("{column}" IS NULL OR "{column}" = ?)))',
                (to_space, thread_id, from_space, int(from_space == OWNER_SPACE_ID), ""),
            ).rowcount
        if count:
            moved[f"{store}:{table}"] = count
    return moved
