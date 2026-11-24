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
  -- names the copy, and a deleted chat stays deleted
  (``kazma_ui.session_manager``);
* **moving a chat moves the conversation, never memory.** What Kazma
  remembered from it stays where it was said, and from now on it remembers
  what is said in the new space: the copy's row says how many messages came
  with it, and turn reconcile starts after them. Taking a chat out of a
  shared space makes that space forget it (``memory.forget.forget_chat``).
  Personal memory is never touched by a move in either direction;
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
with the stage it reached, and removed when it ends. One that did not end is
settled at the next start and on the maintenance cadence
(``kazma_ui.chat_move.settle_chat_moves``, which the app registers with
:func:`register_settler`): ``copying`` takes the copy away, ``copied``
finishes the delete here. Ids and the title the line keeps, never a word of
the chat.

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

__all__ = [
    "MOVES_KEY",
    "ChatMoveRefused",
    "advance_move",
    "is_chat_app_chat",
    "move_refusal",
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
#: ``target``, ``by``, ``new_session``, ``new_thread``, ``taken_out``,
#: ``kept_out``, ``title``, ``stage`` (``copying``, ``putting_back``,
#: ``copied``), each step of the finish done (``done``), ``at``. Ids, the
#: title the line will keep, never a word of the chat.
MOVES_KEY = "system.chats.moves"

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
