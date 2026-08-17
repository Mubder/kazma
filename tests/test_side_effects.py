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


_MEMORY_GRAPH_TOOLS = (
    "memory_store",
    "memory_search",
    "memory_list_beliefs",
    "memory_list_entities",
    "memory_invalidate",
    "memory_merge_entities",
    "memory_link_entities",
    "memory_delete_entity",
    "memory_purge_empty_entities",
    "memory_admin",
)


def test_memory_graph_tools_are_registered():
    """Cleanup tools must not infer as unregistered mutators (fail-closed)."""
    missing = [t for t in _MEMORY_GRAPH_TOOLS if not get_effect_profile(t).registered]
    assert missing == [], f"memory tools missing from _PROF: {missing}"


def test_memory_merge_not_denied_when_enforcement_on():
    from kazma_core.safety.commitment import authorize_effect

    d = authorize_effect(
        "memory_merge_entities",
        {"source_id": "mubder_kazma", "target_id": "kazma"},
        enforce_unknown_mutators=True,
        context={"source": "registry"},
    )
    assert d.decision != "deny", d.reason
    assert d.profile.registered is True
    assert d.profile.effect == EffectKind.WRITE_MEMORY


def test_memory_link_and_delete_not_unregistered_deny():
    from kazma_core.safety.commitment import authorize_effect

    for name, args in (
        ("memory_link_entities", {"subject": "user", "object": "kazma", "predicate": "has_project"}),
        ("memory_delete_entity", {"entity_id": "true"}),
        ("memory_invalidate", {"belief_id": "b1"}),
        ("memory_purge_empty_entities", {"confirm": True}),
        ("memory_admin", {"action": "list_entities"}),
    ):
        d = authorize_effect(name, args, enforce_unknown_mutators=True)
        assert d.decision != "deny", f"{name}: {d.reason}"
        assert d.profile.registered is True, name


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


# ── MCP classification into the registry (Phase 5 partial, §5) ─────────────

from kazma_core.safety.side_effects import classify_mcp_tool_effect  # noqa: E402


def test_mcp_write_tool_classified_danger():
    p = classify_mcp_tool_effect("mcp__fs__write_file")
    assert p.security_tier == SecurityTier.DANGER
    assert p.semantic_tier == SemanticTier.CRITICAL
    assert not p.registered


def test_mcp_read_tool_classified_safe():
    p = classify_mcp_tool_effect("mcp__db__list_tables")
    assert p.security_tier == SecurityTier.SAFE
    assert p.semantic_tier == SemanticTier.NONE
    assert is_read_only("mcp__db__list_tables")


def test_mcp_unknown_tool_fails_closed():
    p = classify_mcp_tool_effect("mcp__svc__frobnicate")
    assert p.security_tier == SecurityTier.UNSAFE
    assert p.semantic_tier == SemanticTier.CRITICAL


def test_mcp_sensitive_read_forced_danger():
    """'get_password' has a safe verb but a sensitive-read keyword → danger,
    not safe (the secret-exfil guard, audit H6)."""
    p = classify_mcp_tool_effect("mcp__vault__get_password")
    assert p.security_tier == SecurityTier.DANGER


def test_get_effect_profile_routes_mcp_names():
    """The gate calls get_effect_profile; mcp__ names must route to MCP
    classification so the gate sees MCP tools' tiers."""
    assert get_effect_profile("mcp__fs__write_file").security_tier == SecurityTier.DANGER
    assert requires_security_approval("mcp__fs__write_file", hitl_enabled=True)
    assert get_effect_profile("mcp__db__list_tables").security_tier == SecurityTier.SAFE
