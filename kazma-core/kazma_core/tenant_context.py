"""Tenant context propagation via ContextVar.

Enables thread-safe, async-safe propagation of the active `tenant_id`
from the HTTP gateway/middleware layer down into storage (session stores,
memories, and vector DBs) without passing it explicitly through every
function parameter.

``kazma_core.safety.hitl`` used to keep a *second* ContextVar with the same
exported names and a different default (``"default"`` vs ``None``). The
tool worker bound only the HITL copy; vault reads the one in this module.
Those two drifting is how Settings-saved keys look “not configured” on
swarm / connector-health / backup (audit 2026-09-17). ``set`` / ``reset``
here always mirror onto the HITL var (HITL cannot be ``None``, so a
cleared vault scope becomes ``"default"`` there). HITL's setter mirrors
back. Direct ``_current_tenant_id.set`` on either module still diverges
— don't.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

__all__ = [
    "get_current_tenant_id",
    "reset_current_tenant_id",
    "set_current_tenant_id",
    "tenant_scope",
]

_current_tenant_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "kazma_tenant_id",
    default=None,
)


def _mirror_hitl(tenant_id: str | None) -> None:
    """Keep the HITL tenant var in lockstep. Never go through hitl.set (recurse)."""
    try:
        from kazma_core.safety import hitl

        hitl._current_tenant_id.set(tenant_id if tenant_id else "default")
    except Exception:
        pass


def set_current_tenant_id(tenant_id: str | None) -> contextvars.Token[str | None]:
    """Set the tenant_id for the current async/thread context.

    Returns:
        A token that should be passed to `reset_current_tenant_id` to restore
        the prior value.
    """
    token = _current_tenant_id.set(tenant_id)
    _mirror_hitl(tenant_id)
    return token


def reset_current_tenant_id(token: contextvars.Token[str | None]) -> None:
    """Restore the tenant_id ContextVar to its prior value."""
    _current_tenant_id.reset(token)
    _mirror_hitl(_current_tenant_id.get())


def get_current_tenant_id() -> str | None:
    """Return the active tenant_id for the current context.

    Returns:
        The tenant_id string, or None if no tenant context is active.
    """
    return _current_tenant_id.get()


@contextmanager
def tenant_scope(tenant_id: str | None) -> Iterator[None]:
    """Install *tenant_id* for the duration of the ``with`` block."""
    token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(token)
