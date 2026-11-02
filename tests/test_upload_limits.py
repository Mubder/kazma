"""An upload past its route's limit is refused before its body is read.

2026-11 functional audit, FA-134. FastAPI calls a route that takes
``UploadFile = File(...)`` only after Starlette has parsed the whole
multipart body and spooled the file to disk, so the routes' own size checks
refused a 2 GB upload already written to the temporary folder.
``kazma_ui.upload_limits.UploadSizeLimitMiddleware`` answers 413 from the
declared length with nothing read, and stops a body of no declared length at
the limit.

Measured by the bytes the app pulls from the connection (the ASGI
``receive``) and whether the route ran. The app without the layer -- every
byte read, then the route's own 413 -- is the negative control. The class
gate holds :data:`UPLOAD_LIMITS` to every route that takes a file.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 10_000
BODY = 200_000
BOUNDARY = "kazmatestboundary"


def _multipart(size: int) -> bytes:
    head = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.bin\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    return head + b"x" * size + f"\r\n--{BOUNDARY}--\r\n".encode()


@pytest.fixture
def small_limits(monkeypatch):
    from kazma_ui import routes_chat_upload, upload_limits

    monkeypatch.setitem(upload_limits.UPLOAD_LIMITS, "/api/chat/upload", LIMIT)
    monkeypatch.setattr(upload_limits, "MULTIPART_ALLOWANCE", 1_000)
    monkeypatch.setattr(routes_chat_upload, "MAX_UPLOAD_BYTES", LIMIT)
    stored: list[int] = []
    monkeypatch.setattr(routes_chat_upload, "store_uploaded_attachment",
                        lambda data, name: stored.append(len(data)) or "att-1")
    return stored


def _app(with_layer: bool):
    from fastapi import FastAPI
    from kazma_ui.routes_chat_upload import router
    from kazma_ui.upload_limits import UploadSizeLimitMiddleware

    app = FastAPI()
    app.include_router(router)
    return UploadSizeLimitMiddleware(app) if with_layer else app


def _call(app, body: bytes, *, declare: bool, chunk: int = 8_192) -> tuple[int, int]:
    """POST *body* in chunks; return (status, bytes the app read)."""
    pieces = [body[i:i + chunk] for i in range(0, len(body), chunk)]
    headers = [(b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode())]
    if declare:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/api/chat/upload", "raw_path": b"/api/chat/upload",
             "query_string": b"", "headers": headers, "client": ("127.0.0.1", 5000),
             "server": ("127.0.0.1", 9090), "root_path": ""}
    read = 0
    status: list[int] = []

    async def receive():
        nonlocal read
        if not pieces:
            return {"type": "http.disconnect"}
        piece = pieces.pop(0)
        read += len(piece)
        return {"type": "http.request", "body": piece, "more_body": bool(pieces)}

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])

    asyncio.run(app(scope, receive, send))
    return status[0], read


def test_a_declared_oversized_upload_is_refused_unread(small_limits):
    status, read = _call(_app(True), _multipart(BODY), declare=True)
    assert (status, read) == (413, 0)
    assert small_limits == []


def test_an_undeclared_oversized_upload_stops_at_the_limit(small_limits):
    status, read = _call(_app(True), _multipart(BODY), declare=False)
    assert status == 413
    assert read <= LIMIT + 1_000 + 8_192, read
    assert small_limits == []


def test_an_upload_within_the_limit_is_stored(small_limits):
    status, _read = _call(_app(True), _multipart(LIMIT // 2), declare=True)
    assert status == 200 and small_limits == [LIMIT // 2]


def test_negative_control_without_the_layer_the_whole_body_is_read(small_limits):
    status, read = _call(_app(False), _multipart(BODY), declare=True)
    assert status == 413  # the route's own check, after the spool
    assert read == len(_multipart(BODY))


def _upload_routes() -> set[str]:
    """Every route path in the web packages whose endpoint takes an UploadFile."""
    paths: set[str] = set()
    for pkg in ("kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        for file in (ROOT / pkg).rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            prefix = ""
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "APIRouter"):
                    for kw in node.keywords:
                        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                            prefix = kw.value.value
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                takes_file = any("UploadFile" in ast.unparse(a.annotation)
                                 for a in fn.args.args if a.annotation is not None)
                if not takes_file:
                    continue
                for deco in fn.decorator_list:
                    if isinstance(deco, ast.Call) and deco.args and isinstance(deco.args[0], ast.Constant):
                        paths.add(prefix + deco.args[0].value)
    return paths


def test_every_route_that_takes_a_file_has_a_limit():
    from kazma_ui.upload_limits import UPLOAD_LIMITS

    routes = _upload_routes()
    assert routes, "the scan found no upload route: it is broken"
    assert routes == set(UPLOAD_LIMITS), (routes, set(UPLOAD_LIMITS))


def test_the_routes_and_the_layer_share_one_limit():
    from kazma_ui import routes_voice
    from kazma_ui.chat_attachments import MAX_UPLOAD_BYTES
    from kazma_ui.upload_limits import UPLOAD_LIMITS

    assert UPLOAD_LIMITS["/api/voice/stt"] == routes_voice._MAX_STT_BYTES
    assert UPLOAD_LIMITS["/api/chat/upload"] == MAX_UPLOAD_BYTES
