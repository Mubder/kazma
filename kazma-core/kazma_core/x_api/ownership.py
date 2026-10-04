"""X service ownership: missing identity never selects another tenant in production."""

from __future__ import annotations


def x_tenant_id() -> str:
    """Use authenticated context; allow default only in single-operator mode."""
    from kazma_core.tenant_context import get_current_tenant_id
    from kazma_core.tenant_isolation import multi_user_or_production

    tenant = str(get_current_tenant_id() or "").strip()
    if tenant:
        return tenant
    if multi_user_or_production():
        raise PermissionError("X requires an authenticated tenant context.")
    return "default"


def x_config_key(key: str) -> str:
    """Scope X settings and credentials without importing another tenant's defaults."""
    tenant = x_tenant_id()
    return key if tenant == "default" else f"tenant.{tenant}.{key}"
