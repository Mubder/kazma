"""A client that went away is not a server error (2026-11-25).

The live install logged ``[app] Unhandled exception on POST
/api/chat/sessions``, ``code=internal_error ClientDisconnect`` with a
traceback, and uvicorn's ``Exception in ASGI application``: a page closed
while it was still sending. ``kazma_ui.client_gone.ClientGoneMiddleware`` ends
such a request quietly, and only such a request.

Measured through ``create_app()``'s whole stack with a body cut off by
``http.disconnect`` (a JSON body read by hand, a Pydantic body, a multipart
upload, a streamed document, a streamed export), and once through a real
uvicorn and a real socket. The same app without the layer, as before, is the
negative control. The gate finds every ``try`` that reads a body and catches
everything.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from anyio import EndOfStream
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from kazma_ui.client_gone import CLIENT_GONE_STATUS, ClientGoneMiddleware, _only_client_disconnect, body_cut_off
from starlette.requests import ClientDisconnect

from tests._secured_app import SECRET, secured_app

ROOT = Path(__file__).resolve().parents[1]

JSON = {"content-type": "application/json"}
MULTIPART = {"content-type": "multipart/form-data; boundary=cut"}
HALF_JSON = b'{"title": "ha'
HALF_UPLOAD = (b'--cut\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n'
               b"Content-Type: text/plain\r\n\r\nthe first half of")


@pytest.fixture
def app(monkeypatch):
    return secured_app(monkeypatch)


def _without_layer(app: Any) -> Any:
    """The app as it was before 2026-11-25."""
    app.user_middleware = [m for m in app.user_middleware if m.cls is not ClientGoneMiddleware]
    app.middleware_stack = None
    return app


async def _drive(app: Any, method: str, path: str, *, pieces: list[bytes], headers: dict[str, str] | None = None,
                 ends: bool = False) -> list[dict[str, Any]]:
    """One request whose client sends *pieces* and then leaves (*ends*: after
    the whole body). Returns what was sent back; raises what the app raises."""
    sent = [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    sent.append((b"host", b"127.0.0.1:59999"))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": method,
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "headers": sent,
        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 59999), "root_path": "",
    }
    waiting = list(pieces)
    out: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        await asyncio.sleep(0)
        if not waiting:
            return {"type": "http.disconnect"}
        piece = waiting.pop(0)
        return {"type": "http.request", "body": piece, "more_body": bool(waiting) or not ends}

    async def send(message: dict[str, Any]) -> None:
        out.append(message)

    await app(scope, receive, send)
    return out


def _loud(caplog) -> list[str]:
    """Every WARNING or worse, and every traceback, that names this request's
    ending (the app's own boot warnings are not it)."""
    found = []
    for record in caplog.records:
        text = record.getMessage()
        if record.levelno >= logging.WARNING or record.exc_info:
            found.append(f"{record.name} {record.levelname} {text}")
    return found


ROUTES = [
    ("POST", "/api/chat/sessions", HALF_JSON, JSON),  # read by read_json: the live witness
    ("POST", "/api/alerts/read", HALF_JSON, JSON),  # read_json again
    ("POST", "/api/bookmarks", HALF_JSON, JSON),  # a body FastAPI parses
    ("POST", "/api/chat/upload", HALF_UPLOAD, MULTIPART),
    ("POST", "/api/voice/stt", HALF_UPLOAD, MULTIPART),
    ("POST", "/api/import/history/preview", b"PK half", {"content-type": "application/zip"}),
]


@pytest.mark.parametrize(("method", "path", "half", "headers"), ROUTES, ids=[r[1] for r in ROUTES])
def test_a_cut_off_request_is_quiet_and_answered_nothing(app, caplog, method, path, half, headers):
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    out = asyncio.run(_drive(app, method, path, pieces=[half], headers={**headers, "x-kazma-secret": SECRET}))
    assert out == []
    assert not [line for line in _loud(caplog) if "ClientDisconnect" in line or "Unhandled" in line
                or "[error]" in line or "ASGI" in line], _loud(caplog)


def test_the_live_witness_is_wholly_quiet(app, caplog):
    caplog.set_level(logging.DEBUG)
    # The first request after a boot may warm things up; this is about the cut.
    asyncio.run(_drive(app, "GET", "/api/alerts", pieces=[b""], ends=True, headers={"x-kazma-secret": SECRET}))
    caplog.clear()
    out = asyncio.run(_drive(app, "POST", "/api/chat/sessions", pieces=[HALF_JSON],
                             headers={**JSON, "x-kazma-secret": SECRET}))
    assert out == []
    assert _loud(caplog) == []


def test_control_without_the_layer_it_is_an_unhandled_exception(app, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ClientDisconnect):
        asyncio.run(_drive(_without_layer(app), "POST", "/api/chat/sessions", pieces=[HALF_JSON],
                           headers={**JSON, "x-kazma-secret": SECRET}))
    loud = "\n".join(_loud(caplog))
    assert "Unhandled exception on POST /api/chat/sessions" in loud
    assert "code=internal_error ClientDisconnect" in loud


def test_control_without_the_layer_a_cut_upload_is_answered(app):
    out = asyncio.run(_drive(_without_layer(app), "POST", "/api/chat/upload", pieces=[HALF_UPLOAD],
                             headers={**MULTIPART, "x-kazma-secret": SECRET}))
    assert out and out[0]["status"] == 400


def test_a_whole_request_still_answers(app):
    out = asyncio.run(_drive(app, "POST", "/api/alerts/read", pieces=[b"{", b"}"], ends=True,
                             headers={**JSON, "x-kazma-secret": SECRET}))
    assert out[0]["status"] < 500
    assert out[-1]["type"] == "http.response.body"


def test_the_layer_is_outside_everything_that_could_raise_to_the_catch_all(app):
    classes = [m.cls for m in app.user_middleware]
    from kazma_ui.proxy_headers import ForwardedHeadersMiddleware

    # Only the layer that rewrites the client's address is outside it.
    assert classes[:2] == [ForwardedHeadersMiddleware, ClientGoneMiddleware]


# -- what stays loud -------------------------------------------------------------


def _small(route: Any) -> tuple[FastAPI, list[BaseException]]:
    """An app with the layer, one route and a catch-all that keeps what reaches it."""
    caught: list[BaseException] = []
    app = FastAPI()

    @app.exception_handler(Exception)
    async def catch_all(request: Request, exc: Exception) -> Any:
        from fastapi.responses import JSONResponse

        caught.append(exc)
        return JSONResponse({"error": "internal_error"}, status_code=500)

    app.add_api_route("/r", route, methods=["POST"])

    # Two layers of Starlette's own kind, as the app has: each re-raises.
    async def through(request: Request, call_next: Any) -> Any:
        return await call_next(request)

    app.middleware("http")(through)
    app.middleware("http")(through)
    app.add_middleware(ClientGoneMiddleware)
    return app, caught


async def _reads(request: Request) -> Any:
    await request.body()
    return {}


async def _fails(request: Request) -> Any:
    try:
        await request.body()
    finally:
        raise ValueError("a real failure")


async def _group(request: Request) -> Any:
    try:
        await request.body()
    except ClientDisconnect as gone:
        raise ExceptionGroup("both", [gone, ValueError("a real failure")])


async def _nested_group(request: Request) -> Any:
    try:
        await request.body()
    except ClientDisconnect as gone:
        raise ExceptionGroup("both", [gone, ExceptionGroup("in", [ValueError("a real failure")])])


async def _only_group(request: Request) -> Any:
    try:
        await request.body()
    except ClientDisconnect as gone:
        raise ExceptionGroup("one", [gone, ExceptionGroup("in", [ClientDisconnect()])])


async def _while_handling(request: Request) -> Any:
    try:
        raise ValueError("a real failure")
    except ValueError:
        await request.body()


async def _task_group(request: Request) -> Any:
    async def boom() -> None:
        raise ValueError("a real failure")

    async def read() -> None:
        try:
            await request.body()
        finally:
            await asyncio.sleep(0)

    async with asyncio.TaskGroup() as tg:
        tg.create_task(read())
        tg.create_task(boom())


async def _cancelled(request: Request) -> Any:
    try:
        await request.body()
    except ClientDisconnect:
        raise asyncio.CancelledError from None


@pytest.mark.parametrize("route", [_reads, _only_group])
def test_a_client_that_left_reaches_no_catch_all(route):
    app, caught = _small(route)
    assert asyncio.run(_drive(app, "POST", "/r", pieces=[b"half"])) == []
    assert caught == []


@pytest.mark.parametrize("route", [_fails, _group, _nested_group, _while_handling, _task_group],
                         ids=lambda r: r.__name__)
def test_a_real_failure_is_raised_on(route):
    app, caught = _small(route)
    with pytest.raises((ValueError, ExceptionGroup, ClientDisconnect)) as raised:
        asyncio.run(_drive(app, "POST", "/r", pieces=[b"half"]))
    assert caught and caught[0] is raised.value
    assert not _only_client_disconnect(raised.value)


def test_a_cancel_is_never_taken_for_a_client_that_left():
    async def bare(scope, receive, send):
        await receive()
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_drive(ClientGoneMiddleware(bare), "POST", "/r", pieces=[]))

    async def grouped(scope, receive, send):
        await receive()
        raise BaseExceptionGroup("x", [ClientDisconnect(), asyncio.CancelledError()])

    with pytest.raises(BaseExceptionGroup):
        asyncio.run(_drive(ClientGoneMiddleware(grouped), "POST", "/r", pieces=[]))


def test_a_disconnect_raised_with_the_client_still_there_is_raised_on():
    async def route(request: Request) -> Any:
        await request.body()
        raise ClientDisconnect

    app, caught = _small(route)
    with pytest.raises(ClientDisconnect):
        asyncio.run(_drive(app, "POST", "/r", pieces=[b"whole"], ends=True))
    assert len(caught) == 1


def test_a_client_that_was_served_is_not_one_that_left():
    """A server says ``http.disconnect`` too once the answer is complete: a
    body read after that is the handler's own mistake."""
    async def late(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done"})
        assert (await receive())["type"] == "http.disconnect"
        raise ClientDisconnect

    with pytest.raises(ClientDisconnect):
        asyncio.run(_drive(ClientGoneMiddleware(late), "POST", "/r", pieces=[]))


def test_a_client_that_left_while_the_answer_streamed_keeps_what_was_sent():
    async def streams(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        await receive()
        raise ClientDisconnect

    out = asyncio.run(_drive(ClientGoneMiddleware(streams), "POST", "/r", pieces=[b"x"], ends=True))
    assert [m["type"] for m in out] == ["http.response.start", "http.response.body"]


def test_only_client_disconnect_reads_the_chain():
    def chained(exc, *, cause=None, context=None):
        exc.__context__ = context
        if cause is not None:
            exc.__cause__ = cause
        return exc

    assert _only_client_disconnect(ClientDisconnect())
    assert _only_client_disconnect(chained(ClientDisconnect(), cause=EndOfStream()))
    assert not _only_client_disconnect(chained(ClientDisconnect(), cause=OSError("disk")))
    assert not _only_client_disconnect(chained(ClientDisconnect(), context=OSError("disk")))
    assert not _only_client_disconnect(EndOfStream())
    assert not _only_client_disconnect(chained(ValueError("x"), cause=ClientDisconnect()))
    assert not _only_client_disconnect(ExceptionGroup("g", [EndOfStream()]))
    ring = ClientDisconnect()
    ring.__context__ = chained(ClientDisconnect(), context=ring)
    assert _only_client_disconnect(ring)
    assert not _only_client_disconnect(chained(HTTPException(400), cause=ClientDisconnect()))


# -- nothing half stored -----------------------------------------------------------


def test_a_cut_export_leaves_no_file(monkeypatch, tmp_path):
    from kazma_ui import history_import_api

    monkeypatch.setattr(history_import_api, "_refusal", lambda: None)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(history_import_api, "_folder", lambda: uploads)
    app = FastAPI()
    app.include_router(history_import_api.create_history_import_router())
    app.add_middleware(ClientGoneMiddleware)
    assert asyncio.run(_drive(app, "POST", "/api/import/history/preview", pieces=[b"PK"])) == []
    assert list(uploads.iterdir()) == []


def test_a_cut_document_is_not_a_failed_upload(tmp_path, monkeypatch, caplog):
    """The upload's catch-all logged it as an ERROR with a traceback."""
    from tests.test_document_upload_admission import _app, _service

    svc = _service(tmp_path)
    try:
        app = _app(svc, monkeypatch)
        app.add_middleware(ClientGoneMiddleware)
        caplog.set_level(logging.DEBUG)
        caplog.clear()
        out = asyncio.run(_drive(app, "POST", "/api/documents", pieces=[b"half a doc"],
                                 headers={"content-type": "text/plain", "x-document-filename": "a.txt"}))
        assert out == [] and _loud(caplog) == []
        assert list((tmp_path / ".upload-staging").iterdir()) == []
    finally:
        svc.close()


# -- the recorders -----------------------------------------------------------------


def test_the_recorders_note_a_client_that_left_not_a_failure(monkeypatch):
    from kazma_core.spaces import scope as spaces_scope
    from kazma_ui import activity, memory_actions

    rows: list[tuple[str, int]] = []
    monkeypatch.setattr(spaces_scope, "spaces_enforced", lambda: True)
    monkeypatch.setattr(activity, "_write", lambda space, account, action, ids, status: rows.append(("act", status)))
    monkeypatch.setattr(memory_actions, "_record", lambda action, ids, status, *a: rows.append(("mem", status)))
    monkeypatch.setattr(activity, "_who", lambda scope: "someone")

    def make(reader: Any) -> FastAPI:
        app = FastAPI()
        app.add_api_route("/api/memory/x", reader, methods=["POST"])

        async def bind(request: Request, call_next: Any) -> Any:
            request.scope["kazma_space"] = "s"
            return await call_next(request)

        app.middleware("http")(bind)
        app.add_middleware(memory_actions.MemoryActionRecorder)
        app.add_middleware(activity.ActivityRecorder)
        app.add_middleware(ClientGoneMiddleware)
        return app

    async def typed(body: dict[str, Any]) -> Any:
        return {}

    async def settle(app: FastAPI) -> None:
        await _drive(app, "POST", "/api/memory/x", pieces=[HALF_JSON], headers=JSON)
        for _ in range(200):
            if any(kind == "act" for kind, _status in rows):
                return
            await asyncio.sleep(0.01)

    asyncio.run(settle(make(typed)))  # FastAPI's own 400: answered, to nobody
    assert sorted(rows) == [("act", CLIENT_GONE_STATUS), ("mem", CLIENT_GONE_STATUS)]
    rows.clear()
    asyncio.run(settle(make(_reads)))  # raised through
    assert rows == [("act", CLIENT_GONE_STATUS)]
    rows.clear()

    async def does_it_all_the_same(request: Request) -> Any:
        for _ in range(2):  # the half, then the disconnect: the route goes on
            await request.receive()
        assert body_cut_off(request.scope)
        return {"ok": True}

    asyncio.run(settle(make(does_it_all_the_same)))  # done, and noted as done
    assert sorted(rows) == [("act", 200), ("mem", 200)]


def test_body_cut_off_without_the_layer_is_false():
    assert body_cut_off({}) is False


# -- through a real server ---------------------------------------------------------


def _serve(app: Any) -> tuple[Any, threading.Thread, int]:
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None, proxy_headers=False))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve(sockets=[sock])), daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    return server, thread, port


def _cut(port: int) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(b"POST /r HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                  b"Content-Length: 4000\r\n\r\n" + HALF_JSON)
        time.sleep(0.2)


@pytest.mark.parametrize("layer", [True, False], ids=["with", "control-without"])
def test_a_closed_socket_through_uvicorn(caplog, layer):
    caplog.set_level(logging.DEBUG)
    seen = threading.Event()

    async def route(request: Request) -> Any:
        try:
            await request.body()
        finally:
            seen.set()

    app, caught = _small(route)
    if not layer:
        _without_layer(app)
    server, thread, port = _serve(app)
    try:
        _cut(port)
        assert seen.wait(10)
        time.sleep(0.3)
    finally:
        server.should_exit = True
        thread.join(10)
    logged = [r for r in caplog.records if "Exception in ASGI application" in r.getMessage()
              or "without starting response" in r.getMessage()]
    assert (bool(logged), len(caught)) == ((False, 0) if layer else (True, 1))


# -- the gate: no handler takes the disconnect for a failure of its own -------------

PACKAGES = ("kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway")
_READS = {"body", "json", "form", "stream"}

#: A ``try`` that reads a body and catches everything, with no
#: ``except ClientDisconnect: raise`` before: file -> why it may.
DECLARED = {
    "kazma-ui/kazma_ui/settings.py": "the model switch: a DEBUG line, then the refusal an empty body gets; "
                                     "nothing is stored and the layer drops the answer",
    "kazma-gateway/kazma_gateway/adapters/telegram.py": "the webhook: a 400 with nothing logged or stored, "
                                                        "dropped by the layer",
}


def _name(node: ast.expr | None) -> str:
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""


def _caught(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    if isinstance(handler.type, ast.Tuple):
        return {_name(item) for item in handler.type.elts}
    return {_name(handler.type)}


def _is_read(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name):
        return node.func.id in {"read_json", "_read_json", "_read_json_body", "_json_body", "_bounded_body",
                                "_read_backup_body"}
    return (isinstance(node.func, ast.Attribute) and node.func.attr in _READS
            and isinstance(node.func.value, ast.Name) and node.func.value.id in {"request", "req"})


def _swallowing(source: str) -> list[int]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try) or not any(_is_read(n) for stmt in node.body for n in ast.walk(stmt)):
            continue
        for handler in node.handlers:
            names = _caught(handler)
            if "ClientDisconnect" in names and any(isinstance(s, ast.Raise) for s in handler.body):
                break
            if names & {"Exception", "BaseException"}:
                found.append(handler.lineno)
    return found


def _sources() -> dict[str, str]:
    return {file.relative_to(ROOT).as_posix(): file.read_text(encoding="utf-8")
            for pkg in PACKAGES for file in sorted((ROOT / pkg).rglob("*.py"))}


def test_no_body_read_takes_the_disconnect_for_its_own_failure():
    found = {rel: lines for rel, source in _sources().items() if (lines := _swallowing(source))}
    assert set(found) == set(DECLARED), found


def test_control_a_planted_catch_all_is_found():
    planted = ("async def r(request):\n    try:\n        async for c in request.stream():\n            pass\n"
               "    except Exception as exc:\n        log(exc)\n")
    assert _swallowing(planted) == [5]
    fixed = planted.replace("    except Exception", "    except ClientDisconnect:\n        raise\n    except Exception")
    assert _swallowing(fixed) == []
