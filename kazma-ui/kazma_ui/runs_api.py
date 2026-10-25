"""Running work on the web: ``/api/runs`` and the Dashboard's Runs card.

* ``GET /api/runs`` -- every run (the run registry's read model) with the
  kinds that could not be read, the incidents (open first, then the newest
  finished ones) and whether the supervisor is watching. Any signed-in user.
* ``POST /api/runs/incidents/{id}/act`` -- the owner's decision on an
  incident (``kazma_core.runs.decide``): admin, same origin, rate-limited.
  The first decision wins on every surface; a second answers 409 with what
  was decided.
* ``GET`` / ``PUT /api/runs/runbooks`` -- the automatic first steps and
  whether each is on (``runs.runbooks``, all off by default). PUT is admin.

Single-operator only (decision D1): with platform users every route answers
409 ``runs_single_operator``. Words a person reads (states, actions) come in
the page's language; run titles and failure reasons are the runs' own data.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from kazma_ui.rate_limit import rate_limit
from kazma_ui.same_origin import verify_same_origin

__all__ = ["create_runs_router"]

logger = logging.getLogger(__name__)

REFUSAL_CODE = "runs_single_operator"
_REFUSAL = ("Running work is shown and acted on from a single-operator install. This install has "
            "platform users, and runs are not separated per user yet.")


class _Act(BaseModel):
    action: str = Field(..., min_length=1, max_length=40)


async def _refusal() -> str | None:
    from kazma_core.security.single_operator import single_operator_refusal

    return await asyncio.to_thread(single_operator_refusal, _REFUSAL)


async def _admin_denied(request: Request) -> JSONResponse | None:
    from kazma_ui.auth import admin_decision

    decision = await asyncio.to_thread(admin_decision, request)
    if decision == "ok":
        return None
    status = 401 if decision == "unauthorized" else 403
    return JSONResponse({"ok": False, "code": decision, "error": "Admin role required."}, status_code=status)


def _run_view(run: Any, lang: str) -> dict[str, Any]:
    from kazma_core.runs.kinds import KINDS
    from kazma_core.runs.text import attention, state_word

    data = run.to_dict()
    spec = KINDS.get(run.kind)
    data["kind_label"] = (spec.label_ar if lang == "ar" else spec.label) if spec else run.kind
    data["state_label"] = state_word(run, lang)
    data["attention"] = attention(run)
    return data


def _incident_view(row: dict[str, Any], lang: str) -> dict[str, Any]:
    from kazma_core.runs.incidents import public
    from kazma_core.runs.kinds import KINDS
    from kazma_core.runs.notify import _CONDITION
    from kazma_core.runs.text import action_word

    data = public(row)
    spec = KINDS.get(row["kind"])
    data["kind_label"] = (spec.label_ar if lang == "ar" else spec.label) if spec else row["kind"]
    data["condition_label"] = _CONDITION.get(row["condition"], ("", ""))[1 if lang == "ar" else 0]
    offered = row["actions"] if row["state"] == "open" else []
    data["offered"] = [{"action": a, "label": action_word(a, lang)} for a in offered]
    data["decided_label"] = action_word(row["decided_action"], lang) if row.get("decided_action") else ""
    # Why no message was sent, as a code the page words in its language
    # (the stored detail is the English log line).
    detail = str(row.get("notify_detail") or "")
    data["quiet_reason"] = ("before_start" if detail.startswith("ended before") else
                            "announced" if detail.startswith("announced by") else "")
    return data


def create_runs_router() -> APIRouter:
    router = APIRouter(tags=["runs"])
    mutating = [Depends(verify_same_origin), Depends(rate_limit("runs", 30))]

    @router.get("/api/runs")
    async def list_all(request: Request) -> JSONResponse:
        refusal = await _refusal()
        if refusal:
            return JSONResponse({"ok": False, "code": REFUSAL_CODE, "error": refusal}, status_code=409)
        from kazma_core.runs import supervisor
        from kazma_core.runs.incidents import get_incident_store
        from kazma_core.runs.kinds import KINDS
        from kazma_core.runs.registry import list_runs

        from kazma_ui.i18n import current_language

        lang = current_language()
        listing = await list_runs()
        incidents = await asyncio.to_thread(get_incident_store().recent, limit=30)
        return JSONResponse({
            "ok": True,
            "runs": [_run_view(r, lang) for r in listing.runs],
            "errors": listing.errors,
            "incidents": [_incident_view(row, lang) for row in incidents],
            "supervisor": supervisor.status(),
            "kinds": {kind: (spec.label_ar if lang == "ar" else spec.label) for kind, spec in KINDS.items()},
        })

    @router.post("/api/runs/incidents/{incident_id}/act", dependencies=mutating)
    async def act(incident_id: str, body: _Act, request: Request) -> JSONResponse:
        refusal = await _refusal()
        if refusal:
            return JSONResponse({"ok": False, "code": REFUSAL_CODE, "error": refusal}, status_code=409)
        denied = await _admin_denied(request)
        if denied is not None:
            return denied
        from kazma_core.runs.decide import decide

        from kazma_ui.i18n import current_language
        from kazma_ui.web_actor import web_actor

        actor = await asyncio.to_thread(web_actor, request)
        outcome = await decide(incident_id, body.action.strip().lower(), actor=actor, platform="web")
        status = 200
        if not outcome.ok:
            status = {"unknown_incident": 404, "already_decided": 409, "resolved": 409, "expired": 409,
                      "diagnosis_running": 409, "not_offered": 409}.get(outcome.code, 422)
        lang = current_language()
        return JSONResponse({"ok": outcome.ok, "code": outcome.code, "message": outcome.message,
                             "incident": _incident_view(outcome.incident, lang) if outcome.incident else None},
                            status_code=status)

    @router.get("/api/runs/runbooks")
    async def get_runbooks(request: Request) -> JSONResponse:
        refusal = await _refusal()
        if refusal:
            return JSONResponse({"ok": False, "code": REFUSAL_CODE, "error": refusal}, status_code=409)
        from kazma_core.runs.runbooks import RUNBOOK, enabled
        from kazma_core.runs.text import action_word

        from kazma_ui.i18n import current_language

        lang = current_language()
        flags = await asyncio.to_thread(enabled)
        return JSONResponse({"ok": True, "runbooks": [
            {"name": name, "action": action, "action_label": action_word(action, lang), "enabled": flags[name]}
            for name, action in RUNBOOK.items()]})

    @router.put("/api/runs/runbooks", dependencies=mutating)
    async def put_runbooks(body: dict[str, Any], request: Request) -> JSONResponse:
        refusal = await _refusal()
        if refusal:
            return JSONResponse({"ok": False, "code": REFUSAL_CODE, "error": refusal}, status_code=409)
        denied = await _admin_denied(request)
        if denied is not None:
            return denied
        from kazma_core.runs.runbooks import RUNBOOKS_KEY, enabled, validate_runbooks

        try:
            wanted = validate_runbooks(body)
        except ValueError as exc:
            return JSONResponse({"ok": False, "code": "invalid", "error": str(exc)}, status_code=422)

        def save() -> dict[str, bool]:
            from kazma_core.config_store import get_config_store

            merged = {**enabled(), **wanted}
            get_config_store().set(RUNBOOKS_KEY, merged)
            return merged

        merged = await asyncio.to_thread(save)
        logger.info("[runs] runbooks changed: %s", {k: v for k, v in merged.items() if v} or "all off")
        return JSONResponse({"ok": True, "runbooks": merged})

    return router
