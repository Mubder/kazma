"""The space and opener of a chat thread (Spaces S1-07).

Every chat thread is recorded once: the space it belongs to and the account
that opened it (``thread_scopes.db``). Every place that runs a turn -- the
web, the chat apps, a scheduled fire, a resume after a restart, an
approval's timeout -- runs it inside ``turn_segment_budget``
(``agent/turn_budget.py``), which binds the scope from this record through
:func:`scope_for_turn` and :func:`bound`:

* a thread with no record is recorded with the scope its entry point bound;
* a thread with a record runs in the record's space. An entry point that
  bound another space is refused (:class:`ThreadInAnotherSpace`): a turn
  that paused in one space can only be resumed there;
* an entry point that bound nothing (a watchdog, a resume at start) runs the
  turn as the thread's opener, in the thread's space.

While the install does not enforce Spaces, a thread whose entry point bound
nothing is recorded as the owner's and runs as it did before: the tenant
ContextVar is left as the entry point left it.

The record holds ids only: no title, no text. It is one small row per
thread, kept while its chat exists (:func:`forget_thread` when it is deleted).
**A deleted chat's thread never runs again** (:func:`end_thread`): the mark is
kept in this process, and no turn starts on a marked thread and none is
recorded again -- an approval answered late, a turn being prepared, a resume.
A chat app's chat, named by its sender, begins again with the message that
comes in (:func:`begin_again`).

A chat a person moved from another space (S2-12) says when it was copied
(``copied_at``): what was said before is the space's where it was said, and
this one remembers what is said here (:func:`copied_threads`).

**Everything kept by thread follows the record** (S1-08): the checkpoints,
the Time Travel steps, the plan, the receipts. :func:`thread_refusal` is the one
answer to "may this work touch this thread", asked by the checkpoint saver
and by the routes and tools that take a thread id. It refuses only on an
install that enforces Spaces; elsewhere a thread is reached as it was before.
A thread with no record on such an install is from before Spaces, and those
stay the owner's (decision N19).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from kazma_core.spaces import scope as _scope
from kazma_core.spaces.scope import NoSpaceBound, Scope, valid_account_id, valid_space_id

__all__ = [
    "ThreadEnded",
    "ThreadInAnotherSpace",
    "ThreadRecord",
    "begin_again",
    "bound",
    "copied_threads",
    "end_thread",
    "ensure_store",
    "forget_thread",
    "record_new_thread",
    "record_of",
    "record_thread",
    "release_thread",
    "space_threads",
    "thread_refusal",
    "require_thread",
    "scope_for_turn",
    "thread_ended",
    "thread_scopes_db",
    "new_web_thread_id",
]

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS thread_scopes (
    thread_id  TEXT PRIMARY KEY,
    space_id   TEXT NOT NULL,
    opener     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    copied_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_thread_scopes_space ON thread_scopes(space_id);
"""


class ThreadInAnotherSpace(NoSpaceBound):
    """A turn was asked for on a thread that belongs to another space."""


class ThreadEnded(NoSpaceBound):
    """The thread's chat was deleted: nothing runs on it again."""


class _AskerNotInSpace(NoSpaceBound):
    """A turn was asked for by a person who is no longer a member of its
    space (the S2-12 review, finding 7)."""


@dataclass(frozen=True)
class ThreadRecord:
    """Where a chat thread lives and who opened it."""

    thread_id: str
    space_id: str
    opener: str
    created_at: str
    #: When the chat was copied here from another space (S2-12); None for
    #: every other chat.
    copied_at: float | None = None


def new_web_thread_id(session_id: str) -> str:
    """The thread id a web chat gets when its first message makes it, the
    same for every mouth (the chat's stream, live voice): its session id in
    the owner's space -- one id everywhere, as before Spaces -- and a fresh
    one in any other, since two spaces' browsers may hold the same session
    id and a thread id names one chat for good."""
    import uuid

    from kazma_core.tenant_isolation import require_tenant_id

    return session_id if require_tenant_id() == _scope.OWNER_SPACE_ID else str(uuid.uuid4())


def thread_scopes_db() -> str:
    """The path of the thread record store, in the data folder."""
    from kazma_core.paths import data_dir

    return str(Path(data_dir()) / "thread_scopes.db")


_schema_lock = threading.Lock()
_schema_ready: set[str] = set()


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """A short-lived connection: opened, used and closed by one call."""
    from kazma_core.config_store import apply_sqlite_pragmas

    path = thread_scopes_db()
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        apply_sqlite_pragmas(conn)
        if path not in _schema_ready:
            with _schema_lock:
                if path not in _schema_ready:
                    conn.executescript(_SCHEMA)
                    from kazma_core.db.sqlite_columns import add_missing_columns

                    add_missing_columns(conn, "thread_scopes", (("copied_at", "REAL"),))
                    _schema_ready.add(path)
        yield conn
    finally:
        conn.close()


def ensure_store() -> None:
    """Make the record store's table when it is not there (a moved space
    lands in it: ``spaces.row_stores``)."""
    with _connection() as conn:
        conn.executescript(_SCHEMA)


def _row(row: tuple | None) -> ThreadRecord | None:
    if row is None:
        return None
    return ThreadRecord(thread_id=row[0], space_id=row[1], opener=row[2], created_at=row[3],
                        copied_at=float(row[4]) if row[4] is not None else None)


def record_of(thread_id: str) -> ThreadRecord | None:
    """The record of a thread, or None when it has none."""
    if not thread_id:
        return None
    with _connection() as conn:
        found = _row(
            conn.execute(
                "SELECT thread_id, space_id, opener, created_at, copied_at FROM thread_scopes "
                "WHERE thread_id = ?",
                (str(thread_id),),
            ).fetchone()
        )
    if found is not None:
        # A record never changes: remember its space, so the guards a turn's
        # own code asks afterwards (``thread_refusal``, often on the event
        # loop) answer from memory. ``scope_for_turn`` reads it for every turn.
        _remember(found)
    return found


def record_thread(thread_id: str, space_id: str, opener: str, *,
                  copied_at: float | None = None) -> ThreadRecord:
    """Record a thread once. A thread already recorded keeps its record: the
    first writer wins, and the record that stands is returned. *copied_at*:
    when the chat was copied here from another space (S2-12). A deleted
    chat's thread is not recorded again (:class:`ThreadEnded`)."""
    if not thread_id:
        raise ValueError("a thread record needs a thread id")
    if not valid_space_id(space_id) or not valid_account_id(opener):
        raise ValueError(f"not a space and an account: {space_id!r}, {opener!r}")
    _refuse_ended(thread_id)
    with _connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO thread_scopes (thread_id, space_id, opener, created_at, copied_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(thread_id), space_id, opener, datetime.now(UTC).isoformat(), copied_at),
        )
        stands = _row(
            conn.execute(
                "SELECT thread_id, space_id, opener, created_at, copied_at FROM thread_scopes "
                "WHERE thread_id = ?",
                (str(thread_id),),
            ).fetchone()
        )
    assert stands is not None
    _remember(stands)
    return stands


# ── The thread's space, for everything kept by thread (S1-08) ────────────

#: thread id -> its recorded space. A record never changes once written, so a
#: hit is safe to keep; a thread with no record is asked again every time (it
#: may be recorded a moment later). Bounded; :func:`forget_thread` removes.
_spaces: dict[str, str] = {}
_spaces_lock = threading.Lock()
_SPACES_MAX = 4096


def _remember(record: ThreadRecord) -> None:
    with _spaces_lock:
        if len(_spaces) >= _SPACES_MAX:
            _spaces.clear()
        _spaces[record.thread_id] = record.space_id


def _thread_space(thread_id: str) -> str | None:
    """The space a thread is recorded in, or None when it has no record.
    A recorded thread is answered from memory; the first asking reads the
    store, so async code calls it in a thread."""
    tid = str(thread_id or "")
    if not tid:
        return None
    with _spaces_lock:
        known = _spaces.get(tid)
    if known is not None:
        return known
    record = record_of(tid)  # remembers a record it finds
    return record.space_id if record is not None else None


def known_space(thread_id: str) -> str | None:
    """A recorded thread's space from memory alone: None when the store must be asked."""
    with _spaces_lock:
        return _spaces.get(str(thread_id or ""))


def copied_threads() -> dict[str, float]:
    """Every thread whose chat was copied here from another space (S2-12),
    and when: what was said before that was said elsewhere, so this space's
    memory starts there (turn reconcile, the past-chats search, the scan of
    old sessions). One read; raises when the store cannot be read -- never
    "none was"."""
    if not Path(thread_scopes_db()).exists():
        return {}  # no thread was ever recorded
    with _connection() as conn:
        return {str(r[0]): float(r[1]) for r in conn.execute(
            "SELECT thread_id, copied_at FROM thread_scopes WHERE copied_at IS NOT NULL").fetchall()}


# ── A deleted chat's thread never runs again ─────────────────────────────

#: thread id -> the space its chat was deleted in, newest last; in this
#: process, bounded.
_ended: OrderedDict[str, str] = OrderedDict()
_ENDED_KEPT = 10_000


def end_thread(thread_id: str, space_id: str = "") -> None:
    """Mark a thread whose chat is deleted (in *space_id*): no turn starts on
    it again and nothing records it again. Memory only."""
    tid = str(thread_id or "")
    if not tid:
        return
    with _spaces_lock:
        _ended[tid] = space_id or _ended.get(tid) or _spaces.get(tid) or ""
        _ended.move_to_end(tid)
        while len(_ended) > _ENDED_KEPT:
            _ended.popitem(last=False)


def thread_ended(thread_id: str, space_id: str | None = None) -> bool:
    """Whether the thread's chat was deleted in this process (in *space_id*,
    when one is named). Memory only."""
    with _spaces_lock:
        where = _ended.get(str(thread_id or ""))
    return where is not None and (space_id is None or where == space_id)


def begin_again(thread_id: str) -> None:
    """Take a thread's mark away: a chat app's chat, named by its sender,
    which the message that came in starts again; or a delete that did not
    happen (a move put back)."""
    with _spaces_lock:
        _ended.pop(str(thread_id or ""), None)


def end_space(space_id: str) -> None:
    """The space was erased: the threads this process knows as its own run
    nothing again. Memory only."""
    with _spaces_lock:
        its = [tid for tid, space in _spaces.items() if space == space_id]
    for tid in its:
        end_thread(tid, space_id)


def space_begins_again(space_id: str) -> None:
    """The space was brought in (from its bundle): its threads run. The marks
    of threads ended in it go, and one of a thread it holds whose space was
    not known; what was deleted in another space stays ended. Reads the
    record store."""
    _own, held = space_threads(space_id)
    with _spaces_lock:
        for tid in [t for t, where in _ended.items() if where == space_id or (not where and t in held)]:
            del _ended[tid]


def _refuse_ended(thread_id: str) -> None:
    if thread_ended(thread_id):
        raise ThreadEnded("this chat was deleted, so nothing runs on it")


def forget_thread(thread_id: str) -> bool:
    """Remove a thread's record: its chat was deleted, and it never runs
    again (:func:`end_thread`). True when one was removed. Only a chat's
    delete calls this; a record that goes for another reason is
    :func:`release_thread`'s."""
    if not thread_id:
        return False
    end_thread(str(thread_id))
    return release_thread(thread_id)


def release_thread(thread_id: str) -> bool:
    """Remove a thread's record, and nothing else: no chat was deleted (an
    import taken back, a thread that is no chat's). The id runs again as one
    nobody recorded. True when one was removed."""
    tid = str(thread_id or "")
    if not tid:
        return False
    with _connection() as conn:
        removed = conn.execute("DELETE FROM thread_scopes WHERE thread_id = ?", (tid,)).rowcount
    with _spaces_lock:
        _spaces.pop(tid, None)
    return bool(removed)


def space_threads(space_id: str) -> tuple[bool, list[str]]:
    """The threads of one space, for a statement over a table that holds
    every space's threads: ``(True, ids)`` names the space's threads;
    ``(False, ids)`` names the threads that are NOT its own.

    The owner's space is answered the second way: a thread with no record is
    from before Spaces and is the owner's (N19), so its threads are every
    thread no other space recorded. One read of the record store, whatever
    the number of threads: a listing asks this once, never thread by thread.
    """
    with _connection() as conn:
        if space_id == _scope.OWNER_SPACE_ID:
            rows = conn.execute(
                "SELECT thread_id FROM thread_scopes WHERE space_id <> ?", (space_id,)
            ).fetchall()
            return False, [str(r[0]) for r in rows]
        rows = conn.execute(
            "SELECT thread_id FROM thread_scopes WHERE space_id = ?", (space_id,)
        ).fetchall()
        return True, [str(r[0]) for r in rows]


def thread_home(thread_id: str) -> str:
    """The space a thread belongs to: its record's, and the owner's for a
    thread from before Spaces (N19)."""
    return _thread_space(thread_id) or _scope.OWNER_SPACE_ID


def thread_refusal(thread_id: str, *, space_id: str | None = None) -> str | None:
    """Why this work may not touch *thread_id*, or None when it may.

    Only an install that enforces Spaces refuses. The work's space is
    *space_id*, else the one bound (``require_space_id``); work that bound
    nothing -- a watchdog, a resume at start -- acts for the thread itself
    and is not refused here. Reads the record store on a first asking.
    """
    if not _scope.spaces_enforced() or not thread_id:
        return None
    asked = space_id
    if asked is None:
        bound_scope = _scope.current_scope()
        if bound_scope is not None:
            asked = bound_scope.space_id
        else:
            from kazma_core.tenant_context import get_current_tenant_id

            asked = (get_current_tenant_id() or "").strip() or None
    if asked is None:
        return None
    if thread_home(thread_id) != asked:
        return "this chat belongs to another space"
    return None


def record_new_thread(thread_id: str, *, copied_at: float | None = None) -> ThreadRecord | None:
    """Record a thread this work is about to make (a fork, a moved chat's
    copy with its *copied_at*) in the space, and for the account, it is bound
    to. On an install that does not enforce
    Spaces, work that bound nothing records nothing: the thread is recorded
    on its first turn, as before. Enforced, such work is refused."""
    bound_scope = _scope.current_scope()
    if bound_scope is None:
        if not _scope.spaces_enforced():
            return None
        bound_scope = _scope.require_scope()
    return record_thread(thread_id, bound_scope.space_id, bound_scope.asker, copied_at=copied_at)


def require_thread(thread_id: str, *, space_id: str | None = None) -> None:
    """Raise :class:`ThreadInAnotherSpace` when :func:`thread_refusal` refuses."""
    why = thread_refusal(thread_id, space_id=space_id)
    if why:
        raise ThreadInAnotherSpace(why)


@dataclass(frozen=True)
class TurnScope:
    """What a turn runs as: its scope, and whether the tenant is bound with
    it (an entry point that bound nothing, on an install that does not
    enforce Spaces, keeps its unbound tenant)."""

    scope: Scope
    bind_tenant: bool


def _still_a_member(found: Scope) -> None:
    """Every turn starts for a member of its space (the S2-12 review,
    finding 7): a person who left it -- between their request and the turn,
    or before a resume -- gets none. An identity store that cannot answer
    raises its own error."""
    if not found.asker:
        return
    from kazma_core.spaces import identity

    if not identity.is_member(found.asker, found.space_id):
        raise _AskerNotInSpace("you are no longer in this space, so this chat's turn does not run")


def scope_for_turn(thread_id: str) -> TurnScope:
    """The scope a turn on *thread_id* runs in, recording the thread on its
    first turn. Reads and writes the record store: call it off the loop.

    Raises :class:`ThreadInAnotherSpace` when the entry point bound a space
    the thread does not belong to, :class:`ThreadEnded` for a deleted chat's
    thread, and ``NoSpaceBound`` when nothing is bound for a new thread on an
    install that enforces Spaces.
    """
    if thread_id:
        _refuse_ended(thread_id)
    entry = _scope.current_scope()
    enforced = _scope.spaces_enforced()
    record = record_of(thread_id) if thread_id else None

    if entry is not None:
        if record is None:
            if thread_id:
                record = record_thread(thread_id, entry.space_id, entry.asker)
        if record is not None and record.space_id != entry.space_id:
            _other_space(thread_id, record.space_id, entry.space_id, enforced)
        if enforced:
            _still_a_member(entry)
        return TurnScope(scope=entry, bind_tenant=True)

    # The entry point bound no scope: a watchdog, a resume at start, or an
    # older entry point that binds the tenant alone or nothing.
    if record is not None:
        if enforced:
            found = Scope(record.space_id, record.opener)
            _still_a_member(found)
            return TurnScope(scope=found, bind_tenant=True)
        if _legacy_tenant_bound():
            # An older entry point bound a tenant and no asker: the turn runs
            # in that tenant, as before Spaces.
            legacy = _scope.require_scope()
            if legacy.space_id != record.space_id:
                _other_space(thread_id, record.space_id, legacy.space_id, enforced)
            return TurnScope(scope=legacy, bind_tenant=True)
        return TurnScope(scope=Scope(record.space_id, record.opener), bind_tenant=False)
    # No record and no scope. Enforced: require_scope refuses. Not enforced:
    # the owner's scope (or the bound tenant's), as before Spaces.
    fresh = _scope.require_scope()
    if thread_id:
        record_thread(thread_id, fresh.space_id, fresh.asker)
    return TurnScope(scope=fresh, bind_tenant=_legacy_tenant_bound())


_said_other_space: set[str] = set()


def _other_space(thread_id: str, recorded: str, asked: str, enforced: bool) -> None:
    """A turn was asked for in a space the thread's record does not name.

    Enforced: refused. Not enforced (one operator, or the older multi-user
    mode): it runs where the entry point said, as it did before Spaces, and
    the mismatch is said once per thread so stage 1 can find such callers.
    """
    if enforced:
        raise ThreadInAnotherSpace(
            "this chat belongs to another space: it can only be continued there"
        )
    if thread_id not in _said_other_space:
        if len(_said_other_space) >= 512:
            _said_other_space.clear()
        _said_other_space.add(thread_id)
        logger.warning(
            "[spaces] a turn ran in space %s on a chat recorded in space %s; "
            "an install that enforces Spaces refuses this",
            asked, recorded,
        )


def _legacy_tenant_bound() -> bool:
    from kazma_core.tenant_context import get_current_tenant_id

    return bool((get_current_tenant_id() or "").strip())


@contextmanager
def bound(turn: TurnScope) -> Iterator[Scope]:
    """Run the block as *turn*: its scope, and its tenant when the turn
    binds one."""
    if turn.bind_tenant:
        with _scope.bind_scope(turn.scope.space_id, turn.scope.asker) as scope_:
            yield scope_
        return
    token = _scope.set_scope_only(turn.scope)
    try:
        yield turn.scope
    finally:
        _scope.reset_scope_only(token)
