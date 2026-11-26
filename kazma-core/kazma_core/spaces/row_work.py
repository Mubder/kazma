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

* :func:`work_of` -- real work Kazma does for a row: a document's stages, a
  recovery that calls the product. It binds the whole scope: the person the
  row names while they are in the space, else the space's own account.
* :func:`resolve_act_of` -- a person's own act in another space of theirs (a
  move into it). It binds that person or refuses, never anyone else.
* :func:`statements_of` -- only statements that name the row's space. A
  variable the database door alone reads: who is acting does not change, and
  nothing is read to enter it.
* :func:`row_statements` -- a row Kazma keeps for itself (a mirror's copy, a
  vector) is written in its own space where Kazma does the work itself, and
  in the bound (or declared) space where a request or a turn does; a row of
  another space there is said as ``cross``.
* :func:`install_statements` -- the install's own statements: about every
  space, or about none. Each use is declared, with its reason, in
  ``tests/test_row_work_gates.py``.

**Nothing decides by it yet.** Step 1 (report mode) asks :meth:`Door.now` at
every checkout of Kazma's own database and records the answer
(``db.space_doors``), with nothing refused; the tests' stand-in that asks at
a store's own methods is ``tests/_space_door.py``. A step that arms the lock
changes what the answer decides, and no caller here.

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
from dataclasses import dataclass, field
from typing import Any

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
    "SpaceErasing",
    "door_hides",
    "install_loops",
    "install_statements",
    "resolve_act_of",
    "resolve_work_of",
    "row_statements",
    "statements_of",
    "work_of",
    "work_of_async",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Door:
    """Through which door a database statement goes.

    ``space`` is the space it is for; ``""`` with a ``reason`` is the
    install's, and then the reason says why. ``how`` says what made it the
    door: ``scope`` (the bound scope), ``row`` (:func:`statements_of`),
    ``statements`` (:func:`install_statements`), ``work``
    (``scope.install_work``), or ``cross``: a space's door kept for a row of
    another space (:func:`row_statements`; once armed the row is hidden from
    it, so the report counts it as a finding). Two doors to the same space
    are equal however they were said.
    """

    space: str = ""
    reason: str = ""
    how: str = field(default="", compare=False)

    @staticmethod
    def now() -> Door | None:
        """The door a statement goes through now: what was last said under
        what is bound now, else the bound scope's space, else the install's
        inside ``scope.install_work``; None when nothing says it. No I/O.

        The database's door asks it at every checkout (``db.space_doors``:
        report mode, S4-01 step 1)."""
        scope, work = current_scope(), install_work_reason()
        said = _said.get()
        if said is not None and said.scope is scope and said.work == work:
            return said.door
        if scope is not None:
            return Door(space=scope.space_id, how="scope")
        return Door(reason=work, how="work") if work else None


@dataclass(frozen=True)
class _Said:
    """What was said, and what was bound then."""

    door: Door
    scope: Scope | None
    work: str


_said: contextvars.ContextVar[_Said | None] = contextvars.ContextVar("kazma_statements_of", default=None)


def _say(door: Door) -> contextvars.Token:
    return _said.set(_Said(door, current_scope(), install_work_reason()))


@contextmanager
def statements_of(space: object) -> Iterator[None]:
    """The block's statements are *space*'s: the space a row names, or the
    owner's for a row of before Spaces (``scope.space_of_record``).

    For the database alone. The bound scope, the asker and the tenant stay as
    they are, and nothing is read.
    """
    token = _say(Door(space=space_of_record(space), how="row"))
    try:
        yield
    finally:
        _said.reset(token)


@contextmanager
def row_statements(space: object) -> Iterator[None]:
    """The block's statements write or remove a row Kazma keeps for itself (a
    memory's copy in the Postgres mirror, its vector) of *space*: they are
    *space*'s where Kazma does the work itself -- nothing is bound, or the
    install's door is said around it (a sweep, the forget convergence).

    Where a space's door is said already -- the bound scope (a request, a
    turn) or a declared :func:`statements_of` -- that door stays: a person's
    own rows are written in their space, and a write into another space is
    said where it starts (declared), never made by the store that writes it
    (H6, review L3). A space's door that is not the row's is said as
    ``cross`` (the same space, so nothing moves): once the lock is armed the
    row is hidden from it, and step 1's report counts it as a finding of its
    own (0b review M1). For the database alone, like :func:`statements_of`.
    """
    door = Door.now()
    if door is None or not door.space:
        with statements_of(space):
            yield
        return
    if door.space == space_of_record(space) or door.how == "cross":
        yield
        return
    token = _say(Door(space=door.space, how="cross"))
    try:
        yield
    finally:
        _said.reset(token)


def door_hides(space: object) -> bool:
    """Whether the statements now go through a space's door that is not
    *space*'s own (``cross``: a request bound to another space). Once the lock
    is armed such a door cannot see the row, so its "nothing there" says
    nothing about the row (0b review M1). No I/O."""
    door = Door.now()
    return door is not None and bool(door.space) and door.space != space_of_record(space)


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
    token = _say(Door(reason=reason.strip(), how="statements"))
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


class SpaceErasing(NoSpaceBound):
    """A row's space is being erased: nothing binds it (S1-27). Its rows go
    with it; work for one waits until they have."""


def _open_space(space: str) -> dict[str, Any]:
    """The registry, once *space* is one of the install's and not being
    erased. Blocking (the settings store)."""
    from kazma_core.spaces.identity import read_registry

    registry = read_registry()
    found = (registry.get("spaces") or {}).get(space) if valid_space_id(space) else None
    if found is None:
        raise NoSpaceBound(f"a row names {space!r}, which is no space of this install")
    if found.get("erasing_at"):
        raise SpaceErasing(f"the space {space!r} is being erased")
    return registry


def _is_in(registry: dict[str, Any], account: str, space: str) -> bool:
    return bool(account) and any(m.get("account") == account and m.get("space") == space
                                 for m in registry.get("memberships") or ())


def resolve_act_of(space: object, person: object) -> Callable[[], AbstractContextManager[object]]:
    """What a person's own act in *space* runs in, when the work bound now is
    another's of their spaces (a move into it): call the answer and enter it.
    Always as *person*. ``identity.AskerLeft`` when they are not in it, or it
    is being erased: a rule asked before a wait may no longer hold, and an act
    of theirs is never anyone else's. Blocking once Spaces is enforced; off,
    it reads nothing and the work binds nothing."""
    where = space_of_record(space)
    if not spaces_enforced():
        return functools.partial(statements_of, where)
    from kazma_core.spaces.identity import AskerLeft

    who = str(person or "").strip()
    try:
        if _is_in(_open_space(where), who, where):
            return functools.partial(bind_scope, where, who)
    except NoSpaceBound as exc:
        raise AskerLeft(str(exc)) from exc
    raise AskerLeft("the person is no longer in the space this would be done in")


def resolve_work_of(space: object, asker: object = None) -> Callable[[], AbstractContextManager[object]]:
    """What real work for a row of *space* runs in: call the answer and enter
    it. *asker* is the account the row names, who is acting while still a
    member; else the account Kazma's own work for that space acts for
    (``identity.acting_account``): an empty asker, a principal of before
    Spaces ("operator"), a person who left. A row stays in its own space,
    never the owner's.

    ``NoSpaceBound`` for a row of no space of this install, ``SpaceErasing``
    (one) while its space is being erased; ``IdentityUnavailable`` when the
    identity store cannot say. Only for work of Kazma's own: never for what a
    person does themself (:func:`resolve_act_of`), nor for work they booked
    that acts as them later (``identity.work_scope``). Blocking once Spaces is enforced (the settings store); off, it
    reads nothing and the work binds nothing.
    """
    where = space_of_record(space)
    if not spaces_enforced():
        return functools.partial(statements_of, where)
    from kazma_core.spaces.identity import acting_account

    who = str(asker or "").strip()
    if not _is_in(_open_space(where), who, where):
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
