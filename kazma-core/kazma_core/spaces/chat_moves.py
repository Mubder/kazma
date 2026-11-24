"""Moving one's own chat between one's own space and a shared one (Spaces
S2-12): the rules, the journal of moves under way, what of a chat's state
moves, the line a shared space keeps, and the rows that name the chat beside
a space.

"Private means in my personal space": a person moves their own chat from
their personal space into a shared space they are in ("Share with..."), or
takes it back out ("Take out"). Decided (the plan's S2-12 row, the owner's
rule of 2026-11-24):

* **moving a chat moves the conversation, never memory.** What Kazma
  remembered from it stays where it was said, and from now on it remembers
  what is said in the new space (``memory.forget.keep_moved_turns_out``: the
  turns the chat already holds are kept out of the target's memory). Taking
  a chat out of a shared space makes that space forget it
  (``memory.forget.forget_chat``). Personal memory is never touched by a
  move in either direction;
* so what the chat's state holds of memory does not move with it
  (:func:`moved_state`: the notes its turns recalled, "About me", what the
  memory tools answered);
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
* a moved chat is in exactly one space after the move, in every store. Its
  thread id stays: what is kept by thread alone follows the thread's record,
  which the move changes in one statement (``threads.move_thread``). What
  carries a space column beside the thread is moved by
  :func:`move_thread_rows` (:data:`MOVED_COLUMNS`); what stays with the
  space it was made in, and why, is :data:`STAYS`; the chat store and the
  checkpoints are moved by their own code (``kazma_ui.chat_move``).

Every move is journaled before it starts (:data:`MOVES_KEY`, runtime state)
with the stage it reached, and removed when it ends; one that did not end is
settled at the next start and on the maintenance cadence
(``kazma_ui.chat_move.settle_chat_moves``, which the app registers with
:func:`register_settler`). Ids only, never a word of the chat.

Blocking (settings, SQLite, the identity store): call it off the event loop.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

from kazma_core.spaces import moves
from kazma_core.spaces.scope import OWNER_SPACE_ID

__all__ = [
    "MEMORY_LEFT",
    "MOVED_BY_THEIR_CODE",
    "MOVED_COLUMNS",
    "MOVES_KEY",
    "STAYS",
    "ChatMoveRefused",
    "is_chat_app_chat",
    "is_memory_tool",
    "move_refusal",
    "move_thread_rows",
    "moved_state",
    "moves_under_way",
    "others_wrote",
    "record_move",
    "record_taken_out",
    "register_settler",
    "settle_move",
    "settle_unfinished",
    "taken_out_chats",
]

logger = logging.getLogger(__name__)

#: The chat moves under way, by attempt: ``thread``, ``session``, ``source``,
#: ``target``, ``by``, ``taken_out``, ``title``, ``stage`` (``copying``,
#: ``switched``), each step of the finish done (``done``), ``at``. Ids, the
#: title the line will keep, never a word of the chat.
MOVES_KEY = "system.chats.moves"

#: Every table that names a chat's thread beside a space column of its own:
#: the move gives the thread's rows the target's space. ``(store, table) ->
#: space column``.
MOVED_COLUMNS: dict[tuple[str, str], str] = {
    # The drafts and the scratchpad the chat's turns kept (agent.artifacts).
    ("agent_artifacts.db", "agent_artifacts"): "tenant_id",
    # The chat's plan (agent.task_ledger).
    ("task_ledgers.db", "task_ledgers"): "tenant_id",
    # The chat's held confirmations (safety.commitment). A held lesson names
    # no chat, so it stays where it was learned.
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
    ("memory_state.db", "episodes"):
        "what Kazma remembered from the chat stays where it was said; a shared space a chat is "
        "taken out of forgets it (memory.forget.forget_chat)",
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


# ── What of a chat's state moves with it ─────────────────────────────────

#: What a memory tool's answer reads as once its chat moved.
MEMORY_LEFT = "[Left where it was said: what memory answered here stayed in the space this chat moved from.]"

#: State that is about the space a turn ran in, or the turn itself: the last
#: turn's memory hand-off and Time Travel step. Never moved.
_LEFT_CHANNELS = ("_post_turn_memory", "memory_explain", "snapshot_id", "snapshot_iteration", "workspace_root")


def is_memory_tool(name: Any) -> bool:
    """Whether the tool *name* answers from memory (the ``memory_*`` tools,
    ``agent.tool_builtins.memory``)."""
    return str(name or "").startswith("memory_")


def moved_state(values: Mapping[str, Any], *, to_space: str, workspace_root: str = "") -> dict[str, Any]:
    """A chat's checkpointed state as it moves into *to_space*: the
    conversation, without what the space it leaves remembered -- the turns'
    notes (``kazma_core.turn_notes``: what was recalled, the past chats, the
    Knowledge, the lessons, the environment), the identity notes ("About me"
    among them; a turn adds its own again), and what the memory tools
    answered (:data:`MEMORY_LEFT`). *workspace_root* is the target's."""
    from kazma_core.turn_notes import IDENTITY_OPENINGS, opens_with, strip_turn_notes

    out = {k: v for k, v in values.items() if k not in _LEFT_CHANNELS}
    if isinstance(values.get("messages"), list):
        kept = [m for m in strip_turn_notes(values["messages"]) if not opens_with(m, *IDENTITY_OPENINGS)]
        out["messages"] = _without_memory_answers(kept)
    # What the last turn's tools answered, kept for that turn alone.
    for channel, empty in (("tool_results", {}), ("tool_calls_done", []), ("tool_calls_pending", []),
                           ("active_attachments", [])):
        if channel in values:
            out[channel] = empty
    if "tenant_id" in values:
        out["tenant_id"] = to_space
    if workspace_root and "workspace_root" in values:
        out["workspace_root"] = workspace_root
    return out


def _without_memory_answers(messages: list[Any]) -> list[Any]:
    """*messages* with every memory tool's answer replaced (the call and its
    answer both stay: a provider refuses one without the other)."""
    named: dict[str, str] = {}
    out: list[Any] = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(message)
            continue
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                named[str(call.get("id") or "")] = str(fn.get("name") or call.get("name") or "")
        if message.get("role") == "tool" and is_memory_tool(
            message.get("name") or named.get(str(message.get("tool_call_id") or ""))
        ):
            message = {**message, "content": MEMORY_LEFT}
        out.append(message)
    return out


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
