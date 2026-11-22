"""X service ownership: missing identity never selects another tenant in production.

Once Spaces is enforced the X account is a connected account of a space
(Spaces S1-17, part b, N26): the tenant every X key and row is under is the
bound space, and work bound to no space reads no X account at all -- never the
owner's. Background X work (a scheduled post, a notice, the mention poller)
runs in the whole scope of the space whose row it is (:func:`x_work_scope`).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from contextlib import AbstractContextManager

__all__ = ["x_config_key", "x_tenant_id", "x_work_scope"]


def x_tenant_id() -> str:
    """The tenant X keys and rows are under.

    Once Spaces is enforced, the bound space (a tenant bound by older code
    names its space, as ``require_space_id`` reads it), and ``NoSpaceBound``
    with nothing bound -- never the owner's. Otherwise as before: the
    authenticated context, ``default`` only in single-operator mode."""
    from kazma_core.spaces.scope import require_space_id, spaces_enforced
    from kazma_core.tenant_context import get_current_tenant_id
    from kazma_core.tenant_isolation import multi_user_or_production

    if spaces_enforced():
        return require_space_id()
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


def x_work_scope(tenant: str, booker: str | None = None) -> Callable[[], AbstractContextManager[object]]:
    """What background X work for a row of *tenant* runs in: call the answer
    and enter it (``with x_work_scope(tenant, booker)():``).

    While Spaces is not enforced, the tenant alone, as before. Once it is, the
    whole scope: the space and the person the work is for -- *booker*, who
    booked the post, while still a member, else the account the space's own
    work acts for (``identity.work_scope``); ``NoSpaceBound`` for a tenant
    that is no space of the install -- its work does not run; ``FeatureOff``
    when that person no longer has X Studio (Spaces S2-11) -- its work waits,
    nothing failed, and goes on once the feature is on again. Blocking (the
    identity store): async callers resolve it in a thread and enter it on the
    loop."""
    from kazma_core.spaces.scope import NoSpaceBound, bind_scope, spaces_enforced
    from kazma_core.tenant_context import tenant_scope

    if not spaces_enforced():
        return functools.partial(tenant_scope, tenant)
    from kazma_core.spaces.features import FeatureOff, work_refusal
    from kazma_core.spaces.identity import AskerLeft, work_scope

    try:
        found = work_scope(str(tenant or ""), booker or None)
    except AskerLeft as exc:
        # The person who booked it left the space: never posted as anyone
        # else (the S2-12 review, finding 6). As work of no space: refused.
        raise NoSpaceBound(f"X work of {tenant!r}: {exc}") from exc
    if found is None:
        raise NoSpaceBound(f"X work of {tenant!r}: no space of this install holds it")
    refused = work_refusal("x_studio", found[1])
    if refused:
        raise FeatureOff("x_studio", refused)
    return functools.partial(bind_scope, *found)


def kept_in_its_space(tenant: str) -> bool:
    """Whether an X notice of *tenant* -- a booked post's outcome, a health
    notice -- stays off the owner's channels (O1, Spaces S1-17 part b): once
    Spaces is enforced, every space's but the owner's. Until people link
    their own chat apps (stage 3) every ops channel, and every address a
    booking captured, is the owner's. No I/O."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced

    return spaces_enforced() and (str(tenant or "") or OWNER_SPACE_ID) != OWNER_SPACE_ID


def identity_failure_passes(exc: BaseException) -> bool:
    """Whether an identity store that could not say whose X work it is
    (``IdentityUnavailable``) will answer again by itself: the database away
    -- reported to the database watch -- or a locked or busy SQLite settings
    file. The work then waits for a later pass, nothing held or failed; any
    other failure ends it once, never tried again every pass (as cron's
    ``_hold_for_identity``)."""
    import sqlite3

    from kazma_core.db import availability

    cause = exc.__cause__ if exc.__cause__ is not None else exc
    text = str(cause).lower()
    if isinstance(cause, sqlite3.OperationalError) and ("locked" in text or "busy" in text):
        return True
    return availability.note_failure("x", cause)
