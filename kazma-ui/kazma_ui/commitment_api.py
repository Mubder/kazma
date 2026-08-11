"""Commitment Layer operator API — soul-delta confirm queue (Phase 7).

Surfaces the pending soul-delta commitments (the needs_confirm queue created by
mint_soul_commitment) and the confirm→re-apply trigger. When
``soul_requires_confirm`` is ON, a soul delta is held at the apply site until an
operator confirms it here; the confirm flips the commitment to ``committed`` and
re-applies the delta (apply is event-driven, so the confirm path must trigger
the apply — a bare status flip would leave the delta unapplied).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)


def create_commitment_router() -> APIRouter:
    router = APIRouter(tags=["commitment"])

    @router.get("/api/commitment/soul/pending")
    async def list_pending_soul() -> dict:
        """List needs_confirm soul-delta commitments awaiting confirmation."""
        from kazma_core.safety.commitment.store import list_pending_soul as _list

        rows = _list()
        return {
            "count": len(rows),
            "pending": [
                {
                    "commitment_id": r.commitment_id,
                    "agent_id": (r.slots or {}).get("agent_id"),
                    "worker_name": (r.slots or {}).get("worker_name"),
                    "delta_preview": (r.slots or {}).get("delta", "")[:200],
                    "goal_text": r.goal_text,
                    "created_at": r.created_at,
                    "expires_at": r.expires_at,
                }
                for r in rows
            ],
        }

    @router.post("/api/commitment/soul/{commitment_id}/confirm")
    async def confirm_soul(commitment_id: str) -> dict:
        """Confirm a soul delta: flip to committed + re-apply (the trigger the
        event-driven apply path needs). Routes through apply_* so the now-
        committed commitment passes the gate."""
        from kazma_core.safety.commitment.store import get_commitment
        from kazma_core.skills.self_improvement import (
            apply_agent_mutation, confirm_soul_delta, get_self_improvement,
        )

        c = get_commitment(commitment_id)
        if c is None or c.act != "soul_delta":
            return {"error": "not a soul commitment", "commitment_id": commitment_id}
        if not confirm_soul_delta(commitment_id):
            return {"error": "confirm failed", "commitment_id": commitment_id}
        slots = c.slots or {}
        delta = slots.get("delta", "")
        agent_id = slots.get("agent_id")
        worker_name = slots.get("worker_name")
        applied = False
        try:
            if agent_id:
                applied = apply_agent_mutation(agent_id, delta, commitment_id=commitment_id)
            elif worker_name:
                si = get_self_improvement()
                if si is not None:
                    applied = await si.apply_mutation(
                        worker_name, delta, commitment_id=commitment_id)
        except Exception as exc:
            logger.warning("[commitment_api] soul re-apply failed for %s: %s",
                           commitment_id, exc)
        return {"confirmed": True, "applied": applied, "commitment_id": commitment_id}

    @router.post("/api/commitment/soul/{commitment_id}/reject")
    async def reject_soul(commitment_id: str) -> dict:
        """Reject a soul delta: abort the commitment (the delta stays unapplied)."""
        from kazma_core.safety.commitment.store import update_status

        c = update_status(commitment_id, "aborted", event_type="soul_rejected")
        if c is None:
            return {"error": "not found", "commitment_id": commitment_id}
        return {"rejected": True, "commitment_id": commitment_id}

    return router
