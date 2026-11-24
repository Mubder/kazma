"""The scope of a piece of work: its space and its asker (Spaces S1-02).

One answer to "whose data may this code touch": the :class:`Scope` bound to
the request or the task that runs it. A scope is two values, bound together:

* ``space_id`` -- the space whose rows and files the work reads and writes.
  It is the ``tenant_id`` of every store, and binding a scope binds the
  tenant ContextVar too (``kazma_core.tenant_context``), so a store that
  reads the tenant sees the same space.
* ``asker`` -- the account whose message, click or booking started the work.

**No default once Spaces is enforced.** Until 2026-11 code with no tenant
bound meant ``"default"``, the owner's data: right for one operator, and the
one way a second person's work could quietly act as the owner. Now:

* :func:`require_scope` returns the bound scope, or -- only while the install
  is not enforcing Spaces (one operator, as before) -- the owner's scope.
  With enforcement on, unbound work raises :class:`NoSpaceBound`.
* Work that belongs to the install (an update, a backup, a health check)
  declares itself with :func:`install_work`. Inside it there is no space:
  asking for one raises, enforced or not.

Enforcement is one switch (``identity.enforcement_enabled``), read from
memory here: nothing on the path of a store call reads settings. It cannot be
turned on until every part of stage 1 keeps spaces apart (plan S1-33).

Gate: ``tests/test_no_space_no_data.py``.
"""

from __future__ import annotations

import contextvars
import logging
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from kazma_core import tenant_context

__all__ = [
    "OWNER_ACCOUNT_ID",
    "OWNER_SPACE_ID",
    "NoSpaceBound",
    "Scope",
    "as_if_enforced",
    "bind_scope",
    "bound_asker",
    "current_scope",
    "install_work",
    "made_space_id",
    "new_space_id",
    "require_scope",
    "require_space_id",
    "reset_scope",
    "reset_scope_only",
    "set_enforced",
    "set_scope",
    "space_named_or_bound",
    "space_of_record",
    "space_of_state",
    "set_scope_only",
    "spaces_enforced",
    "valid_account_id",
    "valid_space_id",
]

logger = logging.getLogger(__name__)

#: The owner's space. Every row an install wrote before Spaces says
#: ``default``, so the owner's data is already in it (plan section 7).
OWNER_SPACE_ID = "default"

#: The owner's account: the person who installed Kazma, its one admin.
OWNER_ACCOUNT_ID = "owner"

_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def valid_space_id(value: object) -> bool:
    """A space id is a short lower-case name: letters, digits, ``_`` and
    ``-``. It becomes a folder name and a key prefix, so nothing else."""
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


_MADE_ID_RE = re.compile(r"s[0-9a-f]{12}")


def new_space_id() -> str:
    """A new space's id, the form every space Kazma makes has."""
    return f"s{secrets.token_hex(6)}"


def made_space_id(value: object) -> bool:
    """Whether *value* is an id as Kazma makes them (:func:`new_space_id`):
    what a space brought in from another install must have. Never a name a
    tenant of before Spaces could have had."""
    return isinstance(value, str) and _MADE_ID_RE.fullmatch(value) is not None


def valid_account_id(value: object) -> bool:
    """An account id has the same shape as a space id."""
    return valid_space_id(value)


class NoSpaceBound(RuntimeError):
    """Code asked for a space and none is bound to this request or task.

    Never caught to fall back to the owner's space: the caller's entry point
    must bind the scope (:func:`bind_scope`), or declare itself install work
    (:func:`install_work`) and read no space's content.
    """


@dataclass(frozen=True)
class Scope:
    """The space a piece of work is in, and the account that asked for it."""

    space_id: str
    asker: str

    def __post_init__(self) -> None:
        if not valid_space_id(self.space_id):
            raise ValueError(f"not a space id: {self.space_id!r}")
        if not valid_account_id(self.asker):
            raise ValueError(f"not an account id: {self.asker!r}")


#: What :func:`set_scope` hands back, for :func:`reset_scope`.
ScopeToken = tuple[contextvars.Token, contextvars.Token]

_scope: contextvars.ContextVar[Scope | None] = contextvars.ContextVar(
    "kazma_scope", default=None
)
_install_work: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kazma_install_work", default=None
)

# ── Enforcement: one switch, kept in memory ─────────────────────────────

_enforced_lock = threading.Lock()
_enforced: bool = False


def spaces_enforced() -> bool:
    """True once this install refuses work with no space bound.

    Read from memory. ``identity.prime_enforcement`` sets it at start and
    whenever the stored switch changes; no settings read happens here.
    """
    return _enforced or _as_if_enforced.get()


#: True only inside :func:`as_if_enforced`.
_as_if_enforced: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "kazma_as_if_enforced", default=False
)


@contextmanager
def as_if_enforced() -> Iterator[None]:
    """This context answers as an install that enforces Spaces does.

    For the switch-on check alone (``spaces.preconditions``, S1-33): it asks
    the code what it would do once enforced, before the switch is stored. It
    can only make a context stricter, never looser, and reaches no other
    task or thread (a new thread starts with it off)."""
    token = _as_if_enforced.set(True)
    try:
        yield
    finally:
        _as_if_enforced.reset(token)


def set_enforced(value: bool) -> None:
    """Record the stored switch in this process. Called by
    ``kazma_core.spaces.identity`` only (and by tests of this module)."""
    global _enforced
    with _enforced_lock:
        _enforced = bool(value)


# ── Binding ─────────────────────────────────────────────────────────────


def set_scope(space_id: str, asker: str) -> ScopeToken:
    """Bind a scope and its tenant for this context; give the pair of tokens
    to :func:`reset_scope`. Middleware uses this; other code prefers
    :func:`bind_scope`."""
    scope = Scope(space_id=space_id, asker=asker)
    return _scope.set(scope), tenant_context.set_current_tenant_id(scope.space_id)


def reset_scope(tokens: ScopeToken) -> None:
    """Undo :func:`set_scope`."""
    scope_token, tenant_token = tokens
    tenant_context.reset_current_tenant_id(tenant_token)
    _scope.reset(scope_token)


def set_scope_only(scope: Scope) -> contextvars.Token:
    """Bind *scope* and leave the tenant ContextVar as it is.

    For ``spaces.threads.bound`` alone: a turn whose entry point bound no
    tenant, on an install that does not enforce Spaces, keeps running with
    none bound (the vault tells "no tenant" from ``default``), while the turn
    still knows its space and its asker.
    """
    return _scope.set(scope)


def reset_scope_only(token: contextvars.Token) -> None:
    """Undo :func:`set_scope_only`."""
    _scope.reset(token)


@contextmanager
def bind_scope(space_id: str, asker: str) -> Iterator[Scope]:
    """Run the block as *asker* inside *space_id*.

    The tenant ContextVar is bound to the same space for the block, so every
    store that reads the tenant reads this space. ``asyncio.to_thread`` and
    ``asyncio.create_task`` copy the context, so the scope follows the work.
    """
    tokens = set_scope(space_id, asker)
    try:
        yield _scope.get()  # type: ignore[misc]
    finally:
        reset_scope(tokens)


@contextmanager
def install_work(reason: str) -> Iterator[None]:
    """Run the block as work of the install: an update, a backup, a sweep
    over every space's index columns. No space is bound inside it, and
    :func:`require_scope` raises: install work reads no space's content.

    *reason* says what the work is; it is in the refusal's text.
    """
    if not (reason or "").strip():
        raise ValueError("install work must say what it is")
    scope_token = _scope.set(None)
    tenant_token = tenant_context.set_current_tenant_id(None)
    work_token = _install_work.set(reason.strip())
    try:
        yield
    finally:
        _install_work.reset(work_token)
        tenant_context.reset_current_tenant_id(tenant_token)
        _scope.reset(scope_token)


def current_scope() -> Scope | None:
    """The bound scope, or None. Never falls back; for code that must tell
    "bound" from "not bound"."""
    return _scope.get()


def space_of_state(state: object) -> str:
    """The space a step of a turn runs in, given the graph's state.

    While the install does not enforce Spaces: the state's own ``tenant_id``,
    else the owner's space, exactly as the graph read it before Spaces (a
    receipt's identity is made from this value, so it must not move). Once
    it does: the bound scope's space -- every graph run sits in
    ``turn_segment_budget``, which binds it from the thread's record -- and,
    for a step run outside one, the state's tenant or a refusal.
    """
    getter = getattr(state, "get", None)
    tenant = str((getter("tenant_id") if getter else "") or "").strip()
    if not spaces_enforced():
        return tenant or OWNER_SPACE_ID
    scope = _scope.get()
    if scope is not None:
        return scope.space_id
    return tenant or require_space_id()


def space_of_record(tenant: object) -> str:
    """The space a stored record belongs to: its own tenant, else the
    owner's space -- a record that names none was written before Spaces, and
    every such record is the owner's (decision N19). For a value read from a
    row, a session or a task; never for work that names no space (that asks
    :func:`require_space_id`). No I/O."""
    value = str(tenant or "").strip()
    return value or OWNER_SPACE_ID


def space_named_or_bound(tenant_id: str | None) -> str:
    """The space *tenant_id* names; with none, the space of the work bound
    now once Spaces is enforced (:class:`NoSpaceBound` with none: never the
    owner's), and the owner's while it is not -- the value such code wrote
    before Spaces, so nothing moves on an install that does not enforce it.

    For a parameter some caller leaves out (Spaces S1-02). Unlike
    :func:`require_space_id` it reads neither the ambient tenant nor
    :func:`install_work` while Spaces is off: either would move rows today
    (an older multi-user install's requests bind a principal's tenant; Kazma's
    own sweeps run as install work). No I/O."""
    value = str(tenant_id or "").strip()
    if value:
        return value
    if not spaces_enforced():
        return OWNER_SPACE_ID
    return require_space_id()


def bound_asker() -> str | None:
    """The account this work is for, when a scope is bound; else None. For
    a record that names who did something (a message's author): it is never
    guessed, so work that bound no scope names nobody."""
    scope = _scope.get()
    return scope.asker if scope is not None else None


# ── Asking ──────────────────────────────────────────────────────────────


def _refuse(what: str) -> NoSpaceBound:
    work = _install_work.get()
    if work:
        return NoSpaceBound(
            f"{what}: this is install work ({work}), which reads no space's content"
        )
    return NoSpaceBound(
        f"{what}: no space is bound to this request or task. Its entry point "
        "must bind one (kazma_core.spaces.scope.bind_scope)."
    )


def require_scope() -> Scope:
    """The scope of this work.

    * A bound scope is returned as it is.
    * Inside :func:`install_work`: :class:`NoSpaceBound`, always.
    * A tenant bound by older code with no scope (``tenant_scope``): while
      Spaces is not enforced, that space with the owner as asker; enforced,
      :class:`NoSpaceBound` -- a space with no asker is half a scope.
    * Nothing bound: while Spaces is not enforced, the owner's scope (one
      operator, as before Spaces); enforced, :class:`NoSpaceBound`.
    """
    scope = _scope.get()
    if scope is not None:
        return scope
    if _install_work.get():
        raise _refuse("a space was asked for")
    tenant = (tenant_context.get_current_tenant_id() or "").strip()
    if spaces_enforced():
        if tenant:
            raise NoSpaceBound(
                f"space {tenant!r} is bound with no asker: the entry point "
                "must bind both (kazma_core.spaces.scope.bind_scope)"
            )
        raise _refuse("a space was asked for")
    if tenant and valid_space_id(tenant):
        return Scope(space_id=tenant, asker=OWNER_ACCOUNT_ID)
    return Scope(space_id=OWNER_SPACE_ID, asker=OWNER_ACCOUNT_ID)


def require_space_id() -> str:
    """The id of the space this work is in: the storage key of every store.

    A tenant bound by older code is that space, enforced or not: the binding
    names the space, and stage 1 gives each such entry point its asker.
    Otherwise as :func:`require_scope`.
    """
    scope = _scope.get()
    if scope is not None:
        return scope.space_id
    if _install_work.get():
        raise _refuse("a space id was asked for")
    tenant = (tenant_context.get_current_tenant_id() or "").strip()
    if tenant:
        return tenant
    return require_scope().space_id
