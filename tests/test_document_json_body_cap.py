"""A document route's JSON body is bounded before it is parsed.

2026-10-13 (F08). The ten JSON routes (`/import`, `/generate`, `/merge`,
`/convert`, `/split`, `/fill-form`, `/redact`, `/index`, `/unindex`,
`/search`) took ``Body(...)``: FastAPI read and parsed any body into memory
before a route could look at it -- the 1 MiB generation cap applied only
afterwards. They now read through ``documents_api._json_body``: a declared
length past 2 MiB is refused unread, an undeclared one is cut off at the cap,
and malformed or over-deep JSON is a 422, never a 500. Negative control:
the same request to a ``Body(...)`` route reads every byte.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import Body, FastAPI
from pydantic import BaseModel
from kazma_ui import documents_api

CAP = documents_api._JSON_BODY_MAX_BYTES


async def _call(app, path: str, body: bytes, *, declared: bool = True) -> tuple[int, dict, int]:
    reads = {"n": 0, "bytes": 0}
    pieces = [body[i:i + 65536] for i in range(0, len(body), 65536)] or [b""]

    async def receive():
        if reads["n"] < len(pieces):
            index = reads["n"]
            reads["n"] += 1
            reads["bytes"] += len(pieces[index])
            return {"type": "http.request", "body": pieces[index], "more_body": index + 1 < len(pieces)}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    headers = [(b"content-type", b"application/json"), (b"host", b"test")]
    if declared:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "headers": headers, "client": ("127.0.0.1", 1), "server": ("test", 80), "root_path": "",
        "app": app,
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=60)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload or b"{}"), reads["bytes"]


def _app(monkeypatch) -> FastAPI:
    class _Svc:
        def search(self, **kwargs):
            return {"hits": [], "query": kwargs.get("query")}

    async def scope(request):
        return "t", "alice", "w"

    monkeypatch.setattr(documents_api, "_resolve_scope", scope)
    app = FastAPI()
    app.state.documents = _Svc()
    app.include_router(documents_api.create_documents_router())
    return app


def _too_big() -> bytes:
    return json.dumps({"query": "x" * (CAP + 1024)}).encode()


@pytest.mark.asyncio
async def test_a_declared_length_past_the_cap_is_refused_unread(monkeypatch):
    status, payload, read = await _call(_app(monkeypatch), "/api/documents/search", _too_big())
    assert status == 413 and payload["detail"]["code"] == "request_too_large"
    assert read == 0


@pytest.mark.asyncio
async def test_an_undeclared_body_is_cut_off_at_the_cap(monkeypatch):
    body = json.dumps({"query": "x" * (2 * CAP)}).encode()
    status, payload, read = await _call(_app(monkeypatch), "/api/documents/search", body, declared=False)
    assert status == 413
    assert CAP < read < len(body)  # stopped at the first piece past the cap


@pytest.mark.asyncio
async def test_a_body_route_reads_everything_first():
    # Negative control: FastAPI's Body(...) consumes the whole body before
    # the handler can refuse it.
    app = FastAPI()

    @app.post("/old")
    async def old(payload: dict = Body(...)):  # noqa: B008 - the shape under test
        return {"ok": True}

    body = _too_big()
    status, _payload, read = await _call(app, "/old", body, declared=False)
    assert status == 200 and read == len(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("body, code", [
    (b"{not json", "invalid_json"),
    (b"[1, 2, 3]", "invalid_json"),
    (b"[" * 200_000 + b"]" * 200_000, "invalid_json"),  # too deep for the parser
], ids=["malformed", "not-an-object", "too-deep"])
async def test_bad_json_is_a_422_never_a_crash(monkeypatch, body, code):
    status, payload, _read = await _call(_app(monkeypatch), "/api/documents/search", body)
    assert status == 422 and payload["detail"]["code"] == code


@pytest.mark.asyncio
async def test_a_normal_body_still_reaches_the_route(monkeypatch):
    status, payload, _read = await _call(
        _app(monkeypatch), "/api/documents/search", json.dumps({"query": "contract"}).encode()
    )
    assert status != 413 and status != 422, payload


def test_every_json_route_reads_through_the_bounded_reader():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(documents_api))
    body_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) == "Body"]
    readers = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and getattr(n.func, "id", None) == "Depends"
               and n.args and getattr(n.args[0], "id", None) in {"_json_body", "_optional_json_body"}]
    assert body_calls == []
    # 2026-10-13 (DOC-A4): the delete route read ``await request.json()``,
    # which this gate did not look for: no route reads a body any other way.
    assert _unbounded_reads(tree) == []
    assert len(readers) == 11


def _unbounded_reads(tree) -> list[str]:
    import ast

    return [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr in {"json", "body", "form"}
            and getattr(n.func.value, "id", None) == "request"]


def test_the_gate_sees_a_route_reading_request_json():
    # Negative control: the delete route's old body read.
    import ast

    old = "async def delete_document(request):\n    body = await request.json()\n"
    assert _unbounded_reads(ast.parse(old)) == ["json"]


@pytest.mark.asyncio
async def test_the_delete_route_refuses_a_large_body_unread(monkeypatch):
    status, payload, read = await _call(_app(monkeypatch), "/api/documents/d1/delete", _too_big())
    assert status == 413 and payload["detail"]["code"] == "request_too_large"
    assert read == 0


class Payload(BaseModel):
    """A body model for the negative control (module level: this module's
    postponed annotations resolve against its globals)."""

    text: str


def _routes_fastapi_reads_a_body_for(router) -> list[str]:
    """Routes whose body FastAPI itself reads and parses before the handler
    runs: a pydantic model, a dict or a Body() parameter -- what the source
    gate above cannot see when it is a plain annotation."""
    return sorted(
        f"{','.join(sorted(route.methods or []))} {route.path}"
        for route in router.routes
        if getattr(route, "dependant", None) is not None and _body_params(route.dependant)
    )


def _body_params(dependant) -> list:
    found = list(dependant.body_params)
    for sub in dependant.dependencies:
        found += _body_params(sub)
    return found


def test_fastapi_reads_no_document_route_s_body():
    """Asked of FastAPI, not of the source: every document route's body goes
    through the bounded readers (Q04 defensive check C9, 2026-10-15)."""
    assert _routes_fastapi_reads_a_body_for(documents_api.create_documents_router()) == []

    # Negative control: a route taking a model or a dict is seen.
    from fastapi import APIRouter

    planted = APIRouter()

    @planted.post("/api/documents/planted-model")
    async def _model(payload: Payload):  # noqa: ARG001
        return {}

    @planted.post("/api/documents/planted-dict")
    async def _mapping(payload: dict):  # noqa: ARG001
        return {}

    assert _routes_fastapi_reads_a_body_for(planted) == [
        "POST /api/documents/planted-dict", "POST /api/documents/planted-model",
    ]
