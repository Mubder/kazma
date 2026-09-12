"""A cron turn must run as its tenant, or it cannot read its own secrets.

2026-09-12, 09:00 local. Both daily reminder jobs fired and both failed with::

    LLM call failed (HTTP 401): no usable API key for https://api.deepseek.com/v1

and paged the operator twice — once per job, which is also why there were two
identical alerts rather than a duplicate. The same model had answered a chat
turn a few minutes earlier without complaint.

The key was present the whole time. `Vault.retrieve` resolves a secret by
trying the current tenant and then the global (NULL) scope:

    for query_tid in ([tid] if tid else []) + [None]:

With no tenant context there is no first step, so a secret stored under tenant
``'default'`` is invisible. Chat sets the tenant from the request; the cron
scheduler never did, even though `ScheduledJob` has carried `tenant_id` since
the multi-tenant migration. Every tenant-scoped read inside a cron turn had the
same hole — the API key is simply the one that pages you.

Fixed by installing the job's tenant in `_execute`, not by loosening the
vault's fallback: a global→tenant fallback would let any context-less
background task read another tenant's credentials.
"""

from __future__ import annotations

import inspect

from kazma_core.cron import scheduler as sched_mod
from kazma_core.security import vault as vault_mod
from kazma_core.tenant_context import (
    get_current_tenant_id,
    reset_current_tenant_id,
    set_current_tenant_id,
)


# ── the mechanism, proven without a scheduler ───────────────────────────────


def test_a_tenant_scoped_secret_is_invisible_without_a_tenant(tmp_path, monkeypatch):
    """The root cause, reproduced in four lines."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "v.db"))

    token = set_current_tenant_id("default")
    try:
        v.store("provider.key", "sk-real-value")
    finally:
        reset_current_tenant_id(token)

    token = set_current_tenant_id("default")
    try:
        assert v.retrieve("provider.key") == "sk-real-value"
    finally:
        reset_current_tenant_id(token)

    # No tenant context: exactly what a cron turn had.
    assert get_current_tenant_id() is None
    assert v.retrieve("provider.key") is None, (
        "a context-less caller must not see it -- this is the bug, not a wish"
    )


def test_the_fallback_is_tenant_then_global_and_not_the_reverse(tmp_path, monkeypatch):
    """The direction matters. Global-scoped secrets stay readable by anyone;
    tenant-scoped ones must not leak to a caller with no tenant."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "v.db"))

    v.store("global.key", "g", tenant_id=None)
    v.store("scoped.key", "s", tenant_id="default")

    assert v.retrieve("global.key") == "g", "global is visible with no tenant"
    assert v.retrieve("scoped.key") is None, "tenant-scoped is not"

    token = set_current_tenant_id("default")
    try:
        assert v.retrieve("scoped.key") == "s"
        assert v.retrieve("global.key") == "g", "tenant falls back to global"
    finally:
        reset_current_tenant_id(token)


# ── the fix ─────────────────────────────────────────────────────────────────


def test_execute_installs_the_jobs_tenant():
    src = inspect.getsource(sched_mod.CronScheduler._execute)
    assert "set_current_tenant_id(job.tenant_id" in src, (
        "a cron turn runs context-less and cannot read its own tenant's secrets"
    )
    assert "reset_current_tenant_id(tenant_token)" in src, (
        "the tenant must be restored, or it leaks into whatever runs next "
        "on this task"
    )


def test_the_tenant_is_reset_in_a_finally():
    """A job that raises must not leave its tenant installed."""
    src = inspect.getsource(sched_mod.CronScheduler._execute)
    tail = src.split("finally:", 1)
    assert len(tail) == 2, "the execution body is not wrapped in try/finally"
    assert "reset_current_tenant_id(tenant_token)" in tail[1]


def test_the_job_still_carries_a_tenant_to_install():
    """The fix reads `job.tenant_id`. If that default ever disappears, the
    scheduler would install None and the bug returns silently."""
    job = sched_mod.ScheduledJob(
        job_id="j1", timing="1m", prompt="p", platform="telegram", thread_id="t1",
    )
    assert job.tenant_id == "default"
