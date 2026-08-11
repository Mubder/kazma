"""authorize_effect — the single policy gate (Commitment Layer §3.4 / §4 / §R2.6).

Phase 1: audit-only (still the path used by the ``LocalToolRegistry.execute``
choke, which has no turn context). Phase 2 adds the full decision logic for the
``remind`` act — the reference implementation (plan §3.7). Other acts stay
audit-only until their Phase 4 resolvers ship; the memory act's corruption
half is already blocked at the data layer (``mutate_belief`` source-trust gate).

Decision mapping (§3.4):
  - read-only                                   → allow (audit)
  - remind, unambiguous + memory anchor         → **allow + rewrite** (the gate
                                                  computes the correct fire_at
                                                  via resolve_remind and rewrites
                                                  the tool args — this is what
                                                  makes the CoPilot schedule path
                                                  impossible to get wrong)
  - remind, ambiguous (relative + nearby event) → **clarify** (persist a
                                                  needs_clarify commitment; the
                                                  tool_worker interrupts on it)
  - remind, unsatisfiable                       → deny
  - fail-closed unregistered mutator (opt-in)   → deny

Every decision persists a :class:`Commitment` row (the §8.2 "silent allows must
still audit" rule) — allows as ``ready`` (the caller flips to ``committed`` on
successful execution), clarifies as ``needs_clarify`` (24h TTL).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from kazma_core.safety.side_effects import (
    SecurityTier,
    SemanticTier,
    ToolEffectProfile,
    get_effect_profile,
    is_read_only,
)

logger = logging.getLogger(__name__)

__all__ = ["EffectDecision", "authorize_effect"]


@dataclass
class EffectDecision:
    """Outcome of authorizing one tool effect.

    ``decision`` ∈ {"allow", "deny", "clarify", "confirm"}:
      * allow    — execute (with ``rewritten_args`` if present, else the original).
      * deny     — do not execute; return an error to the model.
      * clarify  — interrupt with a targeted question (a pending commitment is
                   persisted; the tool_worker suspends via interrupt()).
      * confirm  — interrupt for explicit OK (critical acts); Phase 3 wires the
                   combined-card UX. Treated like clarify until then.
    """
    decision: str
    reason: str
    profile: ToolEffectProfile
    audit: dict[str, Any] = field(default_factory=dict)
    commitment_id: str | None = None
    rewritten_args: dict[str, Any] | None = None
    clarify_question: str | None = None


def _args_digest(args: dict[str, Any] | None) -> str:
    if not args:
        return ""
    try:
        h = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()
        return h[:12]
    except Exception:
        return ""


def authorize_effect(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    # Phase 2 resolution inputs (only the tool_worker gate supplies these):
    user_text: str | None = None,
    request_at: datetime | None = None,
    memory_beliefs: list[dict[str, Any]] | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    tenant_id: str = "default",
    # Phase 1 knobs:
    enforce_unknown_mutators: bool = False,
    cfg: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> EffectDecision:
    """Authorize one tool effect.

    Phase 1 callers (``LocalToolRegistry.execute``) pass only ``tool_name`` /
    ``args`` / ``enforce_unknown_mutators`` → audit-only. The Phase 2
    ``tool_worker`` gate additionally passes ``user_text`` / ``request_at`` /
    ``memory_beliefs`` / thread context → full remind-act resolution kicks in.
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
        "thread_id": thread_id or ctx.get("thread_id"),
        "args_digest": _args_digest(args),
    }

    # Phase 1 enforcement: fail-closed unregistered mutators (opt-in only).
    if (enforce_unknown_mutators
            and not profile.registered
            and profile.security_tier == SecurityTier.UNSAFE):
        logger.warning(
            "[commitment] DENY %s — unregistered mutator (fail-closed) source=%s",
            tool_name, ctx.get("source", "unknown"),
        )
        return EffectDecision(
            decision="deny",
            reason="unregistered mutator (fail-closed); not in side-effect registry",
            profile=profile, audit=audit,
        )

    # Phase 2: act-specific resolution. Remind is the reference impl (§3.7);
    # resolve_remind was measured at 0 false-allow on held-out goldens (G2).
    if (profile.act == "remind"
            and user_text
            and request_at is not None
            and memory_beliefs is not None):
        return _resolve_remind_act(
            profile, tool_name, args or {}, user_text, request_at, memory_beliefs,
            audit=audit, thread_id=thread_id, turn_id=turn_id,
            tenant_id=tenant_id, cfg=cfg, source=ctx.get("source", "unknown"),
        )

    # Everything else: audit-only allow (memory corruption is gated at
    # mutate_belief; other acts' resolvers arrive in Phase 4).
    logger.info(
        "[commitment] allow %s tier=%s/%s act=%s (audit-only) source=%s",
        tool_name, profile.security_tier.value, profile.semantic_tier.value,
        profile.act, ctx.get("source", "unknown"),
    )
    return EffectDecision(
        decision="allow",
        reason=("read-only" if profile.semantic_tier == SemanticTier.NONE
                else "audit-only (act resolver pending)"),
        profile=profile, audit=audit,
    )


def _resolve_remind_act(
    profile: ToolEffectProfile,
    tool_name: str,
    args: dict[str, Any],
    user_text: str,
    request_at: datetime,
    memory_beliefs: list[dict[str, Any]],
    *,
    audit: dict[str, Any],
    thread_id: str | None,
    turn_id: str | None,
    tenant_id: str,
    cfg: dict[str, Any] | None,
    source: str,
) -> EffectDecision:
    """Resolve a remind tool call: anchor to memory, compute fire_at, decide."""
    # Lazy imports (store → paths; relative_time is standalone).
    from .relative_time import resolve_remind as _resolve
    from .store import Commitment, create_commitment

    res = _resolve(user_text, request_at=request_at, memory_beliefs=memory_beliefs)

    req_ts = request_at.timestamp() if hasattr(request_at, "timestamp") else time.time()
    commitment = Commitment(
        thread_id=thread_id or "", turn_id=turn_id, act="remind",
        tool_name=tool_name, goal_text=(user_text or "")[:200],
        args_digest=_args_digest(args), request_at=req_ts, tenant_id=tenant_id,
        slots={
            "fire_at": res.fire_at.isoformat() if res.fire_at else None,
            "anchor": res.anchor,
        },
        conflicts=list(res.conflicts),
        confidence=1.0 if res.decision == "allow" else 0.4,
    )

    if res.decision == "allow" and res.fire_at is not None:
        # Rewrite the schedule args to the CORRECT fire_at — this is the fix
        # for the CoPilot schedule path. Whatever the model put in `timing`,
        # the anchored, memory-checked ISO date wins.
        rewritten = dict(args)
        rewritten["timing"] = res.fire_at.isoformat()
        commitment.status = "ready"
        commitment.policy_decision = "allow"
        cid = create_commitment(commitment, cfg=cfg)
        logger.info(
            "[commitment] allow+rewrite %s fire_at=%s anchor=%s cid=%s source=%s",
            tool_name, res.fire_at.isoformat(), res.anchor, cid, source,
        )
        return EffectDecision(
            decision="allow", reason=res.reason, profile=profile, audit=audit,
            commitment_id=cid, rewritten_args=rewritten,
        )

    if res.decision == "clarify":
        commitment.status = "needs_clarify"
        commitment.policy_decision = "clarify"
        cid = create_commitment(commitment, cfg=cfg)  # 24h TTL
        logger.info(
            "[commitment] clarify %s anchor=%s cid=%s — %s source=%s",
            tool_name, res.anchor, cid, res.reason, source,
        )
        return EffectDecision(
            decision="clarify", reason=res.reason, profile=profile, audit=audit,
            commitment_id=cid, clarify_question=res.reason,
        )

    # deny (rare for remind — e.g. unsatisfiable)
    commitment.status = "aborted"
    commitment.policy_decision = "deny"
    cid = create_commitment(commitment, cfg=cfg)
    logger.info("[commitment] deny %s cid=%s — %s", tool_name, cid, res.reason)
    return EffectDecision(
        decision="deny", reason=res.reason or "remind unresolved", profile=profile,
        audit=audit, commitment_id=cid,
    )
