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
#: The install's swarm, read: its workers' totals and breakers count every
#: space's work, and the output target is the owner's chat. Any signed-in
#: user while Spaces is not enforced (as before Spaces); the admin once it
#: is (Spaces S1-15 review).
INSTALL_READ = "install_read"

#: (method, path) -> the role it needs. Reading is for any signed-in user;
#: everything that runs, changes or routes swarm work is the operator's --
#: in every mode, until feature grants give the swarm to others (S2-11).
#: Once Spaces is enforced a task's own actions also keep to its space and
#: to the person who asked for it (``routes_tasks._not_here``, decision N4).
SWARM_ROUTE_ROLES: dict[tuple[str, str], str] = {
    ("GET", "/swarm"): USER,
    ("GET", "/api/swarm/status"): USER,
    ("GET", "/api/swarm/models"): USER,
    ("GET", "/api/swarm/circuit-breakers"): INSTALL_READ,
    ("GET", "/api/swarm/output-target"): INSTALL_READ,
    ("GET", "/api/swarm/templates"): USER,
    ("GET", "/api/swarm/tasks"): USER,
    ("GET", "/api/swarm/tasks/active"): USER,
    ("GET", "/api/swarm/tasks/export"): USER,
    ("GET", "/api/swarm/tasks/{task_id}"): USER,
    ("GET", "/api/swarm/tasks/{task_id}/stream"): USER,
    ("GET", "/api/swarm/workers/metrics/all"): INSTALL_READ,
    ("GET", "/api/swarm/workers/{name}/circuit-breaker"): INSTALL_READ,
    ("GET", "/api/swarm/workers/{name}/logs"): USER,
    ("GET", "/api/swarm/workers/{name}/metrics"): INSTALL_READ,
    # A bus approval request on the Web dashboard (SW-S03): its whole call is
    # read like the rest of the page; answering it is the operator's.
    ("GET", "/api/swarm/approvals/{approval_id}"): USER,
    ("POST", "/api/swarm/approvals/{approval_id}/decide"): ADMIN,
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
    """The role a route needs; a route not in the table needs ``admin``. An
    :data:`INSTALL_READ` route is any signed-in user's while Spaces is not
    enforced and the admin's once it is."""
    method = "GET" if method == "HEAD" else method
    role = SWARM_ROUTE_ROLES.get((method, path), ADMIN)
    if role == INSTALL_READ:
        from kazma_core.spaces.scope import spaces_enforced

        return ADMIN if spaces_enforced() else USER
    return role


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
            role = role_for(request.method, path)
            if role == ADMIN:
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
