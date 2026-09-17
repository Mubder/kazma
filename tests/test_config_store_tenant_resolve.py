"""A context-less ConfigStore read must see the operator's own secrets.

The 2026-09-12 cron 401, the 2026-09-16 Z.AI substitution and the 2026-09-17
"X credentials are incomplete" report are the same defect, patched three times
at three call sites: ``ConfigStore._resolve_vault_value`` called
``vault.retrieve(name)`` with no tenant, which sees ONLY global rows, while
Settings writes secrets under tenant ``default``.

Measured on the live install 2026-09-17 — the vault held 67 secrets, 33 global
and **34 scoped to 'default'**, and the same probe resolved 4/4 X credentials
with a tenant installed and 0/4 without. This is not an edge case; it is half
the vault.

The fix belongs at the resolver, but it cannot be the global→tenant fallback
that ``tests/test_cron_tenant_context.py`` refuses to put in ``Vault.retrieve``
— that would let tenant B read tenant ``default``'s credentials. So the
``default`` rung is posture-gated: present where ``default`` IS the operator,
absent the moment the install is multi-user or production.
"""

from __future__ import annotations

import pytest

from kazma_core.security import vault as vault_mod
from kazma_core.tenant_context import (
    reset_current_tenant_id,
    set_current_tenant_id,
)


@pytest.fixture(autouse=True)
def _fresh_posture():
    """The posture is memoized; a stale cache would leak between tests."""
    vault_mod.reset_posture_cache()
    yield
    vault_mod.reset_posture_cache()


@pytest.fixture
def _vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "v.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    return v


def _single_tenant(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production", lambda: False
    )


def _multi_tenant(monkeypatch):
    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production", lambda: True
    )


def _store_as(v, tenant, name, value):
    token = set_current_tenant_id(tenant)
    try:
        v.store(name, value)
    finally:
        reset_current_tenant_id(token)


# ── the bug, and the fix ──────────────────────────────────────────────────

def test_context_less_read_sees_the_operators_secret(_vault, monkeypatch):
    """The whole point. Single-operator install, no tenant installed."""
    _single_tenant(monkeypatch)
    _store_as(_vault, "default", "cfg:connectors.x.api_key", "sk-real")

    assert _vault.retrieve("cfg:connectors.x.api_key") is None, (
        "the vault itself must still not leak it — that contract is unchanged"
    )
    assert vault_mod.retrieve_scoped("cfg:connectors.x.api_key") == "sk-real"


def test_multi_tenant_omits_the_default_rung(_vault, monkeypatch):
    """Tenant B must not read tenant 'default''s credentials."""
    _multi_tenant(monkeypatch)
    _store_as(_vault, "default", "cfg:provider.key", "operator-secret")

    assert vault_mod.retrieve_scoped("cfg:provider.key") is None

    token = set_current_tenant_id("tenant-b")
    try:
        assert vault_mod.retrieve_scoped("cfg:provider.key") is None, (
            "falling back to 'default' here is a cross-tenant read"
        )
    finally:
        reset_current_tenant_id(token)


def test_posture_probe_failure_omits_the_rung(_vault, monkeypatch):
    """Fail closed: when we cannot tell the posture, do not widen the read."""
    def _boom():
        raise RuntimeError("rbac store down")

    _store_as(_vault, "default", "cfg:provider.key", "operator-secret")
    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production", _boom
    )
    assert vault_mod.retrieve_scoped("cfg:provider.key") is None


def test_current_tenant_still_wins(_vault, monkeypatch):
    """The ladder's first rung is the caller's own tenant, not 'default'."""
    _single_tenant(monkeypatch)
    _store_as(_vault, "default", "cfg:k", "default-value")
    _store_as(_vault, "tenant-b", "cfg:k", "b-value")

    token = set_current_tenant_id("tenant-b")
    try:
        assert vault_mod.retrieve_scoped("cfg:k") == "b-value"
    finally:
        reset_current_tenant_id(token)


def test_global_rows_still_readable(_vault, monkeypatch):
    _single_tenant(monkeypatch)
    _store_as(_vault, None, "cfg:global", "g")
    assert vault_mod.retrieve_scoped("cfg:global") == "g"


def test_missing_secret_is_none(_vault, monkeypatch):
    _single_tenant(monkeypatch)
    assert vault_mod.retrieve_scoped("cfg:nope") is None


def test_scoped_returns_raw_value(_vault, monkeypatch):
    """No strip — a caller storing whitespace-significant data is unaffected."""
    _single_tenant(monkeypatch)
    _store_as(_vault, "default", "cfg:padded", "  spaced  ")
    assert vault_mod.retrieve_scoped("cfg:padded") == "  spaced  "
    assert vault_mod.retrieve_with_tenant_ladder("cfg:padded") == "spaced"


# ── the resolver actually uses it ─────────────────────────────────────────

def test_config_store_resolver_is_scoped():
    """A grep-proof against the bare retrieve coming back."""
    import inspect

    from kazma_core.config_store import ConfigStore

    src = inspect.getsource(ConfigStore._resolve_vault_value)
    assert "retrieve_scoped" in src, (
        "the resolver must resolve vault:// pointers through the scoped "
        "ladder; a bare vault.retrieve(name) sees global rows only and every "
        "context-less caller reads Settings-saved secrets as missing"
    )


def test_vault_retrieve_contract_unchanged(_vault, monkeypatch):
    """test_cron_tenant_context.py's invariant, re-asserted from this side."""
    _single_tenant(monkeypatch)
    _vault.store("global.key", "g", tenant_id=None)
    _vault.store("scoped.key", "s", tenant_id="default")

    assert _vault.retrieve("global.key") == "g"
    assert _vault.retrieve("scoped.key") is None


# ── the resolver must not re-enter ConfigStore per read ───────────────────
#
# The first cut of this fix called multi_user_or_production() eagerly, on
# every resolve. That probe reads `platform.users` THROUGH ConfigStore, and
# the resolver is called FROM ConfigStore.get -- so every vaulted config read
# re-entered ConfigStore.get. Measured at nesting depth 2 on the provider-key
# path, which is read constantly.
#
# The CHANGELOG has the precedent: a method called under the ConfigStore lock
# that acquired something else deadlocked the whole application from one swarm
# approval. The lock is an RLock now, but "it happens to be survivable" is not
# a design. These tests pin the shape instead.

def _trace_depth(store_cls):
    """Patch ConfigStore.get to record max nesting. Returns (restore, state)."""
    state = {"max": 0, "cur": 0}
    real = store_cls.get

    def traced(self, key, default=None):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        try:
            return real(self, key, default)
        finally:
            state["cur"] -= 1

    store_cls.get = traced
    return (lambda: setattr(store_cls, "get", real)), state


def test_the_posture_probe_is_not_run_per_read(_vault, monkeypatch):
    """The probe must be amortised, not paid on every resolve.

    The first cut called multi_user_or_production() eagerly on every resolve.
    That probe reads `platform.users` THROUGH ConfigStore while this function
    is called FROM ConfigStore's resolver, so every vaulted read re-entered
    ConfigStore.get -- measured at nesting depth 2 on the provider-key path,
    which is read constantly. The CHANGELOG has the precedent: a method called
    under the ConfigStore lock that acquired something else deadlocked the
    whole application from one swarm approval.

    Counting probe calls tests that invariant directly. An earlier version of
    this test counted ConfigStore.get nesting instead, which made it a test of
    singleton wiring under conftest's isolation fixture rather than of the
    property, and it failed for reasons that had nothing to do with
    re-entrancy.
    """
    probes = {"n": 0}

    def _probe():
        probes["n"] += 1
        return False  # single-tenant

    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production", _probe
    )
    _store_as(_vault, "default", "cfg:hot", "value")

    for _ in range(25):
        assert vault_mod.retrieve_scoped("cfg:hot") == "value"

    assert probes["n"] == 1, (
        f"the posture probe ran {probes['n']}x for 25 reads — it is back on "
        "the hot path and every vaulted read re-enters ConfigStore"
    )


def test_a_hit_on_the_first_rung_never_probes(_vault, monkeypatch):
    """A caller with its own tenant short-circuits before the probe."""
    probes = {"n": 0}

    def _probe():
        probes["n"] += 1
        return False

    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production", _probe
    )
    _store_as(_vault, "tenant-b", "cfg:own", "b-value")

    token = set_current_tenant_id("tenant-b")
    try:
        assert vault_mod.retrieve_scoped("cfg:own") == "b-value"
    finally:
        reset_current_tenant_id(token)
    assert probes["n"] == 0


def test_nested_probe_fails_closed(_vault, monkeypatch):
    """A probe that itself resolves a vaulted key must not recurse forever."""
    _store_as(_vault, "default", "cfg:probe.key", "v")

    calls = {"n": 0}

    def _recursive():
        calls["n"] += 1
        # Simulate the probe reaching back into a scoped resolve.
        vault_mod.retrieve_scoped("cfg:probe.key")
        return False

    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production", _recursive
    )
    vault_mod.retrieve_scoped("cfg:probe.key")
    assert calls["n"] == 1, "the re-entrancy guard did not stop the nested probe"


def test_multi_user_is_sticky(_vault, monkeypatch):
    """Once multi-user, never re-probe back to the permissive answer.

    The unsafe direction of caching a posture is a stale 'single-tenant' after
    an install turns multi-user -- that would leave the `default` rung on,
    which is a cross-tenant read. Multi-user therefore sticks; its own failure
    mode is a secret looking missing, which is the pre-fix behaviour.
    """
    _store_as(_vault, "default", "cfg:k", "operator-secret")

    posture = {"multi": True}
    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production",
        lambda: posture["multi"],
    )
    assert vault_mod.retrieve_scoped("cfg:k") is None

    # Even if the probe would now say single-tenant, the rung stays off.
    posture["multi"] = False
    assert vault_mod.retrieve_scoped("cfg:k") is None


def test_single_tenant_is_re_probed(_vault, monkeypatch):
    """The safe direction is cached only briefly, so turning multi-user on
    takes effect without a restart."""
    _store_as(_vault, "default", "cfg:k", "operator-secret")

    posture = {"multi": False}
    monkeypatch.setattr(
        "kazma_core.tenant_isolation.multi_user_or_production",
        lambda: posture["multi"],
    )
    assert vault_mod.retrieve_scoped("cfg:k") == "operator-secret"

    posture["multi"] = True
    monkeypatch.setattr(vault_mod, "_POSTURE_TTL_S", 0.0)
    assert vault_mod.retrieve_scoped("cfg:k") is None


def test_tenant_scope_beats_a_stale_global_copy(_vault, monkeypatch):
    """Order is not cosmetic: `default` must be tried BEFORE global.

    A first cut of the ladder queried global first (Vault.retrieve already
    falls back tenant -> global, so one call looked like it covered both).
    When a name exists in BOTH scopes that returns the stale global copy of a
    credential the operator has since re-saved through Settings — silently,
    and looking exactly like a wrong key. Caught by
    tests/test_x_publisher.py::test_vault_get_resolves_tenant_scoped_secrets,
    which asserts the call order rather than just the result.
    """
    _single_tenant(monkeypatch)
    _store_as(_vault, None, "cfg:dual_scoped", "GLOBAL-stale")
    _store_as(_vault, "default", "cfg:dual_scoped", "DEFAULT-fresh")

    assert vault_mod.retrieve_scoped("cfg:dual_scoped") == "DEFAULT-fresh"


def test_a_scoped_caller_does_not_get_the_operator_rung(_vault, monkeypatch):
    """`default` is the rung for context-LESS callers only.

    A caller that already resolved its own tenant and missed must not fall
    through to the operator's secrets, even on a single-operator box where
    the posture gate would wave it through.
    """
    _single_tenant(monkeypatch)
    _store_as(_vault, "default", "cfg:only_default", "operator-secret")

    token = set_current_tenant_id("tenant-b")
    try:
        assert vault_mod.retrieve_scoped("cfg:only_default") is None
    finally:
        reset_current_tenant_id(token)
