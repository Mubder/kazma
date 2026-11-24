"""Moving one's own chat between one's own space and a shared one (Spaces
S2-12): the rules, the journal of moves under way, and the line a shared
space keeps. The move itself is ``kazma_ui.chat_move``.

"Private means in my personal space": a person moves their own chat from
their personal space into a shared space they are in ("Share with..."), or
takes it back out ("Take out"). Decided (the plan's S2-12 row, the owner's
rule of 2026-11-24):

* **a move is a fork into the other space and the ordinary delete here**,
  under a new session id and a new thread id. Nothing that named the chat --
  a tab left open, a message being sent, a queued memory write, a saved step
  -- names the copy, a deleted chat stays deleted
  (``kazma_ui.session_manager``) and its thread never runs again
  (``spaces.threads``);
* **moving a chat moves the conversation, never memory.** What Kazma
  remembered from it stays where it was said, and from now on it remembers
  what is said in the new space: the copy's thread says when it was copied
  (``threads.copied_threads``), and turn reconcile, the past-chats search and
  the scan of old sessions take what was asked since. Taking a chat out of a
  shared space makes that space forget it (``memory.forget.forget_chat``).
  Personal memory is never touched by a move in either direction;
* **what Kazma kept by the chat's old ids** (:data:`MOVED_COLUMNS`,
  :func:`remove_what_it_left`) is removed from a shared space the chat is
  taken out of; a person's own reminders and tasks booked in it come with
  them (:func:`follow_jobs`). On a share nothing of that moves: drafts,
  plans, reminders and tasks stay in the person's own space;
* the spaces' rule is the document move's (``spaces.moves.move_rule``):
  Spaces enforced, a member of both spaces, one the person's own personal
  space and the other a shared one. The chat is the person's own: they
  opened it (its thread's record);
* a chat other members wrote in cannot be taken out -- their words are the
  shared space's (:func:`others_wrote`); the refusal offers "New private
  chat" instead;
* taking a chat out leaves the shared space a line -- who took it out, when,
  the title it had -- which is not a chat and which only the move writes
  (:func:`record_taken_out`, :func:`taken_out_chats`).

Every move is journaled before it starts (:data:`MOVES_KEY`, runtime state)
with the stage it reached and what it copied (how many messages, the last
one's time, the checkpoint), and removed when it ends. One that did not end is
settled at the next start and on the maintenance cadence
(``kazma_ui.chat_move.settle_chat_moves``, which the app registers with
:func:`register_settler`), by what the two chats hold then: never by removing
words that are nowhere else. Ids, counts, times and the title the line keeps,
never a word of the chat.

Blocking (settings, SQLite, the identity store): call it off the event loop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from kazma_core.spaces import moves

__all__ = [
    "FOLLOWS",
    "MOVED_BY_THEIR_CODE",
    "MOVED_COLUMNS",
    "MOVES_KEY",
    "STAYS",
    "ChatMoveRefused",
    "advance_move",
    "follow_jobs",
    "is_chat_app_chat",
    "move_refusal",
    "moves_under_way",
    "others_wrote",
    "record_move",
    "record_taken_out",
    "register_settler",
    "remove_what_it_left",
    "settle_move",
    "settle_unfinished",
    "taken_out_chats",
]

logger = logging.getLogger(__name__)

#: The chat moves under way, by attempt: ``thread``, ``session``, ``source``,
#: ``target``, ``by``, ``new_session``, ``new_thread``, ``taken_out``,
#: ``kept_out``, ``title``, ``stage`` (``copying``, ``copied``,
#: ``putting_back``), what was copied (``count``, ``last_ts``, ``checkpoint``) and the copy's own
#: (``new_count``, ``new_last_ts``), each step of the finish done (``done``),
#: ``at``. Ids, counts, times, the title the line will keep, never a word of
#: the chat.
MOVES_KEY = "system.chats.moves"

# ── What keys on the old ids ─────────────────────────────────────────────

#: The tables whose rows name the chat's thread beside its space, and the
#: space's column: a shared space the chat is taken out of keeps none of the
#: chat's rows (:func:`remove_what_it_left`). On a share they stay in the
#: person's own space. A new table naming a thread beside a space goes here,
#: in :data:`FOLLOWS`, in :data:`MOVED_BY_THEIR_CODE` or in :data:`STAYS`
#: (a gate: ``tests/test_chat_moves.py``).
MOVED_COLUMNS: dict[tuple[str, str], str] = {
    # The drafts and the scratchpad the chat's turns kept (agent.artifacts).
    ("agent_artifacts.db", "agent_artifacts"): "tenant_id",
    # The chat's plan (agent.task_ledger).
    ("task_ledgers.db", "task_ledgers"): "tenant_id",
    # The chat's confirmations (safety.commitment). A held lesson names no
    # chat, so it stays where it was learned.
    ("memory_ops.db", "commitments"): "tenant_id",
    # The chat's decided approval gates (the move refuses an open one).
    ("hitl_gates.db", "hitl_gates"): "tenant_id",
}
#: The rows that go with their maker on a take-out, and the space's column
#: (:func:`follow_jobs`).
FOLLOWS: dict[tuple[str, str], str] = {
    # The reminders and tasks the person booked in the chat: theirs.
    ("cron.db", "cron_jobs"): "tenant_id",
}
#: The rows the move or the delete writes itself, with how.
MOVED_BY_THEIR_CODE: dict[tuple[str, str], str] = {
    ("thread_scopes.db", "thread_scopes"):
        "the thread's record: the copy has its own, and the delete removes this one last",
    ("chat_sessions.db", "sessions"):
        "the chat row: the copy's is written last, and this one deleted once it stands (kazma_ui.chat_move)",
    ("chat_sessions_test.db", "sessions"): "the chat store's file in test mode, as above",
    ("chat_sessions_spool.db", "spool"):
        "a session the database refused, kept until its next accepted save: the session manager "
        "discards it with the row it deletes",
    ("chat_sessions_test_*.db", "spool"): "the spool's file in test mode, as above",
}
#: What stays with the space it was made in, with why.
STAYS: dict[tuple[str, str], str] = {
    ("x_scheduled.db", "x_scheduled_posts"):
        "a post booked through the space's own X account: it goes out from that account",
    ("x_publications.db", "x_thread_events"): "a thread of posts on X, not a chat",
    ("sandbox_emails.db", "messages"): "a mail thread of the space's practice mailbox, not a chat",
    ("llm_calls.db", "llm_calls"): "the spend ledger: counts and money, never a word; the space spent it",
    ("thread_scopes.db", "chat_taken_out"): "the line the shared space keeps when a chat is taken out",
    ("sessions.db", "sessions"):
        "a chat app's delivery context of a thread, gone by itself within minutes (the gateway's store)",
    ("memory_state.db", "episodes"):
        "what Kazma remembered from the chat stays where it was said; a shared space a chat is "
        "taken out of forgets it (memory.forget.forget_chat)",
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
    recomputed from each message, so its next message starts it again here.
    It is not moved."""
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
    another move of the same chat is recorded (``move_in_progress``); an
    update of one that ended meanwhile records nothing."""
    from kazma_core.config_store import get_config_store

    def update(current: Any) -> dict[str, Any]:
        held = _held(current)
        entry = held.get(attempt) or {}
        if not entry and "thread" not in fields:
            return held
        if not entry:
            for other in held.values():
                if other.get("thread") == fields.get("thread"):
                    raise ChatMoveRefused("move_in_progress", 409,
                                          "This chat is already being moved. Try again in a moment.")
        entry.update(fields)
        held[attempt] = entry
        return held

    get_config_store().atomic_update(MOVES_KEY, update, category="system")


def advance_move(attempt: str, *, at: str, to: str) -> bool:
    """Move *attempt* from stage *at* to stage *to*, only if it is there:
    the move itself and whatever settles it never both decide. False: not
    there (settled meanwhile, or gone)."""
    from kazma_core.config_store import get_config_store

    advanced = False

    def update(current: Any) -> dict[str, Any]:
        nonlocal advanced
        held = _held(current)
        entry = held.get(attempt)
        advanced = entry is not None and entry.get("stage") == at
        if advanced:
            held[attempt] = {**entry, "stage": to}
        return held

    get_config_store().atomic_update(MOVES_KEY, update, category="system")
    return advanced


def settle_move(attempt: str) -> None:
    """The move *attempt* ended (done, or put back): its entry goes."""
    from kazma_core.config_store import get_config_store

    def update(current: Any) -> dict[str, Any]:
        held = _held(current)
        held.pop(attempt, None)
        return held

    get_config_store().atomic_update(MOVES_KEY, update, category="system")


# ── What a chat leaves in a shared space it is taken out of ──────────────


def _open_store(name: str) -> sqlite3.Connection | None:
    """A connection to the row store *name*, or None when the install has no
    such file yet."""
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.spaces.space_data import store_paths

    path = dict(store_paths()).get(name)
    if path is None:
        return None
    conn = sqlite3.connect(str(path), timeout=5.0)
    apply_sqlite_pragmas(conn)
    return conn


def remove_what_it_left(thread_id: str, *, space_id: str) -> int:
    """Remove from *space_id* every row of :data:`MOVED_COLUMNS` that names
    the chat's old thread: its drafts and scratchpad, its plan, its
    confirmations (and their events), its decided approval gates. A gate
    still open is left to its own ending. Idempotent; returns how many."""
    from kazma_core.safety.hitl_gates import LIVE_STATES

    removed = 0
    for (name, table), column in MOVED_COLUMNS.items():
        conn = _open_store(name)
        if conn is None:
            continue
        with closing(conn):
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                                (table,)).fetchone():
                continue
            where, params = f"{column} = ? AND thread_id = ?", [space_id, thread_id]
            if table == "hitl_gates":
                where += f" AND state NOT IN ({','.join('?' * len(LIVE_STATES))})"
                params += LIVE_STATES
            with conn:
                if table == "commitments":
                    conn.execute("DELETE FROM commitment_events WHERE commitment_id IN "
                                 f"(SELECT commitment_id FROM commitments WHERE {where})", params)
                removed += int(conn.execute(f"DELETE FROM {table} WHERE {where}", params).rowcount or 0)
    return removed


def follow_jobs(thread_id: str, *, space_id: str, by: str, to_space: str, to_thread: str) -> int:
    """The reminders and tasks *by* booked in the chat go with them out of
    the shared space *space_id*: to *to_space*, under the copy's thread. One
    another member booked stays. Idempotent; returns how many."""
    conn = _open_store("cron.db")
    if conn is None:
        return 0
    with closing(conn):
        with conn:
            mine = [r[0] for r in conn.execute(
                "SELECT job_id FROM cron_jobs WHERE tenant_id = ? AND thread_id = ? AND creator = ?",
                (space_id, thread_id, by))]
            for job_id in mine:
                conn.execute("UPDATE cron_jobs SET tenant_id = ?, thread_id = ? WHERE job_id = ?",
                             (to_space, to_thread, job_id))
                conn.execute("UPDATE cron_deliveries SET tenant_id = ? WHERE job_id = ? AND tenant_id = ?",
                             (to_space, job_id, space_id))
                conn.execute("INSERT INTO cron_events (job_id, at, event, actor, source, detail) "
                             "VALUES (?, ?, 'moved', ?, 'chat_move', ?)",
                             (job_id, time.time(), by, json.dumps({"from": space_id, "to": to_space})))
    return len(mine)


# ── A move that did not end: settled at start and on the cadence ─────────

#: What settles the moves under way (``kazma_ui.chat_move``, registered by
#: the app at start): kazma-core may not import the web UI.
_SETTLER: list[Callable[[], None]] = []


def register_settler(settle: Callable[[], None] | None) -> None:
    """Register what settles the moves under way: called by the maintenance
    sweep "chat moves", off the event loop. None takes it away (shutdown)."""
    _SETTLER[:] = [settle] if settle is not None else []


def settle_unfinished() -> None:
    """The maintenance sweep "chat moves": a move its own request could not
    finish is finished, or put back, without waiting for a restart. Nothing
    to do with no move recorded."""
    if not moves_under_way():
        return
    if not _SETTLER:
        logger.info("[spaces] a chat move waits: the app that settles it is not running")
        return
    _SETTLER[0]()


# ── The line a shared space keeps ────────────────────────────────────────


def _connection() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.spaces.threads import thread_scopes_db

    conn = sqlite3.connect(thread_scopes_db(), timeout=5.0, isolation_level=None)
    apply_sqlite_pragmas(conn)
    conn.executescript(_SCHEMA)
    return conn


def record_taken_out(*, space_id: str, thread_id: str, title: str, by: str, since: float = 0.0) -> None:
    """The line the shared space *space_id* keeps for a chat its maker took
    out: written only by the move (``kazma_ui.chat_move``). The newest
    :data:`_KEPT_PER_SPACE` are kept. A line of the chat written since
    *since* (when its move began) is this one, written already."""
    begun = datetime.fromtimestamp(float(since), UTC).isoformat(timespec="seconds") if since else ""
    with closing(_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            written = begun and conn.execute(
                "SELECT 1 FROM chat_taken_out WHERE space_id = ? AND thread_id = ? AND taken_at >= ? LIMIT 1",
                (space_id, thread_id, begun)).fetchone()
            if not written:
                conn.execute(
                    "INSERT INTO chat_taken_out (space_id, thread_id, title, taken_by, taken_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (space_id, thread_id, str(title or "")[:_TITLE_MAX], by,
                     datetime.now(UTC).isoformat(timespec="seconds")))
                conn.execute(
                    "DELETE FROM chat_taken_out WHERE space_id = ? AND rowid NOT IN ("
                    "SELECT rowid FROM chat_taken_out WHERE space_id = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?)",
                    (space_id, space_id, _KEPT_PER_SPACE))
            conn.execute("COMMIT")
            committed = True
        finally:
            if not committed:
                conn.execute("ROLLBACK")


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
