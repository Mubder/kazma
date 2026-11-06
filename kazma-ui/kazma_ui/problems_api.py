"""Open problems on the web: ``/api/problems`` and the Dashboard's card.

* ``GET /api/problems`` -- every open problem (``observability.open_problems``),
  without the acknowledged and snoozed ones unless ``?hidden=1``. Any
  signed-in user.
* ``POST /api/problems/{id}/ack`` -- hide a problem until it changes; ``POST
  /api/problems/{id}/snooze`` -- hide it for a day. Admin, same origin,
  rate-limited.
"""

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from kazma_ui.rate_limit import rate_limit
from kazma_ui.same_origin import verify_same_origin

__all__ = ["create_problems_router"]

_ID = re.compile(r"^[0-9a-f]{16}$")


def create_problems_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/problems")
    async def list_problems(hidden: int = 0) -> JSONResponse:
        from kazma_core.observability.open_problems import open_problems
        from kazma_ui.i18n import current_language

        lang = current_language()
        data = await asyncio.to_thread(open_problems, lang, include_hidden=bool(hidden))
        return JSONResponse({"ok": True, **data})

    async def _decide(request: Request, problem_id: str, how: str) -> JSONResponse:
        from kazma_ui.auth import admin_decision
        from kazma_ui.web_actor import web_actor

        decision = await asyncio.to_thread(admin_decision, request)
        if decision != "ok":
            return JSONResponse({"ok": False, "code": decision, "error": "Admin role required."},
                                status_code=401 if decision == "unauthorized" else 403)
        if not _ID.match(problem_id):
            return JSONResponse({"ok": False, "code": "invalid_id", "error": "Not a problem id."}, status_code=422)
        from kazma_core.observability import open_problems as op

        actor = await asyncio.to_thread(web_actor, request)
        await asyncio.to_thread(op.acknowledge if how == "ack" else op.snooze, problem_id, actor or "owner")
        return JSONResponse({"ok": True, "id": problem_id, "done": how})

    @router.post("/api/problems/{problem_id}/ack",
                 dependencies=[Depends(verify_same_origin), Depends(rate_limit("problems", 30))])
    async def ack_problem(problem_id: str, request: Request) -> JSONResponse:
        return await _decide(request, problem_id, "ack")

    @router.post("/api/problems/{problem_id}/snooze",
                 dependencies=[Depends(verify_same_origin), Depends(rate_limit("problems", 30))])
    async def snooze_problem(problem_id: str, request: Request) -> JSONResponse:
        return await _decide(request, problem_id, "snooze")

    return router
