"""A swarm-bus approval request on the Web dashboard (Swarm audit SW-S03).

Every chat card links here (``approval_card.web_link``): the Swarm page's
approval panel reads ``GET /api/swarm/approvals/{id}`` -- the tool, the state
and the WHOLE call the gate record keeps, with the hash the card shows -- and
answers it with ``POST .../decide``, one more place to give the same shared
decision (``shared_approvals.resolve``, platform ``web``): the first human
Approve or Deny on any platform decides it (owner decision D7), and every
card shows that outcome.

A swarm request is never on the dashboard's pending list: that list admits a
gate whose chat session the caller owns, and a swarm task has none. Roles come
from the one table (``route_roles``): reading for a signed-in user, deciding
for the operator.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

#: Ids ``SafetyMiddleware`` mints: a gate id (letters, digits, "-", "_",
#: ":") or ``swarm-<hex>``. Anything else names no request.
_APPROVAL_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


def valid_approval_id(value: str | None) -> str:
    """*value* when it can name a request, else ``""`` (the page's query)."""
    value = (value or "").strip()
    return value if _APPROVAL_ID.match(value) else ""


def _actor(request: Request) -> str:
    """Who pressed, for the decision record: the signed-in user's name."""
    from kazma_ui.auth import get_request_principal

    principal = get_request_principal(request) or {}
    return str(principal.get("username") or principal.get("user_id") or "web user")


def register_approval_routes(router: APIRouter) -> None:
    """The approval panel's two routes. Plain ``def``s: each reads the
    settings store and the gate registry, off the event loop."""

    @router.get("/api/swarm/approvals/{approval_id}")
    def swarm_approval(approval_id: str) -> JSONResponse:
        from kazma_core.swarm import shared_approvals

        view = shared_approvals.describe(valid_approval_id(approval_id))
        if view is None:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        return JSONResponse({"ok": True, "approval": view})

    @router.post("/api/swarm/approvals/{approval_id}/decide")
    def swarm_approval_decide(approval_id: str, payload: dict[str, Any], request: Request) -> JSONResponse:
        from kazma_core.swarm import shared_approvals

        decision = str((payload or {}).get("decision") or "")
        if decision not in ("approve", "deny"):
            return JSONResponse({"ok": False, "error": "bad_decision"}, status_code=422)
        approval_id = valid_approval_id(approval_id)
        actor = _actor(request)
        decided = shared_approvals.decide_from_web(approval_id, decision == "approve", actor=actor)
        if decided is None:
            # Pressed and not recorded: said in the log and counted, like a
            # chat button; the request stays pending.
            return JSONResponse({"ok": False, "error": "not_recorded"}, status_code=500)
        view = shared_approvals.describe(approval_id)
        if decided:
            logger.info("[SwarmApproval] %s: %s by %s on web", approval_id, decision, actor)
            return JSONResponse({"ok": True, "approval": view})
        if view is None:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        # Decided, timed out or ended already: the panel shows what happened.
        return JSONResponse({"ok": False, "error": "not_pending", "approval": view}, status_code=409)
