"""Phase 1 — authorize_effect library tests (Commitment §4 / §R2.6)."""

from __future__ import annotations

from kazma_core.safety.commitment import authorize_effect
from kazma_core.safety.side_effects import SecurityTier, SemanticTier


def test_read_only_tools_allow_without_enforcement():
    d = authorize_effect("file_read", {"path": "/x"}, context={"source": "ide"})
    assert d.decision == "allow"
    assert d.audit["read_only"] is True
    assert d.audit["semantic_tier"] == "none"


def test_registered_mutator_allows_audit_only_by_default():
    """Phase 1: registered mutators (memory_store, schedule_task) allow with
    an audit record — the semantic gate is Phase 2."""
    d = authorize_effect("memory_store", {"text": "x"}, context={"source": "graph"})
    assert d.decision == "allow"
    assert d.audit["semantic_tier"] == "high"
    assert d.audit["act"] == "store_fact|revise_fact"


def test_unregistered_mutator_allowed_when_enforcement_off():
    """Default (safe): even fail-closed mutators allow, so MCP/custom tools
    that aren't yet in the registry aren't broken."""
    d = authorize_effect("delete_everything", enforce_unknown_mutators=False)
    assert d.decision == "allow"
    assert d.profile.security_tier == SecurityTier.UNSAFE


def test_unregistered_mutator_denied_when_enforcement_on():
    d = authorize_effect("delete_everything", enforce_unknown_mutators=True,
                         context={"source": "registry"})
    assert d.decision == "deny"
    assert "fail-closed" in d.reason
    assert d.audit["registered"] is False


def test_registered_danger_tool_not_denied_even_with_enforcement():
    """Enforcement targets UNREGISTERED mutators only — a registered danger
    tool (shell_exec) is audit-allowed in Phase 1 (its HITL security gate is
    separate; the semantic confirm is Phase 2)."""
    d = authorize_effect("shell_exec", enforce_unknown_mutators=True)
    assert d.decision == "allow"
    assert d.profile.security_tier == SecurityTier.DANGER
    assert d.profile.semantic_tier == SemanticTier.CRITICAL


def test_audit_carries_profile_and_context():
    d = authorize_effect("schedule_task", {"timing": "2d"},
                         context={"source": "graph", "thread_id": "t1"})
    assert d.audit["source"] == "graph"
    assert d.audit["thread_id"] == "t1"
    assert d.audit["effect"] == "schedule"
    assert d.audit["args_keys"] == ["timing"]
