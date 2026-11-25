"""Whose statements these are: work that knows one row's space (Spaces S4-01,
step 0a).

A request or a turn runs with its scope bound, and its statements are that
space's. Other code knows the space of one row and runs with nothing bound, or
bound to another space: a document job's stages, a crash recovery, a swarm
task's ending, a spool drain. Others ask about every space at once: a fair
claim, a schema check, a ping. Until this module the first lock (the tenant an
argument names) was all they had. The second lock (S4-01) asks the database
which space a statement is for, so each of them says it here, in one of three
forms:

* :func:`work_of` -- real work for a row: a document's stages, a recovery that
  calls the product. It binds the whole scope, the person included.
* :func:`statements_of` -- only statements that name the row's space. A
  variable the database door alone reads: who is acting does not change, and
  nothing is read to enter it.
* :func:`install_statements` -- the install's own statements: about every
  space, or about none. Each use is declared, with its reason, in
  ``tests/test_row_work_gates.py``.

**Nothing decides by it yet**: the database door is step 1 (report mode),
and the tests' stand-in for it is ``tests/_space_door.py``. A step that adds
the door changes what asks :func:`current_door`, and no caller here.

With Spaces off :func:`work_of` binds no scope and no tenant and reads
nothing, so an install runs exactly as before.

Inside :func:`statements_of` or :func:`install_statements` a scope bound later
wins: a sweep that binds a space is in that space, as the design has it.

Never hold one across a ``yield``: in an async generator the variable is in
force in the consumer between rows (M5). Gate: ``tests/test_row_work_gates.py``.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass

from kazma_core.spaces.scope import (
    NoSpaceBound,
    Scope,
    bind_scope,
    current_scope,
    install_work,
    install_work_reason,
    space_of_record,
    spaces_enforced,
    valid_space_id,
)

__all__ = [
    "Door",
    "current_door",
    "install_loops",
    "install_statements",
    "resolve_work_of",
    "statements_of",
    "work_of",
    "work_of_async",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Door:
    """Through which door a database statement goes.

    ``space`` is the space it is for; ``""`` with a ``reason`` is the
    install's, and then the reason says why.
    """

    space: str = ""
    reason: str = ""


@dataclass(frozen=True)
class _Said:
    """What was said, and what was bound then."""

    door: Door
    scope: Scope | None
    work: str


_said: contextvars.ContextVar[_Said | None] = contextvars.ContextVar("kazma_statements_of", default=None)


def current_door() -> Door | None:
    """The door a statement goes through now: what was last said under what
    is bound now, else the bound scope's space, else the install's inside
    ``scope.install_work``; None when nothing says it. No I/O."""
    scope, work = current_scope(), install_work_reason()
    said = _said.get()
    if said is not None and said.scope is scope and said.work == work:
        return said.door
    if scope is not None:
        return Door(space=scope.space_id)
    return Door(reason=work) if work else None


def _say(door: Door) -> contextvars.Token:
    return _said.set(_Said(door, current_scope(), install_work_reason()))


@contextmanager
def statements_of(space: object) -> Iterator[None]:
    """The block's statements are *space*'s: the space a row names, or the
    owner's for a row of before Spaces (``scope.space_of_record``).

    For the database alone. The bound scope, the asker and the tenant stay as
    they are, and nothing is read.
    """
    token = _say(Door(space=space_of_record(space)))
    try:
        yield
    finally:
        _said.reset(token)


@contextmanager
def install_statements(reason: str) -> Iterator[None]:
    """The block's statements are the install's own, and *reason* says why:
    they ask about every space at once, or about none. As a decorator, on a
    plain function only (a coroutine or a generator would run its body after
    the block ended).

    For the database alone, like :func:`statements_of`; ``scope.install_work``
    is the other thing, which unbinds the scope. Only for code no request
    reaches with an id of its own: there the bound space is the lock (H6).
    """
    if not (reason or "").strip():
        raise ValueError("install statements must say what they are")
    token = _say(Door(reason=reason.strip()))
    try:
        yield
    finally:
        _said.reset(token)


def install_loops(reason: str) -> AbstractContextManager[None]:
    """What a loop or a task that serves every space is started in.

    A task keeps the context it was created in, so a loop started inside one
    person's turn would hold that person's space for its whole life, and do
    every other space's work in it. Once Spaces is enforced it starts with no
    space (``scope.install_work``); while it is not, only its statements are
    said to be the install's, and nothing else moves.
    """
    return install_work(reason) if spaces_enforced() else install_statements(reason)


def resolve_work_of(space: object, asker: object = None) -> Callable[[], AbstractContextManager[object]]:
    """What real work for a row of *space* runs in: call the answer and enter
    it. *asker* is the account the row names, who is acting while still a
    member; else the account Kazma's own work for that space acts for
    (``identity.acting_account``): an empty asker, a principal of before
    Spaces ("operator"), a person who left. A row stays in its own space,
    never the owner's.

    ``NoSpaceBound`` for a value that is no space id; ``IdentityUnavailable``
    when the identity store cannot say. Not for work a person booked that
    acts as them later (``identity.work_scope`` refuses that one when they
    left). Blocking once Spaces is enforced (the settings store); off, it
    reads nothing and the work binds nothing.
    """
    where = space_of_record(space)
    if not spaces_enforced():
        return functools.partial(statements_of, where)
    if not valid_space_id(where):
        raise NoSpaceBound(f"a row names {where!r}, which is no space of this install")
    from kazma_core.spaces.identity import acting_account, is_member

    who = str(asker or "").strip()
    if not (who and is_member(who, where)):
        who = acting_account(where)
    return functools.partial(bind_scope, where, who)


@contextmanager
def work_of(space: object, asker: object = None) -> Iterator[None]:
    """Run the block as work for a row of *space* (:func:`resolve_work_of`).
    Blocking once Spaces is enforced: from a coroutine use
    :func:`work_of_async`."""
    with resolve_work_of(space, asker)():
        yield


async def work_of_async(space: object, asker: object = None) -> Callable[[], AbstractContextManager[object]]:
    """:func:`resolve_work_of` from a coroutine, its reading off the event
    loop: ``with (await work_of_async(space, asker))(): ...``. With Spaces off
    it never leaves the loop."""
    if not spaces_enforced():
        return functools.partial(statements_of, space_of_record(space))
    return await asyncio.to_thread(resolve_work_of, space, asker)
