"""No method and path is registered twice in the built app (2026-10-15).

FastAPI serves the FIRST route that matches; a second registration of the
same method and path is dead code that keeps drifting from the one that
runs. ``settings.py`` and ``routes_voice.py`` both defined
``/api/voice/providers``, ``/api/voice/voices`` and ``/api/voice/stt-models``
(and the voice list asked Microsoft over the network on every Settings load,
in both copies).
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, FastAPI

from tests.test_api_route_callers import _all_routes


def _twice(app: FastAPI) -> list[str]:
    seen: Counter = Counter()
    for route in _all_routes(app.routes):
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", None) or ():
            if method != "HEAD":
                seen[(method, path)] += 1
    return sorted(f"{m} {p} x{n}" for (m, p), n in seen.items() if n > 1)


def test_no_method_and_path_is_registered_twice() -> None:
    from kazma_ui.app import create_app

    assert _twice(create_app()) == []


def test_a_path_registered_twice_is_found() -> None:
    """Negative control: two routers serving one path."""
    one, two = APIRouter(), APIRouter()

    @one.get("/api/x")
    def _first() -> dict:
        return {}

    @two.get("/api/x")
    def _second() -> dict:
        return {}

    app = FastAPI()
    app.include_router(one)
    app.include_router(two)
    assert _twice(app) == ["GET /api/x x2"]
