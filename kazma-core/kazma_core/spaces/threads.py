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
thread, kept while its chat exists.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from kazma_core.spaces import scope as _scope
from kazma_core.spaces.scope import NoSpaceBound, Scope, valid_account_id, valid_space_id

__all__ = [
    "ThreadInAnotherSpace",
    "ThreadRecord",
    "bound",
    "record_of",
    "record_thread",
    "scope_for_turn",
    "thread_scopes_db",
]

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS thread_scopes (
    thread_id  TEXT PRIMARY KEY,
    space_id   TEXT NOT NULL,
    opener     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_thread_scopes_space ON thread_scopes(space_id);
"""


class ThreadInAnotherSpace(NoSpaceBound):
    """A turn was asked for on a thread that belongs to another space."""


@dataclass(frozen=True)
class ThreadRecord:
    """Where a chat thread lives and who opened it."""

    thread_id: str
    space_id: str
    opener: str
    created_at: str


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
                    _schema_ready.add(path)
        yield conn
    finally:
        conn.close()


def _row(row: tuple | None) -> ThreadRecord | None:
    if row is None:
        return None
    return ThreadRecord(thread_id=row[0], space_id=row[1], opener=row[2], created_at=row[3])


def record_of(thread_id: str) -> ThreadRecord | None:
    """The record of a thread, or None when it has none."""
    if not thread_id:
        return None
    with _connection() as conn:
        return _row(
            conn.execute(
                "SELECT thread_id, space_id, opener, created_at FROM thread_scopes "
                "WHERE thread_id = ?",
                (str(thread_id),),
            ).fetchone()
        )


def record_thread(thread_id: str, space_id: str, opener: str) -> ThreadRecord:
    """Record a thread once. A thread already recorded keeps its record: the
    first writer wins, and the record that stands is returned."""
    if not thread_id:
        raise ValueError("a thread record needs a thread id")
    if not valid_space_id(space_id) or not valid_account_id(opener):
        raise ValueError(f"not a space and an account: {space_id!r}, {opener!r}")
    with _connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO thread_scopes (thread_id, space_id, opener, created_at) "
            "VALUES (?, ?, ?, ?)",
            (str(thread_id), space_id, opener, datetime.now(UTC).isoformat()),
        )
        stands = _row(
            conn.execute(
                "SELECT thread_id, space_id, opener, created_at FROM thread_scopes "
                "WHERE thread_id = ?",
                (str(thread_id),),
            ).fetchone()
        )
    assert stands is not None
    return stands


@dataclass(frozen=True)
class TurnScope:
    """What a turn runs as: its scope, and whether the tenant is bound with
    it (an entry point that bound nothing, on an install that does not
    enforce Spaces, keeps its unbound tenant)."""

    scope: Scope
    bind_tenant: bool


def scope_for_turn(thread_id: str) -> TurnScope:
    """The scope a turn on *thread_id* runs in, recording the thread on its
    first turn. Reads and writes the record store: call it off the loop.

    Raises :class:`ThreadInAnotherSpace` when the entry point bound a space
    the thread does not belong to, and ``NoSpaceBound`` when nothing is bound
    for a new thread on an install that enforces Spaces.
    """
    entry = _scope.current_scope()
    enforced = _scope.spaces_enforced()
    record = record_of(thread_id) if thread_id else None

    if entry is not None:
        if record is None:
            if thread_id:
                record = record_thread(thread_id, entry.space_id, entry.asker)
        if record is not None and record.space_id != entry.space_id:
            _other_space(thread_id, record.space_id, entry.space_id, enforced)
        return TurnScope(scope=entry, bind_tenant=True)

    # The entry point bound no scope: a watchdog, a resume at start, or an
    # older entry point that binds the tenant alone or nothing.
    if record is not None:
        if enforced:
            return TurnScope(scope=Scope(record.space_id, record.opener), bind_tenant=True)
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
