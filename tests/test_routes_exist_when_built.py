"""Every route the server serves exists when the app is built (2026-10-01).

The gates that read the route table build the app with ``create_app()`` and
never start it: every ``/api`` route and method has a caller
(``test_api_route_callers``), the load tests call real routes
(``test_loadtest_routes``). The Research and Time-travel APIs were mounted
in ``_on_startup``, so none of those gates ever saw ``/api/research`` or
``/api/replay`` -- 29 routes, among them a paper export that read any file
the server could and a report scorer that measured any file on disk.

This starts the real app (its lifespan runs ``_on_startup``) and requires
the route table to be the same afterwards. Mount a router in
``KazmaAppBuilder._setup_routers``; a router whose handlers need something
startup makes reads it per request (the replay API's getters).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tests.test_api_route_callers import _all_routes

Route = tuple[str, tuple[str, ...]]


def route_table(app: FastAPI) -> set[Route]:
    """(path, methods) of every route, included routers unwrapped."""
    return {
        (getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", None) or ())))
        for r in _all_routes(app.routes)
    }


def added_at_startup(app: FastAPI) -> set[Route]:
    """The routes that exist only once *app* has started."""
    before = route_table(app)
    with TestClient(app):
        after = route_table(app)
    return after - before


def test_no_route_is_mounted_at_startup() -> None:
    from kazma_ui.app import create_app

    app = create_app()
    # The two that were mounted at startup, by name.
    built = {path for path, _ in route_table(app)}
    assert {
        "/api/research/papers/export",
        "/api/research/eval",
        "/api/replay/threads",
        "/api/replay/fork",
    } <= built
    added = added_at_startup(app)
    assert not added, (
        "These routes are mounted while the app starts, so no gate that reads "
        "the built app's route table sees them. Mount the router in "
        "KazmaAppBuilder._setup_routers (read what startup makes per request):\n"
        + "\n".join(f"  {' '.join(m) or '-'} {p}" for p, m in sorted(added))
    )


def test_a_route_mounted_at_startup_is_found() -> None:
    """Negative control (§28): a router included by the lifespan."""
    late = APIRouter()

    @late.get("/api/late")
    def _late() -> dict:
        return {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.include_router(late)
        yield

    app = FastAPI(lifespan=lifespan)
    assert added_at_startup(app) == {("/api/late", ("GET",))}

