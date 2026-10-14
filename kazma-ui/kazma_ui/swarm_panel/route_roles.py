"""Who may call each ``/api/swarm`` route -- one table, enforced on every route.

Swarm audit 2026-10-13, SW-S07: some routes asked for the admin role in
their own body (approve, templates), and others -- adding or editing a worker
and its system prompt, the output target, a breaker reset -- asked nothing,
so any signed-in user could change what a worker obeys or where results go.
Now the swarm routers use :class:`RoleCheckedRoute`, which looks every route
up here before it runs: ``user`` routes need only the sign-in the auth
middleware already demanded; ``admin`` routes need the admin decision. A
route missing from the table is refused as ``admin`` (closed by default), and
``tests/test_swarm_route_roles.py`` fails until it is listed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

__all__ = ["SWARM_ROUTE_ROLES", "RoleCheckedRoute"]

logger = logging.getLogger(__name__)

USER = "user"
ADMIN = "admin"

#: (method, path) -> the role it needs. Reading is for any signed-in user;
#: everything that runs, changes or routes swarm work is the operator's.
SWARM_ROUTE_ROLES: dict[tuple[str, str], str] = {
    ("GET", "/swarm"): USER,
    ("GET", "/api/swarm/status"): USER,
    ("GET", "/api/swarm/models"): USER,
    ("GET", "/api/swarm/circuit-breakers"): USER,
    ("GET", "/api/swarm/output-target"): USER,
    ("GET", "/api/swarm/templates"): USER,
    ("GET", "/api/swarm/tasks"): USER,
    ("GET", "/api/swarm/tasks/active"): USER,
    ("GET", "/api/swarm/tasks/export"): USER,
    ("GET", "/api/swarm/tasks/{task_id}"): USER,
    ("GET", "/api/swarm/tasks/{task_id}/stream"): USER,
    ("GET", "/api/swarm/workers/metrics/all"): USER,
    ("GET", "/api/swarm/workers/{name}/circuit-breaker"): USER,
    ("GET", "/api/swarm/workers/{name}/logs"): USER,
    ("GET", "/api/swarm/workers/{name}/metrics"): USER,
    ("POST", "/api/swarm/dispatch"): ADMIN,
    ("POST", "/api/swarm/start"): ADMIN,
    ("POST", "/api/swarm/stop"): ADMIN,
    ("POST", "/api/swarm/autoscaler/reap"): ADMIN,
    ("PUT", "/api/swarm/output-target"): ADMIN,
    ("POST", "/api/swarm/templates"): ADMIN,
    ("DELETE", "/api/swarm/templates/{name}"): ADMIN,
    ("POST", "/api/swarm/tasks/{task_id}/approve"): ADMIN,
    ("POST", "/api/swarm/tasks/{task_id}/reject"): ADMIN,
    ("POST", "/api/swarm/tasks/{task_id}/cancel"): ADMIN,
    ("POST", "/api/swarm/tasks/{task_id}/retry"): ADMIN,
    ("POST", "/api/swarm/workers"): ADMIN,
    ("POST", "/api/swarm/workers/spawn"): ADMIN,
    ("PUT", "/api/swarm/workers/{name}"): ADMIN,
    ("DELETE", "/api/swarm/workers/{name}"): ADMIN,
    ("POST", "/api/swarm/workers/{name}/start"): ADMIN,
    ("POST", "/api/swarm/workers/{name}/stop"): ADMIN,
    ("POST", "/api/swarm/workers/{name}/circuit-breaker/reset"): ADMIN,
}


def role_for(method: str, path: str) -> str:
    """The role a route needs; a route not in the table needs ``admin``."""
    method = "GET" if method == "HEAD" else method
    return SWARM_ROUTE_ROLES.get((method, path), ADMIN)


class RoleCheckedRoute(APIRoute):
    """An APIRoute that asks :data:`SWARM_ROUTE_ROLES` before it runs."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        path = self.path

        async def checked(request: Request) -> Response:
            # A single-operator surface (SW-S16, decision D1): tasks are not
            # separated per user, so an install with platform users refuses.
            from kazma_core.swarm.single_operator import swarm_refusal

            refusal = await asyncio.to_thread(swarm_refusal)
            if refusal:
                if path == "/swarm":  # the page says it in the page's language
                    request.state.swarm_refusal = refusal
                else:
                    return JSONResponse({"status": "error", "code": "swarm_single_operator",
                                         "message": refusal}, status_code=409)
            if role_for(request.method, path) == ADMIN:
                from kazma_ui.auth import admin_decision

                # The session and user stores: off the event loop.
                decision = await asyncio.to_thread(admin_decision, request)
                if decision == "unauthorized":
                    return JSONResponse({"status": "error", "code": "unauthorized",
                                         "message": "Unauthorized"}, status_code=401)
                if decision != "ok":
                    return JSONResponse({"status": "error", "code": "admin_required",
                                         "message": "Admin role required"}, status_code=403)
            return await handler(request)

        return checked
