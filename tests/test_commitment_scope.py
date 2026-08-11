"""Phase 5 — swarm scope-token (Commitment §3.11 privilege-escalation guard).

A dispatched worker inherits its orchestrator's scope; a mutator outside that
scope is denied. The main agent (no scope) is never restricted. Enforcement is
gated by agent.commitment.swarm_scope_enforce (default OFF; live via
KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE).
"""

from __future__ import annotations

import pytest

from kazma_core.safety.commitment import authorize_effect
from kazma_core.safety.commitment.scope import (
    ScopeToken, current_scope, is_act_within_scope, swarm_scope,
)
from kazma_core.safety.side_effects import SemanticTier

ENFORCE = {"KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE": "1"}


# ── unit: the scope predicate ──────────────────────────────────────────────

def test_main_agent_scope_none_is_unrestricted():
    ok, _ = is_act_within_scope("config_change", SemanticTier.CRITICAL, None)
    assert ok is True


def test_allowed_acts_excludes_act():
    scope = ScopeToken(allowed_acts=frozenset({"remind", "store_fact"}))
    ok, why = is_act_within_scope("config_change", SemanticTier.CRITICAL, scope)
    assert ok is False and "config_change" in why
    ok2, _ = is_act_within_scope("remind", SemanticTier.HIGH, scope)
    assert ok2 is True


def test_denied_acts_wins():
    scope = ScopeToken(denied_acts=frozenset({"exec"}))
    ok, why = is_act_within_scope("exec", SemanticTier.CRITICAL, scope)
    assert ok is False and "denied" in why


def test_semantic_tier_ceiling():
    scope = ScopeToken(max_semantic_tier=SemanticTier.HIGH)
    ok_hi, _ = is_act_within_scope("mutate_fs", SemanticTier.HIGH, scope)
    assert ok_hi is True
    ok_crit, why = is_act_within_scope("config_change", SemanticTier.CRITICAL, scope)
    assert ok_crit is False and "ceiling" in why


def test_token_metadata_roundtrip():
    t = ScopeToken(allowed_acts=frozenset({"remind"}), max_semantic_tier=SemanticTier.HIGH,
                   parent_commitment_id="cmt_1", thread_id="t1")
    meta = t.to_metadata()
    assert ScopeToken.from_metadata(meta) == t
    assert ScopeToken.from_metadata(None) is None
    assert ScopeToken.from_metadata({}) is None


# ── integration: authorize_effect under a bound worker scope ───────────────

@pytest.mark.anyio
async def test_worker_critical_act_denied_when_ceiling_high(monkeypatch):
    """A worker (HIGH ceiling) calling a CRITICAL-tier act → denied when
    enforcement is on. shell_exec is CRITICAL in the registry."""
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    token = ScopeToken(max_semantic_tier=SemanticTier.HIGH, thread_id="t1")
    async with swarm_scope(token):
        d = authorize_effect("shell_exec", {"command": "rm -rf /"})
    assert d.decision == "deny"
    assert "ceiling" in d.reason


@pytest.mark.anyio
async def test_worker_high_act_allowed_under_high_ceiling(monkeypatch):
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    token = ScopeToken(max_semantic_tier=SemanticTier.HIGH, thread_id="t1")
    async with swarm_scope(token):
        # file_write is HIGH; allowed under a HIGH ceiling (audit-only, no resolver)
        d = authorize_effect("file_write", {"path": "/x"})
    assert d.decision == "allow"


@pytest.mark.anyio
async def test_enforcement_off_lets_critical_through(monkeypatch):
    """Flag OFF (default) → even a CRITICAL act under a HIGH ceiling is allowed
    (the mechanism is inert until an operator enables it)."""
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "0")
    token = ScopeToken(max_semantic_tier=SemanticTier.HIGH)
    async with swarm_scope(token):
        d = authorize_effect("shell_exec", {"command": "x"})
    assert d.decision == "allow"


@pytest.mark.anyio
async def test_main_agent_unrestricted_even_with_flag_on(monkeypatch):
    """No scope bound (main agent) → never restricted, even with flag on."""
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    assert current_scope() is None
    d = authorize_effect("shell_exec", {"command": "x"})
    assert d.decision == "allow"


@pytest.mark.anyio
async def test_swarm_scope_contextvar_binds_and_resets():
    token = ScopeToken(thread_id="t1")
    assert current_scope() is None
    async with swarm_scope(token):
        assert current_scope() is token
    assert current_scope() is None  # reset after exit


# ── default_worker_scope (Phase 5 activation, §3.11) ───────────────────────

def test_default_worker_scope_off(monkeypatch):
    """Flag OFF (default) → no scope assigned → workers unrestricted."""
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "0")
    from kazma_core.safety.commitment.scope import default_worker_scope
    assert default_worker_scope("ws1") is None


def test_default_worker_scope_on(monkeypatch):
    """Flag ON → default HIGH ceiling + deny soul/identity/config."""
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    from kazma_core.safety.commitment.scope import default_worker_scope
    s = default_worker_scope("ws1")
    assert s is not None
    assert s.max_semantic_tier == SemanticTier.HIGH
    assert "soul_delta" in s.denied_acts
    assert "identity" in s.denied_acts
    assert "config_change" in s.denied_acts
    assert s.workspace_id == "ws1"


@pytest.mark.anyio
async def test_default_scope_actually_denies_critical(monkeypatch):
    """End-to-end: the default scope (bound via swarm_scope) denies a CRITICAL
    act (shell_exec) and allows a HIGH act (file_write)."""
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    from kazma_core.safety.commitment.scope import default_worker_scope, swarm_scope

    token = default_worker_scope()
    async with swarm_scope(token):
        d_crit = authorize_effect("shell_exec", {"command": "x"})
        d_hi = authorize_effect("file_write", {"path": "/x"})
    assert d_crit.decision == "deny"
    assert d_hi.decision == "allow"
