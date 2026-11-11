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

from kazma_ui import security_headers
from kazma_ui.security_headers import (
    CSP,
    SecurityHeadersMiddleware,
    _livekit_connect_sources,
    pin_static_types,
    _policy_for,
)


def _directive(csp: str, name: str) -> str:
    for part in csp.split(";"):
        if part.strip().startswith(name + " "):
            return part.strip()
    return ""


def _check(headers) -> None:
    csp = headers.get("content-security-policy", "")
    assert "img-src 'self' data: blob:" in csp, csp
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp, csp
    # Scripts: this server's, inline and Alpine's expressions; no other host
    # (2026-11-11: the LiveKit client was loaded from a CDN).
    assert _directive(csp, "script-src") == "script-src 'self' 'unsafe-inline' 'unsafe-eval'", csp
    # Connections: this server first; another host only when it is LiveKit's.
    assert _directive(csp, "connect-src").startswith("connect-src 'self'"), csp
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("referrer-policy") == "same-origin"
    for directive in (
        "style-src 'self'", "font-src 'self' data:",
        "frame-src 'none'", "worker-src 'self' blob:", "manifest-src 'self'",
    ):
        assert directive in csp, (directive, csp)
    # No window shares an opener with another site's (2026-11-11): every
    # sign-in runs in the same tab (tests/test_no_popup_sign_in.py).
    assert headers.get("cross-origin-opener-policy") == "same-origin"
    assert headers.get("cross-origin-resource-policy") == "same-origin"
    assert headers.get("permissions-policy") == "camera=(), geolocation=(), microphone=(self)"


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


def test_the_policy_names_only_this_origin() -> None:
    """Every directive the fixed policy carries is 'self' (plus data:/blob:
    the page makes, and the inline scripts and Alpine expressions the pages
    run); no host is allowed anywhere. ``connect-src`` is per response."""
    assert "http://" not in CSP and "https://" not in CSP and "*" not in CSP
    assert "default-src" not in CSP and "connect-src" not in CSP
    assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" in CSP


# ── connect-src and the LiveKit server (2026-11-11) ─────────────────────


def _http_scope(host: bytes = b"127.0.0.1:9090", scheme: str = "http") -> dict:
    return {
        "type": "http", "method": "GET", "path": "/chat", "raw_path": b"/chat", "query_string": b"",
        "headers": [(b"host", host)], "scheme": scheme, "server": ("127.0.0.1", 9090),
        "client": ("203.0.113.9", 5000), "root_path": "", "http_version": "1.1",
    }


@pytest.mark.parametrize("url, sources", [
    ("wss://lk.example.com", ("wss://lk.example.com", "https://lk.example.com")),
    ("https://lk.example.com:7443/rtc", ("wss://lk.example.com:7443", "https://lk.example.com:7443")),
    ("ws://localhost:7880", ("ws://localhost:7880", "http://localhost:7880")),
    ("wss://proj.livekit.cloud", ("wss://proj.livekit.cloud", "https://proj.livekit.cloud",
                                  "wss://*.livekit.cloud", "https://*.livekit.cloud")),
    # Nothing that could end the directive or name another, and no other scheme.
    ("wss://a;script-src *", ()), ("wss://a.com b.com", ()), ("wss://u:p@lk.example.com", ()),
    ("javascript:alert(1)", ()), ("lk.example.com", ()), ("", ()),
])
def test_the_livekit_address_becomes_its_origins_only(url: str, sources: tuple) -> None:
    assert _livekit_connect_sources(url) == sources


def test_connect_src_is_this_server_and_the_configured_livekit_server() -> None:
    plain = _policy_for(_http_scope()).decode()
    assert _directive(plain, "connect-src") == "connect-src 'self' ws://127.0.0.1:9090"
    behind_tls = _policy_for(_http_scope(b"kazma.example", "https"),
                            _livekit_connect_sources("wss://lk.example.com")).decode()
    assert _directive(behind_tls, "connect-src") == (
        "connect-src 'self' wss://kazma.example wss://lk.example.com https://lk.example.com")
    # A Host that could end the directive never reaches the policy.
    for host in (b"evil.example;script-src *", b"evil.example, *"):
        hostile = _policy_for(_http_scope(host)).decode()
        assert "evil" not in hostile and hostile.count("script-src") == 1, hostile


def test_the_livekit_server_is_read_off_the_loop_and_kept(monkeypatch) -> None:
    """The settings read runs in a thread, and once per TTL."""
    import threading

    from kazma_core.voice import livekit

    reads: list[int] = []
    loop_thread: list[int] = []

    def config():
        reads.append(threading.get_ident())
        return livekit.LiveKitConfig(enabled=True, url="wss://lk.example.com", api_key="k", api_secret="s")

    monkeypatch.setattr(livekit, "get_livekit_config", config)
    security_headers._LIVEKIT.forget()

    async def page(request):
        loop_thread.append(threading.get_ident())
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/p", page)])
    app.add_middleware(SecurityHeadersMiddleware)
    try:
        client = TestClient(app)
        first = client.get("/p").headers["content-security-policy"]
        second = client.get("/p").headers["content-security-policy"]
        assert "wss://lk.example.com https://lk.example.com" in first and first == second
        assert len(reads) == 1, "kept between responses"
        assert reads[0] not in loop_thread, "read off the loop"
        # Duplex off: no host but this server's.
        monkeypatch.setattr(livekit, "get_livekit_config", lambda: livekit.LiveKitConfig(
            enabled=False, url="wss://lk.example.com", api_key="", api_secret=""))
        security_headers._LIVEKIT.forget()
        off = client.get("/p").headers["content-security-policy"]
        assert "lk.example.com" not in off
    finally:
        security_headers._LIVEKIT.forget()


def test_negative_control_the_old_policy_had_no_script_or_connect_rule() -> None:
    """The policy before 2026-11-11 let a page load a script from any host."""
    old = (
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' data: blob:; "
        "font-src 'self' data:; frame-src 'none'; worker-src 'self' blob:; manifest-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    assert _directive(old, "script-src") == "" and _directive(old, "connect-src") == ""


def _scope(scheme: str, peer: str = "203.0.113.9", forwarded_proto: str | None = None) -> dict:
    headers = [(b"host", b"kazma.test")]
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    return {
        "type": "http", "method": "GET", "path": "/p", "raw_path": b"/p", "query_string": b"",
        "headers": headers, "scheme": scheme, "server": ("kazma.test", 443),
        "client": (peer, 5000), "root_path": "", "http_version": "1.1",
    }


async def _hsts_for(scope: dict) -> bytes | None:
    async def app(sc, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    seen: list[dict] = []

    async def send(message):
        seen.append(message)

    async def receive():
        return {"type": "http.request"}

    await SecurityHeadersMiddleware(app)(scope, receive, send)
    start = next(m for m in seen if m["type"] == "http.response.start")
    return dict(start["headers"]).get(b"strict-transport-security")


def test_hsts_only_over_https_as_auth_decides(monkeypatch) -> None:
    import asyncio

    monkeypatch.delenv("KAZMA_TRUSTED_PROXIES", raising=False)
    assert asyncio.run(_hsts_for(_scope("https"))) == b"max-age=31536000"
    # Plain http on loopback (the normal local setup): no HSTS.
    assert asyncio.run(_hsts_for(_scope("http", peer="127.0.0.1"))) is None
    # A client claiming https through a header, with no declared proxy: no HSTS.
    assert asyncio.run(_hsts_for(_scope("http", forwarded_proto="https"))) is None
    # A declared trusted proxy that says https: HSTS.
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    assert asyncio.run(_hsts_for(_scope("http", peer="127.0.0.1", forwarded_proto="https"))) == b"max-age=31536000"


def test_negative_control_the_old_policy_fails_the_new_check() -> None:
    old = (
        "img-src 'self' data: blob:; media-src 'self' data: blob:; object-src 'none'; "
        "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    assert "frame-src 'none'" not in old and "worker-src" not in old and "font-src" not in old


def test_every_uvicorn_launcher_hides_the_server_header() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for rel in ("serve.py", "kazma-ui/kazma_ui/app.py", "kazma-cli/kazma_cli/main.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "server_header=False" in text or '"server_header": False' in text, rel
    assert "--no-server-header" in (root / "Dockerfile").read_text(encoding="utf-8")
    # Negative control: the old launcher line names no such switch.
    assert "server_header" not in "uvicorn.run(app, host=h, proxy_headers=False)"
