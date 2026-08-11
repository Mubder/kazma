"""Swarm worker scope-token (Commitment Layer §3.11 — privilege-escalation guard).

A dispatched worker inherits its orchestrator's authorization scope and may NOT
exceed it. The scope is a :class:`ScopeToken` carried on a ContextVar
(``_swarm_scope_ctx``), set once at dispatch (``worker_dispatch._do_dispatch``
wraps ``worker.dispatch()``) and read inside ``authorize_effect`` for every
worker tool call — the same pattern as ``ide.workspace_scope`` / the HITL
ContextVars.

Enforcement is gated by ``agent.commitment.swarm_scope_enforce`` (default OFF —
safe rollout; mirrors ``enforce_unknown_mutators``). When ON, a worker tool call
whose act is outside ``allowed_acts``, in ``denied_acts``, or whose semantic
tier exceeds ``max_semantic_tier`` is DENIED. The main agent (no active scope)
is never restricted.

The token rides on ``SwarmDispatchContext.metadata["commitment_scope"]``;
``build_handoff_context`` must forward metadata so it survives A→B handoffs.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import AsyncIterator

from kazma_core.safety.side_effects import SemanticTier

__all__ = [
    "ScopeToken",
    "swarm_scope",
    "current_scope",
    "is_act_within_scope",
    "SEMANTIC_TIER_RANK",
]

# Rank for ceiling comparison (NONE < LOW < HIGH < CRITICAL).
SEMANTIC_TIER_RANK: dict[SemanticTier, int] = {
    SemanticTier.NONE: 0,
    SemanticTier.LOW: 1,
    SemanticTier.HIGH: 2,
    SemanticTier.CRITICAL: 3,
}


@dataclass(frozen=True)
class ScopeToken:
    """Authorization ceiling a swarm worker inherits from its orchestrator.

    All fields optional (None / empty = no restriction on that axis). A token
    with everything None is an explicit " unrestricted worker" grant.
    """
    parent_commitment_id: str | None = None
    allowed_acts: frozenset[str] | None = None   # None = all acts allowed
    denied_acts: frozenset[str] = field(default_factory=frozenset)
    max_semantic_tier: SemanticTier | None = None  # None = no ceiling
    workspace_id: str | None = None
    thread_id: str | None = None

    def to_metadata(self) -> dict:
        """Serialize for SwarmDispatchContext.metadata (frozenset → sorted list)."""
        return {
            "parent_commitment_id": self.parent_commitment_id,
            "allowed_acts": sorted(self.allowed_acts) if self.allowed_acts else None,
            "denied_acts": sorted(self.denied_acts),
            "max_semantic_tier": (self.max_semantic_tier.value
                                  if self.max_semantic_tier else None),
            "workspace_id": self.workspace_id,
            "thread_id": self.thread_id,
        }

    @classmethod
    def from_metadata(cls, meta: dict | None) -> "ScopeToken | None":
        """Reconstruct from metadata. Returns None if meta is absent/empty."""
        if not meta:
            return None
        mst = meta.get("max_semantic_tier")
        try:
            tier = SemanticTier(mst) if mst else None
        except ValueError:
            tier = None
        aa = meta.get("allowed_acts")
        da = meta.get("denied_acts") or []
        return cls(
            parent_commitment_id=meta.get("parent_commitment_id"),
            allowed_acts=frozenset(aa) if aa else None,
            denied_acts=frozenset(da),
            max_semantic_tier=tier,
            workspace_id=meta.get("workspace_id"),
            thread_id=meta.get("thread_id"),
        )


_swarm_scope_ctx: ContextVar[ScopeToken | None] = ContextVar(
    "kazma_swarm_scope", default=None)


@asynccontextmanager
async def swarm_scope(token: "ScopeToken | None") -> AsyncIterator[None]:
    """Bind *token* as the active worker scope for the duration of the block.

    Mirrors ``ide.workspace_scope.workspace_scope``. None token → no-op (the
    main agent / a worker whose dispatch set no scope runs unrestricted).
    """
    if token is None:
        yield
        return
    tok = _swarm_scope_ctx.set(token)
    try:
        yield
    finally:
        _swarm_scope_ctx.reset(tok)


def current_scope() -> ScopeToken | None:
    """The active worker scope, or None (main agent / no scope set)."""
    return _swarm_scope_ctx.get()


def is_act_within_scope(act: str | None, semantic_tier: SemanticTier,
                        scope: ScopeToken | None) -> tuple[bool, str]:
    """Return (allowed, reason). ``scope=None`` (main agent) → always allowed.

    The privilege-escalation guard (§3.11 point 5): a worker mutator outside
    its inherited scope is denied.
    """
    if scope is None:
        return True, "main agent (no scope)"  # orchestrator is unrestricted
    # allowed_acts: if specified, act must be in it.
    if scope.allowed_acts is not None:
        if act and act not in scope.allowed_acts:
            return False, f"act {act!r} not in worker allowed_acts"
    # denied_acts: explicit deny list wins.
    if act and act in scope.denied_acts:
        return False, f"act {act!r} denied by worker scope"
    # max_semantic_tier ceiling (only meaningful for gated tiers).
    if (scope.max_semantic_tier is not None
            and semantic_tier != SemanticTier.NONE
            and SEMANTIC_TIER_RANK[semantic_tier] >
            SEMANTIC_TIER_RANK[scope.max_semantic_tier]):
        return False, (f"semantic tier {semantic_tier.value} exceeds worker "
                       f"ceiling {scope.max_semantic_tier.value}")
    return True, "within scope"
