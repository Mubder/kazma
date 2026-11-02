"""Every document route refuses in one envelope: ``ok``, ``error``, ``code``.

2026-11 functional audit, FA-234. Most refusals were ``{"ok": false,
"error", "code"}``, but the JSON body reader's 413/422 reached the client as
``{"detail": {...}}``, the rate limiter's 429 as ``{"detail": "..."}`` with
no code, a query FastAPI could not validate echoed the input back, and
twenty-odd literal refusals carried no code. A missing knowledge library
answered ``document_access_denied`` on ``/index`` and
``library_access_denied`` on ``/search``: one cause, two codes, neither
saying the library does not exist.

Measured through the real router (``_DocumentRoute``), the literal refusals
by reading the module (a planted codeless refusal is the negative control),
and the library on all four of the adapter's paths.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from kazma_ui import documents_api

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "kazma-ui" / "kazma_ui" / "documents_api.py"


@pytest.fixture
def client(monkeypatch):
    async def scope(request):
        return "t", "operator", "w"

    monkeypatch.setattr(documents_api, "_resolve_scope", scope)
    app = FastAPI()
    app.state.documents = SimpleNamespace()
    app.include_router(documents_api.create_documents_router())
    return TestClient(app)


def _envelope(response) -> dict:
    body = response.json()
    assert "detail" not in body, body
    assert body["ok"] is False and body["error"] and body["code"], body
    return body


def test_a_malformed_json_body_is_answered_in_the_envelope(client):
    response = client.post("/api/documents/search", content=b"{not json",
                           headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert _envelope(response)["code"] == "invalid_json"


def test_an_oversized_json_body_is_answered_in_the_envelope(client, monkeypatch):
    monkeypatch.setattr(documents_api, "_JSON_BODY_MAX_BYTES", 16)
    response = client.post("/api/documents/search", content=b'{"query": "' + b"x" * 64 + b'"}',
                           headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert _envelope(response)["code"] == "request_too_large"


def test_an_invalid_query_names_the_field_and_echoes_nothing(client):
    hostile = "<script>alert(1)</script>"
    response = client.get(f"/api/documents/doc-1/content?max_chars={hostile}")
    assert response.status_code == 422
    body = _envelope(response)
    assert body["code"] == "invalid_request" and body["fields"] == ["query.max_chars"]
    assert "script" not in response.text


def test_a_raised_refusal_without_a_code_gets_one_and_keeps_its_headers():
    from kazma_ui.error_envelope import http_envelope

    response = http_envelope(
        HTTPException(429, "Too many requests", headers={"Retry-After": "7"}))
    assert response.status_code == 429 and response.headers["retry-after"] == "7"
    assert response.body == b'{"ok":false,"error":"Too many requests","code":"rate_limited"}'


def _codeless_refusals(source: str) -> list[int]:
    """Lines of a literal ``JSONResponse`` refusal (status >= 400) whose body
    lacks ``ok``, ``error`` or ``code``."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "JSONResponse"):
            continue
        status = next((kw.value for kw in node.keywords if kw.arg == "status_code"), None)
        if not (isinstance(status, ast.Constant) and status.value >= 400):
            continue
        content = next((kw.value for kw in node.keywords if kw.arg == "content"),
                       node.args[0] if node.args else None)
        keys = ({k.value for k in content.keys if isinstance(k, ast.Constant)}
                if isinstance(content, ast.Dict) else set())
        if not {"ok", "error", "code"} <= keys:
            found.append(node.lineno)
    return found


#: The route modules whose refusals are held to the envelope (FA-234, FA-241).
ENVELOPED = [API, ROOT / "kazma-ui" / "kazma_ui" / "scheduled_api.py"]


@pytest.mark.parametrize("module", ENVELOPED, ids=lambda p: p.name)
def test_every_literal_refusal_has_a_code(module):
    assert _codeless_refusals(module.read_text(encoding="utf-8")) == []


def test_negative_control_a_codeless_refusal_is_found():
    planted = ("def f():\n"
               "    return JSONResponse(status_code=404, content={'ok': False, 'error': 'Job not found'})\n")
    assert _codeless_refusals(planted) == [2]


# ── A missing library ────────────────────────────────────────────────────────


DOC = "3f2b6c1e-7a4d-4e8b-9c0d-1a2b3c4d5e6f"
VER = "8a7b6c5d-4e3f-4a1b-8c2d-3e4f5a6b7c8d"


def _adapter():
    from kazma_core.documents.knowledge import DocumentKnowledgeAdapter

    adapter = DocumentKnowledgeAdapter.__new__(DocumentKnowledgeAdapter)
    record = SimpleNamespace(id=DOC, current_version_id=VER)
    adapter.repository = SimpleNamespace(
        get_document=lambda **kw: record,
        get_version=lambda **kw: SimpleNamespace(document_id=DOC),
        has_access=lambda **kw: True,
    )
    adapter.store = SimpleNamespace(get_library_for_tenant=lambda library_id, tenant_id: None)
    return adapter


def test_a_missing_library_is_one_code_on_every_path():
    adapter = _adapter()
    scope = {"tenant_id": "t", "library_id": "gone"}
    document = SimpleNamespace(document_id=DOC, version_id=VER)
    results = [
        adapter._index_document_ir(document, actor_id="operator", **scope),
        adapter.unindex_document(actor_id="operator", document_id=DOC, **scope),
        asyncio.run(adapter.search_document("q", actor_id="operator", document_id=DOC, **scope)),
        asyncio.run(adapter.search_library("q", **scope)),
    ]
    assert {(r.ok, r.code, r.message) for r in results} == {
        (False, "library_not_found", "Knowledge library not found")}
    assert documents_api._STATUS_BY_CODE["library_not_found"] == 404


def test_a_missing_document_is_still_its_own_answer():
    adapter = _adapter()
    adapter.repository.get_document = lambda **kw: None
    result = adapter.unindex_document(tenant_id="t", actor_id="operator", library_id="lib",
                                      document_id=DOC)
    assert (result.ok, result.code) == (False, "document_access_denied")
