"""Commitment Layer operator API — soul-delta confirm queue (Phase 7).

Surfaces the pending soul-delta commitments (the needs_confirm queue created by
mint_soul_commitment) and the confirm→re-apply trigger. When
``soul_requires_confirm`` is ON, a soul delta is held at the apply site until an
operator confirms it here; the confirm flips the commitment to ``committed`` and
re-applies the delta (apply is event-driven, so the confirm path must trigger
the apply — a bare status flip would leave the delta unapplied).

The Settings → Safety group lists pending deltas and can confirm/reject
them. ``soul_requires_confirm`` defaults OFF on a single-operator lab and
auto-ON in production / multi-user.

AUD-104: every route here is admin-only on an install with platform users
(``/api/commitment`` is in ``platform_rbac._ADMIN_PREFIXES``): a Soul delta
is guidance in every future system prompt, for every user. A refusal or a
failure answers with its own status (404 not a soul commitment, 409 not
confirmed or confirmed but refused at apply, 500 the apply raised) and a
``code``; they all answered 200.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from kazma_core.errors import safe_error

logger = logging.getLogger(__name__)


def create_commitment_router() -> APIRouter:
    router = APIRouter(tags=["commitment"])

    @router.get("/api/commitment/soul/pending")
    async def list_pending_soul() -> dict:
        """List needs_confirm soul-delta commitments awaiting confirmation."""
        from kazma_core.safety.commitment.store import async_list_pending_soul as _list

        rows = await _list()
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

    def _refusal(status: int, code: str, error: str, commitment_id: str) -> JSONResponse:
        return JSONResponse(
            {"error": error, "code": code, "commitment_id": commitment_id},
            status_code=status,
        )

    @router.post("/api/commitment/soul/{commitment_id}/confirm", response_model=None)
    async def confirm_soul(commitment_id: str) -> dict | JSONResponse:
        """Confirm a soul delta: flip to committed + re-apply (the trigger the
        event-driven apply path needs). Routes through apply_* so the now-
        committed commitment passes the gate."""
        from kazma_core.safety.commitment.store import async_get_commitment
        from kazma_core.skills.self_improvement import (
            apply_agent_mutation, confirm_soul_delta, get_self_improvement,
        )

        c = await async_get_commitment(commitment_id)
        if c is None or c.act != "soul_delta":
            return _refusal(404, "not_found", "not a soul commitment", commitment_id)
        # AUD-104: a commitment-store write; off the event loop.
        if not await asyncio.to_thread(confirm_soul_delta, commitment_id):
            return _refusal(409, "not_confirmed", "confirm failed", commitment_id)
        slots = c.slots or {}
        delta = slots.get("delta", "")
        agent_id = slots.get("agent_id")
        worker_name = slots.get("worker_name")
        applied = False
        try:
            if agent_id:
                applied = await asyncio.to_thread(
                    apply_agent_mutation, agent_id, delta, commitment_id=commitment_id
                )
            elif worker_name:
                si = get_self_improvement()
                if si is not None:
                    applied = await si.apply_mutation(
                        worker_name, delta, commitment_id=commitment_id)
        except Exception as exc:
            # AUD-104: confirmed but not applied is a failure the operator
            # must see, not a 200 with ``applied: false`` buried in it.
            info = safe_error(exc)
            return JSONResponse(
                {
                    "confirmed": True,
                    "applied": False,
                    "code": "apply_failed",
                    "error": str(info),
                    "commitment_id": commitment_id,
                },
                status_code=500,
            )
        if not applied:
            # The apply site refused the delta (the prompt fence, no
            # self-improvement engine for a worker): confirmed, not applied.
            return JSONResponse(
                {
                    "confirmed": True,
                    "applied": False,
                    "code": "not_applied",
                    "error": "confirmed, but the delta was not applied",
                    "commitment_id": commitment_id,
                },
                status_code=409,
            )
        return {"confirmed": True, "applied": applied, "commitment_id": commitment_id}

    @router.post("/api/commitment/soul/{commitment_id}/reject", response_model=None)
    async def reject_soul(commitment_id: str) -> dict | JSONResponse:
        """Reject a soul delta: abort the commitment (the delta stays unapplied)."""
        from kazma_core.safety.commitment.store import async_update_status

        c = await async_update_status(commitment_id, "aborted", event_type="soul_rejected")
        if c is None:
            return _refusal(404, "not_found", "not found", commitment_id)
        return {"rejected": True, "commitment_id": commitment_id}

    return router
