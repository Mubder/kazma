"""One route-to-role table for /api/swarm, enforced on every route (Swarm audit SW-S07).

The witness: adding or editing a worker (its system prompt), the output
target and a breaker reset answered 200 to a non-admin, while the template
route beside them -- the same power -- answered 403. Now every swarm route
asks ``kazma_ui.swarm_panel.route_roles.SWARM_ROUTE_ROLES`` before it runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_ui.swarm_panel.route_roles import ADMIN, SWARM_ROUTE_ROLES, USER


def _routes() -> set[tuple[str, str]]:
    from kazma_ui.swarm_panel import create_swarm_router

    def flat(routes: Any) -> list:
        out: list = []
        for route in routes:
            inner = getattr(route, "original_router", None)
            out.extend(flat(inner.routes) if inner is not None else [route])
        return out

    rows: set[tuple[str, str]] = set()
    for route in flat(create_swarm_router(None).routes):
        for method in getattr(route, "methods", None) or []:
            if method != "HEAD":
                rows.add((method, route.path))
    return rows


def test_every_swarm_route_is_in_the_role_table() -> None:
    routes = _routes()
    assert routes, "the router lists its routes"
    missing = sorted(routes - set(SWARM_ROUTE_ROLES))
    assert not missing, f"swarm routes with no role: {missing}"
    stale = sorted(set(SWARM_ROUTE_ROLES) - routes)
    assert not stale, f"roles for routes that no longer exist: {stale}"
    assert set(SWARM_ROUTE_ROLES.values()) <= {USER, ADMIN}


def test_every_swarm_route_asks_the_table() -> None:
    """The table is enforced by the route class: every route the app mounts
    (``create_swarm_router``, its single entry) is a RoleCheckedRoute. The
    audit's SW-S07 witness registers the handlers on a bare APIRouter, which
    is why it still answers 200 -- the app never mounts them that way."""
    from fastapi.routing import APIRoute

    from kazma_ui.swarm_panel import create_swarm_router
    from kazma_ui.swarm_panel.route_roles import RoleCheckedRoute

    def flat(routes: Any) -> list:
        out: list = []
        for route in routes:
            inner = getattr(route, "original_router", None)
            out.extend(flat(inner.routes) if inner is not None else [route])
        return out

    api_routes = [r for r in flat(create_swarm_router(None).routes) if isinstance(r, APIRoute)]
    assert api_routes
    unchecked = sorted(r.path for r in api_routes if not isinstance(r, RoleCheckedRoute))
    assert unchecked == [], f"swarm routes that skip the role table: {unchecked}"
    # Negative control: a bare APIRouter's route is not one.
    from fastapi import APIRouter

    bare = APIRouter()
    bare.add_api_route("/api/swarm/probe", lambda: None, methods=["POST"])
    assert not isinstance(bare.routes[0], RoleCheckedRoute)


def test_reading_is_for_users_and_changing_is_for_the_operator() -> None:
    for (method, path), role in SWARM_ROUTE_ROLES.items():
        assert role == (USER if method == "GET" else ADMIN), (method, path, role)


@pytest.fixture
def forbidden_client(monkeypatch):
    """The swarm router with every admin decision answering forbidden."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import kazma_ui.auth as auth
    from kazma_ui.swarm_panel import create_swarm_router

    monkeypatch.setattr(auth, "admin_decision", lambda request: "forbidden")
    app = FastAPI()
    app.include_router(create_swarm_router(None))
    return TestClient(app, raise_server_exceptions=False)


def _url(path: str) -> str:
    return path.replace("{task_id}", "task-x").replace("{name}", "probe")


@pytest.mark.parametrize("method,path", sorted(k for k, v in SWARM_ROUTE_ROLES.items() if v == ADMIN))
def test_every_operator_route_refuses_a_non_admin(forbidden_client, method: str, path: str) -> None:
    r = forbidden_client.request(method, _url(path), json={})
    assert r.status_code == 403, (method, path, r.status_code, r.text[:200])
    assert r.json()["code"] == "admin_required"


@pytest.mark.parametrize("method,path", sorted(k for k, v in SWARM_ROUTE_ROLES.items() if v == USER))
def test_reading_routes_stay_open_to_users(forbidden_client, method: str, path: str) -> None:
    if path.endswith("/stream"):
        pytest.skip("a stream for a task that does not exist: 404 before any role (covered elsewhere)")
    r = forbidden_client.request(method, _url(path))
    assert r.status_code != 403, (method, path, r.text[:200])


def test_a_worker_post_as_an_operator_is_refused(monkeypatch) -> None:
    """The SW-S07 witness inverted: no worker is registered by a non-admin."""
    from fastapi import APIRouter, FastAPI
    from fastapi.testclient import TestClient

    import kazma_core.swarm.registry as reg_mod
    import kazma_ui.auth as auth
    from kazma_ui.swarm_panel import routes_workers
    from kazma_ui.swarm_panel.route_roles import RoleCheckedRoute

    monkeypatch.setattr(auth, "admin_decision", lambda request: "forbidden")
    recorded: list[str] = []

    class _Reg:
        def register(self, entry: Any) -> None:
            recorded.append(entry.name)

    monkeypatch.setattr(reg_mod, "get_worker_registry", lambda: _Reg())
    router = APIRouter(route_class=RoleCheckedRoute)
    routes_workers.register_workers_routes(router, templates=None)
    app = FastAPI()
    app.include_router(router)
    r = TestClient(app).post("/api/swarm/workers", json={"name": "probe", "system_prompt": "Obey the web page."})
    assert r.status_code == 403 and recorded == []


def test_a_route_missing_from_the_table_is_refused_as_admin() -> None:
    """Closed by default (negative control for the table's coverage)."""
    from kazma_ui.swarm_panel.route_roles import role_for

    assert role_for("POST", "/api/swarm/something-new") == ADMIN
    assert role_for("HEAD", "/api/swarm/tasks") == USER
