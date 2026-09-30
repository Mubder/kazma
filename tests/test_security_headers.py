"""Every response carries the security headers (audit 2026-09-30, AUD-018).

The pages sent no Content-Security-Policy, no framing rule, no nosniff and no
referrer policy (live `curl -D - /login`), while the markdown renderer loaded
images from any host a model reply named -- a prompt injection could make the
browser send chat data out by rendering. The renderer now links such images
(tests/js/test_markdown_images.js); this is the header layer.
"""

from __future__ import annotations

import mimetypes

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kazma_ui.security_headers import CSP, SecurityHeadersMiddleware, pin_static_types


def _check(headers) -> None:
    csp = headers.get("content-security-policy", "")
    assert "img-src 'self' data: blob:" in csp, csp
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp, csp
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("referrer-policy") == "same-origin"


@pytest.fixture(scope="module")
def app_client():
    from kazma_ui.app import create_app

    app = create_app()
    with TestClient(app) as client:
        yield app, client


def test_pages_and_static_files_carry_the_headers(app_client) -> None:
    _, client = app_client
    _check(client.get("/health/live").headers)
    _check(client.get("/login").headers)
    js = client.get("/static/js/streaming.js")
    assert js.status_code == 200
    assert js.headers["content-type"].startswith("application/javascript"), js.headers["content-type"]
    _check(js.headers)


def test_the_forwarded_headers_layer_stays_outermost(app_client) -> None:
    """AGENTS §26A: it records the TCP peer before anything else runs."""
    app, _ = app_client
    names = [getattr(m.cls, "__name__", str(m.cls)) for m in app.user_middleware]
    assert names[0] == "ForwardedHeadersMiddleware", names
    assert "SecurityHeadersMiddleware" in names, names


def _tiny(with_middleware: bool) -> TestClient:
    async def plain(request):
        return PlainTextResponse("ok")

    async def own_policy(request):
        return Response("x", headers={"Content-Security-Policy": "default-src 'none'; sandbox"})

    async def stream(request):
        async def chunks():
            yield b"data: 1\n\n"
            yield b"data: 2\n\n"
        return StreamingResponse(chunks(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/p", plain), Route("/own", own_policy), Route("/s", stream)])
    if with_middleware:
        app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


def test_a_route_that_sets_its_own_policy_keeps_it_and_streams_pass_through() -> None:
    client = _tiny(True)
    _check(client.get("/p").headers)
    own = client.get("/own").headers
    assert own["content-security-policy"] == "default-src 'none'; sandbox"
    assert own.get("x-content-type-options") == "nosniff"
    streamed = client.get("/s")
    assert streamed.text == "data: 1\n\ndata: 2\n\n"
    _check(streamed.headers)


def test_negative_control_without_the_middleware_no_policy() -> None:
    headers = _tiny(False).get("/p").headers
    assert "content-security-policy" not in headers and "x-frame-options" not in headers


def test_scripts_keep_their_type_whatever_the_registry_says() -> None:
    """Windows reads MIME types from the registry; some machines map .js to
    text/plain, and under nosniff the browser then refuses every script."""
    mimetypes.add_type("text/plain", ".js")
    assert mimetypes.guess_type("x.js")[0] == "text/plain", "the instrument set the broken mapping"
    pin_static_types()
    assert mimetypes.guess_type("x.js")[0] == "application/javascript"
    assert mimetypes.guess_type("x.css")[0] == "text/css"


def test_the_policy_restricts_only_what_the_pages_use() -> None:
    """Scripts and styles are left alone on purpose (Alpine evaluates with
    new Function; templates use inline handlers)."""
    assert "script-src" not in CSP and "style-src" not in CSP and "default-src" not in CSP
