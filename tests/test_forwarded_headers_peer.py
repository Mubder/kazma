"""The undeclared-proxy check must see the TCP peer, not the forwarded client.

Live, the first tunnelled request of every boot since 2026-09-23 logged::

    [SECURITY] x-forwarded-for/x-forwarded-proto arrived from peer
    46.186.228.227, which is not in KAZMA_TRUSTED_PROXIES ... Peer-address
    trust is now DISABLED ... Set KAZMA_TRUSTED_PROXIES=46.186.228.227

The server listened on 127.0.0.1 only; the peer was ``cloudflared`` on
127.0.0.1, declared. uvicorn (``proxy_headers=True`` for the declared
proxies) had already replaced ``scope["client"]`` with the visitor's address,
and ``auth._peer_host`` read that. The existing tests passed fake requests
whose ``client`` was the peer, which is exactly what the real stack does not
give you -- so these run requests through the ASGI layers themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kazma_ui import auth
from kazma_ui.proxy_headers import ForwardedHeadersMiddleware

REPO_ROOT = Path(__file__).resolve().parents[1]
TUNNEL = ("127.0.0.1", 40123)  # cloudflared, on the same host
VISITOR = "46.186.228.227"
FORWARDED = {"X-Forwarded-For": VISITOR, "X-Forwarded-Proto": "https"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("KAZMA_TRUSTED_PROXIES", "KAZMA_LOOPBACK_AUTOLOGIN", "KAZMA_TRUST_LAN",
                "KAZMA_DEMO_MODE", "KAZMA_PRODUCTION"):
        monkeypatch.delenv(var, raising=False)
    auth.reset_proxy_detection()
    yield
    auth.reset_proxy_detection()


async def _probe(request: Request) -> JSONResponse:
    """What a route sees, and what the peer checks see."""
    trusted = auth._peer_trust_allowed(request)  # runs the detector
    return JSONResponse({
        "client": request.client.host if request.client else "",
        "scheme": request.url.scheme,
        "peer": auth._peer_host(request),
        "peer_trust": trusted,
    })


async def _ws_probe(websocket) -> None:
    await websocket.accept()
    await websocket.send_json({
        "client": websocket.client.host if websocket.client else "",
        "scheme": websocket.url.scheme,
        "peer": auth._peer_host(websocket),
    })
    await websocket.close()


def _probe_app() -> Starlette:
    return Starlette(routes=[Route("/probe", _probe), WebSocketRoute("/ws", _ws_probe)])


# -- the incident, reproduced, and fixed -------------------------------------


def test_the_old_topology_flags_the_tunnels_own_visitor(monkeypatch):
    """Negative control: uvicorn rewriting OUTSIDE the app is the live bug."""
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    old = ProxyHeadersMiddleware(_probe_app(), trusted_hosts=["127.0.0.1"])
    seen = TestClient(old, client=TUNNEL).get("/probe", headers=FORWARDED).json()

    assert seen["peer"] == VISITOR, "the 'peer' the check read was the visitor"
    assert auth.undeclared_proxy_detected() is True
    assert VISITOR in auth.proxy_health()["hint"], "and it advised trusting a client"


def test_the_app_applies_forwarded_headers_and_keeps_the_peer(monkeypatch):
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    app = ForwardedHeadersMiddleware(_probe_app())
    seen = TestClient(app, client=TUNNEL).get("/probe", headers=FORWARDED).json()

    # Routes see exactly what uvicorn used to give them...
    assert seen["client"] == VISITOR
    assert seen["scheme"] == "https"
    # ...and the peer checks see the peer.
    assert seen["peer"] == "127.0.0.1"
    assert auth.undeclared_proxy_detected() is False
    assert auth.proxy_health()["state"] != "undeclared_proxy"
    # The visitor came through the proxy: no peer trust for them.
    assert seen["peer_trust"] is False


def test_a_real_undeclared_proxy_is_still_caught(monkeypatch):
    """Docker bridge in front, 127.0.0.1 declared: the detector's actual job."""
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    app = ForwardedHeadersMiddleware(_probe_app())
    seen = TestClient(app, client=("172.17.0.1", 40000)).get(
        "/probe", headers=FORWARDED).json()

    assert seen["client"] == "172.17.0.1", "an undeclared peer is never rewritten"
    assert auth.undeclared_proxy_detected() is True
    assert "172.17.0.1" in auth.proxy_health()["hint"]


def test_with_no_proxy_declared_a_local_proxy_is_named_correctly():
    """The hint now names the proxy (127.0.0.1), not whoever it forwarded."""
    app = ForwardedHeadersMiddleware(_probe_app())
    seen = TestClient(app, client=TUNNEL).get("/probe", headers=FORWARDED).json()

    assert seen["client"] == "127.0.0.1" and seen["scheme"] == "http", "nothing rewritten"
    assert auth.undeclared_proxy_detected() is True
    assert "=127.0.0.1" in auth.proxy_health()["hint"]


def test_a_direct_request_is_left_alone(monkeypatch):
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    app = ForwardedHeadersMiddleware(_probe_app())
    seen = TestClient(app, client=TUNNEL).get("/probe").json()
    assert (seen["client"], seen["scheme"], seen["peer"]) == ("127.0.0.1", "http", "127.0.0.1")
    assert auth.undeclared_proxy_detected() is False


def test_websockets_get_the_same_treatment(monkeypatch):
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    app = ForwardedHeadersMiddleware(_probe_app())
    with TestClient(app, client=TUNNEL).websocket_connect("/ws", headers=FORWARDED) as ws:
        seen = ws.receive_json()
    assert (seen["client"], seen["scheme"], seen["peer"]) == (VISITOR, "wss", "127.0.0.1")


# -- wiring: the real app, and every way of serving it ------------------------


def test_the_real_app_does_not_flag_a_declared_tunnel(monkeypatch):
    """Through create_app's whole middleware stack, as the live server runs it."""
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    monkeypatch.setenv("KAZMA_SECRET", "forwarded-headers-test-secret")
    from kazma_ui.app import create_app

    app = create_app()
    assert app.user_middleware[0].cls is ForwardedHeadersMiddleware, "must be outermost"

    TestClient(app, client=TUNNEL).get("/health/live", headers=FORWARDED)
    assert auth.undeclared_proxy_detected() is False

    # Negative control through the same app: uvicorn rewriting in front of it
    # (proxy_headers=True at the server) brings the false alarm straight back.
    auth.reset_proxy_detection()
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=["127.0.0.1"])
    TestClient(wrapped, client=TUNNEL).get("/health/live", headers=FORWARDED)
    assert auth.undeclared_proxy_detected() is True


_UVICORN_LAUNCH = re.compile(r"\buvicorn\.(run|Config)\(")
_PROXY_OFF = re.compile(r"""["']?proxy_headers["']?\s*[:=]\s*False\b""")


def _launches_leaving_proxy_headers_on(sources: dict[str, str]) -> list[str]:
    return sorted(
        rel for rel, text in sources.items()
        if _UVICORN_LAUNCH.search(text) and not _PROXY_OFF.search(text)
    )


def test_every_server_launch_leaves_forwarded_headers_to_the_app():
    sources = {}
    for path in REPO_ROOT.rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith((".venv/", "tests/", "node_modules/")) or "/tests/" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Code, not prose: a docstring that mentions uvicorn.run( is not a launch.
        if _UVICORN_LAUNCH.search(text):
            sources[rel] = text
    launches = [rel for rel in sources if rel != "kazma-core/kazma_core/eventloop.py"]
    assert {"serve.py", "kazma-ui/kazma_ui/app.py", "kazma-cli/kazma_cli/main.py"} <= set(launches)
    offenders = _launches_leaving_proxy_headers_on({r: sources[r] for r in launches})
    assert not offenders, (
        "uvicorn rewrites the client address before the app can see the TCP peer "
        "(its default trusts 127.0.0.1). Pass proxy_headers=False; the app applies "
        "KAZMA_TRUSTED_PROXIES itself (kazma_ui.proxy_headers):\n  " + "\n  ".join(offenders)
    )


_CLI_LAUNCH = re.compile(r"""uvicorn["',\s]+kazma_ui""")


def test_every_image_launch_leaves_forwarded_headers_to_the_app():
    """Dockerfiles and compose files start uvicorn from its CLI: same rule."""
    files = [*REPO_ROOT.glob("Dockerfile*"), *REPO_ROOT.glob("docker-compose*.yml"),
             *(REPO_ROOT / "deploy").glob("*.yml")]
    launching = [p for p in files if _CLI_LAUNCH.search(p.read_text(encoding="utf-8"))]
    assert launching, "the gate lost sight of the images it exists for"
    offenders = [
        p.name for p in launching
        if "--no-proxy-headers" not in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "These start uvicorn with its default --proxy-headers (trusting "
        "127.0.0.1). Add --no-proxy-headers; the app applies "
        "KAZMA_TRUSTED_PROXIES itself: " + ", ".join(offenders)
    )


def test_the_launch_gate_catches_a_default_launch():
    """Negative control (§28)."""
    assert _launches_leaving_proxy_headers_on(
        {"x.py": "uvicorn.run(app, host=h, port=p)\n"}) == ["x.py"]
    assert _launches_leaving_proxy_headers_on(
        {"x.py": "uvicorn.run(app, proxy_headers=False)\n"}) == []
    assert _launches_leaving_proxy_headers_on(
        {"x.py": 'cfg = {"proxy_headers": False}\nuvicorn.run(**cfg)\n'}) == []
    image = 'CMD ["python", "-m", "uvicorn", "kazma_ui.app:create_app", "--factory"]'
    assert _CLI_LAUNCH.search(image), "the image pattern must see a CLI launch"
