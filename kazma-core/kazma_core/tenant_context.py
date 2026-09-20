"""Tenant context propagation via ContextVar.

Enables thread-safe, async-safe propagation of the active `tenant_id`
from the HTTP gateway/middleware layer down into storage (session stores,
memories, and vector DBs) without passing it explicitly through every
function parameter.

This module owns **the** tenant ContextVar. ``kazma_core.safety.hitl``
re-exports the same object under the same name; it does not define its own.

It used to define its own, with a different default (``"default"`` vs
``None``), kept in step by a pair of mirror functions. That is not an
invariant, it is two writes that happen to agree: the tool worker bound only
the HITL copy, the vault read this one, and a direct
``_current_tenant_id.set`` on either module desynced them with no error. A
desync presents as a stored secret reading back absent — "not configured",
which is exactly what a never-stored key looks like. It shipped three times
(cron 09-12, agent turn + ``kazma doctor`` 09-16, connector-health/backup
09-17) before the variable itself was made single.

``None`` means "no tenant installed", which is what
:func:`kazma_core.security.vault.retrieve_scoped` needs in order to tell an
explicit ``default`` tenant from an absent one. Callers that require a
non-None string read through ``safety.hitl.get_current_tenant_id``, which
applies the floor on read. Do not store the floor here.
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


def set_current_tenant_id(tenant_id: str | None) -> contextvars.Token[str | None]:
    """Set the tenant_id for the current async/thread context.

    Returns:
        A token that should be passed to `reset_current_tenant_id` to restore
        the prior value.
    """
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant_id(token: contextvars.Token[str | None]) -> None:
    """Restore the tenant_id ContextVar to its prior value."""
    _current_tenant_id.reset(token)


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
