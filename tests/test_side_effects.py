"""Phase 1 — side-effect registry parity + fail-closed tests (Commitment §5).

Asserts the registry is the single source of truth and that it stays
consistent with ``CANONICAL_DANGER_TOOLS`` / ``TOOL_TIERS`` (the drift problem
§5.1 calls out), plus the unregistered-tool fail-closed invariant (§5.2).
"""

from __future__ import annotations

from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS, TOOL_TIERS
from kazma_core.safety.side_effects import (
    EffectKind,
    SecurityTier,
    SemanticTier,
    get_effect_profile,
    is_read_only,
    requires_security_approval,
    requires_semantic_check,
)


# ── parity with the existing danger SoT (§5.1) ─────────────────────────────

def test_every_canonical_danger_tool_is_danger_in_registry():
    """No security tool may be silently downgraded by the registry."""
    bad = [t for t in CANONICAL_DANGER_TOOLS
           if get_effect_profile(t).security_tier != SecurityTier.DANGER]
    assert bad == [], f"canonical tools not DANGER in registry: {bad}"


def test_registry_covers_all_canonical_tools():
    """Every canonical danger tool has an explicit (not inferred) profile."""
    missing = [t for t in CANONICAL_DANGER_TOOLS
               if not get_effect_profile(t).registered]
    assert missing == [], f"canonical tools without explicit profile: {missing}"


def test_tool_tiers_danger_matches_registry():
    """TOOL_TIERS 'danger' tools are DANGER in the registry (no drift)."""
    bad = [t for t, tier in TOOL_TIERS.items() if tier == "danger"
           and get_effect_profile(t).security_tier != SecurityTier.DANGER]
    assert bad == [], f"TOOL_TIERS-danger not DANGER in registry: {bad}"


# ── core act profiles (§3.5 catalog seed) ──────────────────────────────────

def test_memory_store_is_gated_store_fact_act():
    p = get_effect_profile("memory_store")
    assert p.effect == EffectKind.WRITE_MEMORY
    assert p.semantic_tier == SemanticTier.HIGH
    assert p.act == "store_fact|revise_fact"
    assert requires_semantic_check("memory_store")


def test_schedule_task_is_gated_remind_act():
    p = get_effect_profile("schedule_task")
    assert p.effect == EffectKind.SCHEDULE
    assert p.semantic_tier == SemanticTier.HIGH
    assert p.act == "remind"


def test_exec_and_outbound_are_critical():
    for t in ("shell_exec", "code_exec", "email_send", "config_save"):
        assert get_effect_profile(t).semantic_tier == SemanticTier.CRITICAL, t


def test_reads_are_not_semantically_gated():
    for t in ("file_read", "memory_search", "current_datetime", "email_list"):
        assert not requires_semantic_check(t), t
        assert is_read_only(t), t


# ── fail-closed for unregistered tools (§5.2) ──────────────────────────────

def test_unknown_mutator_fails_closed():
    """A mutator-like name with no profile must NOT be free-fire."""
    for name in ("delete_everything", "run_arbitrary", "send_broadcast",
                 "config_override", "drop_table"):
        p = get_effect_profile(name)
        assert not p.registered, name
        assert p.security_tier == SecurityTier.UNSAFE, name
        assert p.semantic_tier == SemanticTier.CRITICAL, name


def test_unknown_read_is_allowed():
    for name in ("list_items", "get_status", "search_docs"):
        p = get_effect_profile(name)
        assert not p.registered
        assert p.security_tier == SecurityTier.SAFE
        assert is_read_only(name)


def test_unknown_ambiguous_fails_closed():
    """Names with no read/mutator signal default to fail-closed (conservative)."""
    for name in ("frobnicate", "widget", "xyz"):
        assert get_effect_profile(name).security_tier == SecurityTier.UNSAFE, name


# ── security-approval mirror (§5.2 runtime rule) ───────────────────────────

def test_requires_security_approval_mirrors_canonical():
    for t in CANONICAL_DANGER_TOOLS:
        assert requires_security_approval(t, hitl_enabled=True), t
    # reads never require security approval
    assert not requires_security_approval("file_read", hitl_enabled=True)


def test_security_approval_respects_hitl_disabled():
    assert not requires_security_approval("shell_exec", hitl_enabled=False)
