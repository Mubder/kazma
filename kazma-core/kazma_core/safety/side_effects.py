"""Single source of truth for tool side-effect profiles (Commitment Layer §5).

Replaces the fragmented danger lists — ``CANONICAL_DANGER_TOOLS``,
``TOOL_TIERS``, YAML ``require_approval_for``, swarm ``_EXTENDED_DANGER``,
MCP ``classify_mcp_tool`` patterns — with one registry mapping every tool to a
:class:`ToolEffectProfile`. Both HITL danger-detection and the commitment
gate (Phase 2 ``authorize_effect``) consult this so the policy data plane
cannot drift across paths (plan §5.1).

Design
------
- ``security_tier`` stays consistent with ``CANONICAL_DANGER_TOOLS`` /
  ``TOOL_TIERS`` (structural parity; tested).
- ``semantic_tier`` is the NEW dimension HITL didn't have: how much the
  commitment gate cares about this tool's meaning (not just its risk).
- ``effect`` + ``act`` + ``required_slots`` seed the Phase 2/4 act catalog.
- **Unregistered mutators are classified fail-closed** (plan §5.2): a
  mutator-like name with no profile is treated as ``critical`` / unsafe.
  Classification is always fail-closed; *enforcement* (the actual deny) is
  ``agent.commitment.enforce_unknown_mutators`` (**default ON**). A tool
  that is not in this registry (and not on CANONICAL/TOOL_TIERS) is denied
  on the IDE/swarm ``execute()`` path. Pure reads stay allowed. Add every
  real mutator here — do not flip the flag off to paper over drift.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS, TOOL_TIERS

__all__ = [
    "EffectKind", "SecurityTier", "SemanticTier",
    "ToolEffectProfile", "get_effect_profile", "classify_mcp_tool_effect",
    "is_read_only", "requires_semantic_check", "requires_security_approval",
]


class EffectKind(Enum):
    NONE = "none"
    READ = "read"
    WRITE_MEMORY = "write_memory"
    WRITE_FS = "write_fs"
    EXEC = "exec"
    SCHEDULE = "schedule"
    OUTBOUND = "outbound"
    CONFIG = "config"
    DELEGATE = "delegate"
    IDENTITY = "identity"


class SecurityTier(Enum):
    SAFE = "safe"
    WRITE = "write"
    DANGER = "danger"
    UNSAFE = "unsafe"          # always blocked / fail-closed


class SemanticTier(Enum):
    NONE = "none"              # reads, pure Q&A — gate doesn't care
    LOW = "low"
    HIGH = "high"              # memory/schedule/fs — gate resolves slots
    CRITICAL = "critical"      # exec/outbound/config/identity — always confirm


@dataclass(frozen=True)
class ToolEffectProfile:
    name: str
    effect: EffectKind
    security_tier: SecurityTier
    semantic_tier: SemanticTier
    act: str | None = None             # commitment act (plan §3.5) or None for reads
    required_slots: tuple[str, ...] = field(default_factory=tuple)
    registered: bool = True            # False → inferred fail-closed profile


# ── explicit profiles (effect, semantic_tier, act, required_slots) ──────────
# security_tier is derived from CANONICAL/TOOL_TIERS for parity (see below).
_PROF: dict[str, tuple[EffectKind, SemanticTier, str | None, tuple[str, ...]]] = {
    # schedule / remind (the CoPilot act)
    "schedule_task": (EffectKind.SCHEDULE, SemanticTier.HIGH, "remind",
                      ("event_ref|event_at", "fire_at|lead", "prompt")),
    "cancel_scheduled": (EffectKind.SCHEDULE, SemanticTier.HIGH, "cancel_job",
                         ("job_id",)),
    # Editing a scheduled job mutates what runs autonomously; HITL gates it.
    # act=None → audit-only in the commitment gate (no remind/cancel resolver).
    "edit_scheduled": (EffectKind.SCHEDULE, SemanticTier.HIGH, None,
                       ("job_id",)),
    # memory (graph cleanup + store — omitting these fail-closes merge/delete)
    "memory_store": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH,
                     "store_fact|revise_fact",
                     ("subject", "predicate", "object")),
    "memory_search": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "memory_list_beliefs": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "memory_list_entities": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "memory_invalidate": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH,
                          "store_fact|revise_fact", ("belief_id",)),
    "memory_merge_entities": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH,
                              "store_fact|revise_fact",
                              ("source_id", "target_id")),
    "memory_link_entities": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH,
                             "store_fact|revise_fact",
                             ("subject", "object")),
    "memory_delete_entity": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH,
                             "store_fact|revise_fact", ("entity_id",)),
    "memory_purge_empty_entities": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH,
                                    "store_fact|revise_fact", ()),
    "memory_admin": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH,
                     "store_fact|revise_fact", ()),
    # filesystem
    "file_write": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs",
                   ("path", "op")),
    "file_apply_patch": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs",
                         ("path", "op")),
    "file_append": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs",
                    ("path",)),
    "file_delete": (EffectKind.WRITE_FS, SemanticTier.CRITICAL, "mutate_fs",
                    ("path", "op")),
    "file_read": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "mcp_list_resources": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "mcp_read_resource": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "mcp_list_prompts": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "mcp_get_prompt": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "file_search": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "file_list": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "codebase_search": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "codebase_status": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "send_file": (EffectKind.OUTBOUND, SemanticTier.CRITICAL, "send_outbound",
                  ("path",)),
    # Path grants expand the FS allowlist (HITL danger).
    "request_path_access": (EffectKind.CONFIG, SemanticTier.CRITICAL, "config_change",
                            ("path", "mode")),
    # exec
    "shell_exec": (EffectKind.EXEC, SemanticTier.CRITICAL, "exec", ("command",)),
    "code_exec": (EffectKind.EXEC, SemanticTier.CRITICAL, "exec", ("command",)),
    "python_exec": (EffectKind.EXEC, SemanticTier.CRITICAL, "exec", ("command",)),
    "run_tests": (EffectKind.EXEC, SemanticTier.HIGH, "exec", ()),
    "browser_eval_js": (EffectKind.EXEC, SemanticTier.HIGH, "exec", ()),
    "computer_use": (EffectKind.EXEC, SemanticTier.HIGH, "exec", ("goal",)),
    # config
    "config_save": (EffectKind.CONFIG, SemanticTier.CRITICAL, "config_change", ()),
    "config_read": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "vault_delete": (EffectKind.CONFIG, SemanticTier.CRITICAL, "config_change", ()),
    "vault_retrieve": (EffectKind.READ, SemanticTier.NONE, None, ()),
    # outbound
    "email_send": (EffectKind.OUTBOUND, SemanticTier.CRITICAL, "send_outbound",
                   ("target",)),
    "email_delete": (EffectKind.OUTBOUND, SemanticTier.CRITICAL, "send_outbound", ()),
    "email_categorize": (EffectKind.OUTBOUND, SemanticTier.HIGH, None, ()),
    "email_list": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "email_get": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "email_analyze": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "x_post": (EffectKind.OUTBOUND, SemanticTier.CRITICAL, "send_outbound",
               ("text",)),
    "x_delete_post": (EffectKind.OUTBOUND, SemanticTier.CRITICAL, "send_outbound",
                      ("tweet_id",)),
    "x_status": (EffectKind.READ, SemanticTier.NONE, None, ()),
    # Scheduling a post is an outbound public write (approve once at booking);
    # cancelling it is the matching outbound mutation. Both ALWAYS-HITL.
    "x_schedule_post": (EffectKind.OUTBOUND, SemanticTier.CRITICAL, "send_outbound",
                        ("text",)),
    "x_cancel_scheduled_post": (EffectKind.OUTBOUND, SemanticTier.CRITICAL,
                                "send_outbound", ("post_id",)),
    "x_list_scheduled": (EffectKind.READ, SemanticTier.NONE, None, ()),
    # git / github
    "git_commit": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "git_push_pull": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "git_push": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "git_pull": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "git_merge": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "github_create_pr": (EffectKind.OUTBOUND, SemanticTier.HIGH, "send_outbound", ()),
    "github_merge_pr": (EffectKind.OUTBOUND, SemanticTier.CRITICAL, "send_outbound", ()),
    # install / delegate
    "install_python_packages": (EffectKind.EXEC, SemanticTier.CRITICAL, "exec", ()),
    "install_npm_packages": (EffectKind.EXEC, SemanticTier.CRITICAL, "exec", ()),
    "install_agent_skill": (EffectKind.DELEGATE, SemanticTier.HIGH, "delegate", ()),
    "uninstall_agent_skill": (EffectKind.DELEGATE, SemanticTier.HIGH, "delegate", ()),
    # misc
    "sqlite_query": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "current_datetime": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "send_message": (EffectKind.NONE, SemanticTier.NONE, None, ()),
    # Operator-directed messaging: outbound, but ungated on purpose.
    # send_approval_request IS the HITL card -- putting it behind the
    # send_outbound gate would require an approval in order to ask for one.
    # Both deliver to the operator, never to a third party, so the gate that
    # every other OUTBOUND tool carries would protect nobody here.
    "send_approval_request": (EffectKind.OUTBOUND, SemanticTier.NONE, None, ()),
    "dispatch_notification": (EffectKind.OUTBOUND, SemanticTier.NONE, None, ()),
    "context_info": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "update_scratchpad": (EffectKind.NONE, SemanticTier.NONE, None, ()),
    # Internal task-state bookkeeping (mutates only the ledger DB, never the
    # world) — unregistered, the commitment layer denied it as a fail-closed
    # unregistered mutator (2026-08-27 live: the model's add_finding was
    # rejected mid-turn).
    "task_ledger_update": (EffectKind.NONE, SemanticTier.NONE, None, ()),
    "mcp_test_server": (EffectKind.READ, SemanticTier.NONE, None, ()),
    # knowledge library
    "knowledge_list_libraries": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "knowledge_search": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "knowledge_create_library": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH, None, ()),
    "knowledge_ingest_url": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH, None, ()),
    "knowledge_ingest_site": (EffectKind.WRITE_MEMORY, SemanticTier.HIGH, None, ()),
    # research
    "plan_research_queries": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "critique_synthesis_gaps": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "list_research_papers": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "research_readiness": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "start_deep_research": (EffectKind.DELEGATE, SemanticTier.HIGH, "delegate", ()),
    # swarm / sub-agents
    "spawn_agent": (EffectKind.DELEGATE, SemanticTier.HIGH, "delegate", ()),
    "spawn_agents": (EffectKind.DELEGATE, SemanticTier.HIGH, "delegate", ()),
    "dispatch_swarm": (EffectKind.DELEGATE, SemanticTier.HIGH, "delegate", ()),
    "check_swarm_task": (EffectKind.READ, SemanticTier.NONE, None, ()),
    # native skills commonly loaded
    "lint_code": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "format_code": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "run_unit_tests": (EffectKind.EXEC, SemanticTier.HIGH, "exec", ()),
    "generate_pdf": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "generate_docx": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "generate_xlsx": (EffectKind.WRITE_FS, SemanticTier.HIGH, "mutate_fs", ()),
    "browser_navigate": (EffectKind.NONE, SemanticTier.LOW, None, ()),
    "browser_click": (EffectKind.NONE, SemanticTier.LOW, None, ()),
    "browser_extract_text": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "browser_screenshot": (EffectKind.READ, SemanticTier.NONE, None, ()),
    "browser_fill_form": (EffectKind.NONE, SemanticTier.LOW, None, ()),
}


# ── security-tier derivation (parity with CANONICAL / TOOL_TIERS) ───────────

_TIER_MAP = {"safe": SecurityTier.SAFE, "read": SecurityTier.SAFE,
             "write": SecurityTier.WRITE, "danger": SecurityTier.DANGER}


def _security_tier(name: str) -> SecurityTier:
    """CANONICAL is the authority for danger; TOOL_TIERS for the rest."""
    if name in CANONICAL_DANGER_TOOLS:
        return SecurityTier.DANGER
    tier = TOOL_TIERS.get(name)
    if tier in _TIER_MAP:
        return _TIER_MAP[tier]
    return SecurityTier.UNSAFE


# ── fail-closed inference for unregistered tools (plan §5.2) ───────────────
# Tokenize snake_case identifiers and match WHOLE tokens (not substrings —
# "widget" must not match "get", "barcommit" must not match "commit").

_MUTATOR_TOKENS = frozenset({
    "write", "save", "delete", "remove", "drop", "exec", "run", "spawn",
    "send", "schedule", "cancel", "config", "install", "deploy", "update",
    "create", "merge", "push", "pull", "commit", "set", "put", "post",
    "apply", "grant", "revoke", "reset", "clear", "wipe", "override",
})
_READ_TOKENS = frozenset({
    "read", "list", "get", "search", "recall", "fetch", "view", "show",
    "query", "status", "info", "check", "has", "is", "count", "describe",
    "inspect", "exists", "find", "lookup",
})


def _infer_unknown(name: str) -> ToolEffectProfile:
    """Fail-closed profile for a tool with no explicit entry.

    Mutator token present → ``critical`` / unsafe (deny by default). Pure read
    tokens → safe. No recognizable token (ambiguous) → fail-closed. The point:
    omission is never free-fire (the dormant-HITL failure mode, AGENTS.md §7).
    """
    tokens = set(re.split(r"[^a-zA-Z0-9]+", (name or "").lower())) - {""}
    is_mut = bool(tokens & _MUTATOR_TOKENS)
    is_read = bool(tokens & _READ_TOKENS)
    if is_read and not is_mut:
        return ToolEffectProfile(name, EffectKind.READ, SecurityTier.SAFE,
                                 SemanticTier.NONE, None, (), registered=False)
    # mutator token OR ambiguous → fail closed
    return ToolEffectProfile(name, EffectKind.NONE, SecurityTier.UNSAFE,
                             SemanticTier.CRITICAL, None, (), registered=False)


# ── public API ─────────────────────────────────────────────────────────────

def classify_mcp_tool_effect(tool_name: str, description: str = "") -> ToolEffectProfile:
    """Classify an MCP tool into a :class:`ToolEffectProfile` (plan §5).

    Reuses :func:`kazma_core.mcp.manager.classify_mcp_tool`'s danger/safe
    name-pattern logic but maps to the registry's security + semantic tiers so
    the commitment gate sees MCP tools (they were previously on a separate
    classification path). MCP tools are never in the explicit registry, so they
    are ``registered=False``; the fail-closed invariant still holds (unknown →
    critical). The optional *description* is accepted for future schema-based
    refinement but currently unused.
    """
    try:
        from kazma_core.mcp.manager import classify_mcp_tool

        cls = classify_mcp_tool(tool_name)
    except Exception:
        cls = "unknown"
    name = tool_name or ""
    if cls == "safe":
        return ToolEffectProfile(name, EffectKind.READ, SecurityTier.SAFE,
                                 SemanticTier.NONE, None, (), registered=False)
    # danger → HITL security applies; unknown → fail-closed unsafe. Both are
    # semantically critical so the gate resolves them.
    sec = SecurityTier.DANGER if cls == "danger" else SecurityTier.UNSAFE
    return ToolEffectProfile(name, EffectKind.NONE, sec, SemanticTier.CRITICAL,
                             None, (), registered=False)


def get_effect_profile(tool_name: str) -> ToolEffectProfile:
    """Return the side-effect profile for *tool_name*.

    Explicit profile → used. Known to CANONICAL/TOOL_TIERS but no explicit
    profile → a danger-tier default. MCP tools (``mcp__server__tool``) →
    :func:`classify_mcp_tool_effect`. Otherwise → fail-closed inference (§5.2).
    """
    name = tool_name or ""
    prof = _PROF.get(name)
    if prof is not None:
        effect, sem, act, slots = prof
        return ToolEffectProfile(
            name, effect, _security_tier(name), sem, act, slots, registered=True)
    # known danger tool without an explicit profile → danger default
    if name in CANONICAL_DANGER_TOOLS or name in TOOL_TIERS:
        return ToolEffectProfile(name, EffectKind.NONE, _security_tier(name),
                                 SemanticTier.HIGH, None, (), registered=True)
    # MCP tools → classify via the MCP name-pattern logic (§5)
    if name.startswith("mcp__"):
        return classify_mcp_tool_effect(name)
    return _infer_unknown(name)


def is_read_only(tool_name: str) -> bool:
    """True if the tool has no durable side effect (read / none)."""
    p = get_effect_profile(tool_name)
    return p.effect in (EffectKind.READ, EffectKind.NONE) and p.security_tier in (
        SecurityTier.SAFE, SecurityTier.WRITE)


def requires_semantic_check(tool_name: str, *, enabled: bool = True) -> bool:
    """Whether the commitment gate should resolve this tool (plan §5.2).

    ``semantic_tier >= LOW`` AND the layer is enabled. Reads short-circuit.
    """
    return enabled and get_effect_profile(tool_name).semantic_tier != SemanticTier.NONE


def requires_security_approval(tool_name: str, hitl_enabled: bool) -> bool:
    """Whether the existing HITL security gate should prompt (plan §5.2).

    ``security_tier == DANGER`` AND HITL enabled. Mirrors the existing
    ``requires_approval`` rule but read from the registry.
    """
    return (hitl_enabled
            and get_effect_profile(tool_name).security_tier == SecurityTier.DANGER)
