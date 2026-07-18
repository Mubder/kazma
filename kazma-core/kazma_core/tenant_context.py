"""Tenant context propagation via ContextVar.

Enables thread-safe, async-safe propagation of the active `tenant_id`
from the HTTP gateway/middleware layer down into storage (session stores,
memories, and vector DBs) without passing it explicitly through every
function parameter.
"""

from __future__ import annotations

import contextvars

__all__ = ["get_current_tenant_id", "reset_current_tenant_id", "set_current_tenant_id"]

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
