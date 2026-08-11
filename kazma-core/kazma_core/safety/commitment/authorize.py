"""authorize_effect — the single policy gate (Commitment Layer §4 / §R2.6).

Phase 1 scope (this module): **audit-only**. Every mutator passing through any
choke point (graph ``tool_worker_node``, IDE/swarm ``LocalToolRegistry.execute``,
post-turn extract) is classified via the side-effect registry and the decision
is logged. The only enforcement in Phase 1 is a narrow, opt-in deny for
fail-closed unregistered mutators (``enforce_unknown_mutators``) — broad
enforcement waits for Phase 2 so legitimately-registered MCP / custom tools
are not blocked before their classification is wired into the registry.

Phase 2 replaces the body of :func:`authorize_effect` with the real commitment
decision logic (resolve slots → detect conflicts → allow / clarify / confirm /
deny) on top of this same entry point — so wiring the choke points now (Phase 1)
means Phase 2 only changes the decision, not the insertion points.

The memory half of the incident is already blocked at the data layer
(``mutate_belief`` source-trust gate); this module is the policy spine that
will carry the schedule / fs / outbound decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from kazma_core.safety.side_effects import (
    SecurityTier,
    ToolEffectProfile,
    get_effect_profile,
    is_read_only,
)

logger = logging.getLogger(__name__)

__all__ = ["EffectDecision", "authorize_effect"]


@dataclass
class EffectDecision:
    """Outcome of authorizing one tool effect.

    Phase 1: ``decision`` ∈ {"allow", "deny"}. Phase 2 adds "clarify" /
    "confirm" (with a pending ``commitment_id``) once the commitment store and
    the unified HITL bus are wired.
    """
    decision: str                       # "allow" | "deny"  (Phase 2: + clarify | confirm)
    reason: str
    profile: ToolEffectProfile
    audit: dict[str, Any] = field(default_factory=dict)
    commitment_id: str | None = None    # Phase 2


def authorize_effect(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    enforce_unknown_mutators: bool = False,
    context: dict[str, Any] | None = None,
) -> EffectDecision:
    """Authorize one tool effect (Phase 1: audit-only + narrow opt-in deny).

    Args:
        tool_name: the tool about to execute.
        args: its arguments (for the audit digest; Phase 2 reads slots).
        enforce_unknown_mutators: when True, deny fail-closed unregistered
            mutators (``SecurityTier.UNSAFE``). Default False so legitimately
            registered MCP/custom tools are not blocked before their
            classification feeds the registry. Phase 2 derives this from live
            config (``agent.commitment.enabled``) once MCP classification is in.
        context: optional caller context (thread_id, source path, etc.) for audit.

    Returns:
        An :class:`EffectDecision`. Callers execute the tool only on "allow".
    """
    profile = get_effect_profile(tool_name)
    ctx = context or {}
    audit = {
        "tool": tool_name,
        "effect": profile.effect.value,
        "security_tier": profile.security_tier.value,
        "semantic_tier": profile.semantic_tier.value,
        "act": profile.act,
        "registered": profile.registered,
        "read_only": is_read_only(tool_name),
        "source": ctx.get("source", "unknown"),
        "thread_id": ctx.get("thread_id"),
        "args_keys": sorted((args or {}).keys()),
    }

    # Phase 1 enforcement: deny fail-closed unregistered mutators ONLY when the
    # operator has opted in. Everything else is audit-only allow (the memory
    # source-trust gate already blocks the corruption half at the data layer;
    # the schedule/fs/outbound semantic decisions arrive in Phase 2).
    if (enforce_unknown_mutators
            and not profile.registered
            and profile.security_tier == SecurityTier.UNSAFE):
        logger.warning(
            "[commitment] DENY %s — unregistered mutator (fail-closed) "
            "effect=%s source=%s", tool_name, profile.effect.value,
            ctx.get("source", "unknown"),
        )
        return EffectDecision(
            decision="deny",
            reason="unregistered mutator (fail-closed); not in side-effect registry",
            profile=profile, audit=audit,
        )

    logger.info(
        "[commitment] allow %s tier=%s/%s act=%s registered=%s source=%s (phase1 audit-only)",
        tool_name, profile.security_tier.value, profile.semantic_tier.value,
        profile.act, profile.registered, ctx.get("source", "unknown"),
    )
    return EffectDecision(
        decision="allow",
        reason="phase 1 audit-only (semantic gate is Phase 2)",
        profile=profile, audit=audit,
    )
