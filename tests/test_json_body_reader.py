"""A malformed JSON body is a 400, the same on every route (2026-11, FA-274).

Twenty routes parsed their body with ``await request.json()`` and answered a
malformed one each their own way: 400, 422, a 200 with an error inside, a
503 from the chat session bind's catch-all, and in five an empty body -- the
request ran as if the client had sent nothing. Every route now reads through
``kazma_ui.json_body.read_json`` and answers ``InvalidJsonBody`` with the
400 envelope.

The gate finds every other ``request.json()`` in the web packages (a planted
one is its negative control); the behaviour is measured on two real routes,
the chat session bind (it was the 503) and the sign-in, whose empty body
still reads as empty.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway")

#: Reads of ``request.json()`` that are not a route reading its own body.
DECLARED = {
    "kazma-ui/kazma_ui/json_body.py": "the reader itself (it parses request.body())",
    "kazma-ui/kazma_ui/ide_effects.py": "the effect receipt reads the body to key it; the route parses it",
    "kazma-ui/kazma_ui/memory_api.py": "a caller that passes a plain object, not a Request (the bounded "
                                       "reader handles every Request)",
    "kazma-gateway/kazma_gateway/adapters/telegram.py": "the webhook answers 400 itself; the gateway "
                                                        "package does not import kazma_ui",
}


def _raw_reads(source: str) -> list[int]:
    """Lines that call ``<something>.json()`` on a name called ``request``."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "json" and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "request"]


def test_no_route_reads_a_json_body_another_way():
    found: dict[str, list[int]] = {}
    for pkg in PACKAGES:
        for file in sorted((ROOT / pkg).rglob("*.py")):
            rel = file.relative_to(ROOT).as_posix()
            lines = _raw_reads(file.read_text(encoding="utf-8"))
            if lines and rel not in DECLARED:
                found[rel] = lines
    assert not found, f"read the body with kazma_ui.json_body.read_json: {found}"


def test_every_declaration_still_matches_a_read():
    stale = [rel for rel in DECLARED if rel != "kazma-ui/kazma_ui/json_body.py"
             and not _raw_reads((ROOT / rel).read_text(encoding="utf-8"))]
    assert not stale, stale


def test_negative_control_a_planted_read_is_found():
    planted = "async def route(request):\n    body = await request.json()\n"
    assert _raw_reads(planted) == [2]


@pytest.fixture
def bind_client(monkeypatch):
    import kazma_ui.session_manager as sm_mod
    from kazma_ui.sse_chat import create_sse_chat_router

    sm_mod.reset_session_manager()
    app = FastAPI()
    app.include_router(create_sse_chat_router(graph=None, checkpointer=None))
    try:
        yield TestClient(app)
    finally:
        sm_mod.reset_session_manager()


def test_the_session_bind_answers_a_malformed_body_400(bind_client):
    r = bind_client.post("/api/chat/sessions", content=b"{nope", headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json() == {"ok": False, "error": "The request body is not valid JSON", "code": "invalid_json"}


def test_the_session_bind_still_binds(bind_client):
    r = bind_client.post("/api/chat/sessions", json={"session_id": "s-json-reader"})
    assert r.status_code == 200 and r.json()["session_id"] == "s-json-reader"


@pytest.fixture
def login(monkeypatch):
    from kazma_ui.routes_direct.auth import register_auth_routes

    monkeypatch.setenv("KAZMA_SECRET", "s" * 40)
    monkeypatch.setenv("KAZMA_TRUST_LAN", "0")
    app = FastAPI()
    register_auth_routes(SimpleNamespace(app=app))
    return TestClient(app)


def test_a_malformed_sign_in_is_a_400_not_a_failed_sign_in(login):
    r = login.post("/api/auth/login", content=b"secret=abc", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_json"


def test_an_empty_sign_in_still_reads_as_empty(login):
    r = login.post("/api/auth/login", content=b"")
    assert r.status_code == 401
