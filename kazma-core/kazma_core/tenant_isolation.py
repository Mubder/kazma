"""Multi-tenant isolation helpers (SaaS residual hardening).

Threat model notes
------------------
* Single-operator / shared-secret mode: tenant is effectively ``default``.
* Multi-user (``KAZMA_MULTI_USER=1`` or platform users present) **or**
  production: never trust client-supplied ``X-Tenant-ID``; only JWT claims
  or opaque-session principal bind tenant.
* ``require_tenant_id()`` always returns a non-empty string for storage keys.
"""

from __future__ import annotations

import os
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "client_tenant_spoof_allowed",
    "multi_user_or_production",
    "require_opaque_sessions",
    "require_tenant_id",
    "tenant_key",
]


def multi_user_or_production() -> bool:
    if (os.environ.get("KAZMA_PRODUCTION") or "").strip().lower() in (
        "1", "true", "on", "yes",
    ):
        return True
    try:
        from kazma_core.security.platform_rbac import multi_user_enabled

        return bool(multi_user_enabled())
    except Exception as exc:
        # Fail-closed for posture, loud for operators (audit C2b): an RBAC
        # store error previously fell through to single-tenant mode silently,
        # relaxing tenant isolation exactly when the store was broken.
        logger.warning(
            "[tenant_isolation] multi_user_enabled() check failed (%s) — "
            "treating as multi-user (fail-closed)",
            exc,
        )
        return True


def client_tenant_spoof_allowed() -> bool:
    """True only in single-tenant non-prod labs where header spoof is acceptable."""
    return not multi_user_or_production()


def require_opaque_sessions() -> bool:
    """Force opaque web sessions when multi-user is on (no raw secret cookie)."""
    if multi_user_or_production():
        # Allow explicit opt-out only when not multi-user
        try:
            from kazma_core.security.platform_rbac import multi_user_enabled

            if multi_user_enabled():
                return True
        except Exception:
            pass
    from kazma_core.security.web_sessions import use_opaque_sessions

    return use_opaque_sessions()


def require_tenant_id() -> str:
    """Return active tenant id or ``default`` (never None for storage scopes)."""
    try:
        from kazma_core.tenant_context import get_current_tenant_id

        tid = get_current_tenant_id()
        if tid and str(tid).strip():
            return str(tid).strip()
    except Exception:
        pass
    return "default"


def tenant_key(base: str, *, tenant_id: str | None = None) -> str:
    """Prefix a ConfigStore-style key with tenant for isolation."""
    tid = (tenant_id or require_tenant_id()).strip() or "default"
    if tid == "default":
        return base
    return f"tenant.{tid}.{base}"


def principal_tenant_id(principal: dict[str, Any] | None) -> str | None:
    """Extract tenant from an authenticated principal dict if present."""
    if not principal:
        return None
    for k in ("tenant_id", "tenant", "tid"):
        v = principal.get(k)
        if v:
            return str(v)
    return None
