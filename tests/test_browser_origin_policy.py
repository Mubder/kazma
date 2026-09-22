"""Origin policy checks actual ASGI mutations, including IPv6 and proxies."""

from __future__ import annotations

import pytest
from urllib.parse import urlsplit
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

from kazma_ui.browser_origins import configured_browser_origins, normalize_origin
from kazma_ui.csrf import create_csrf_middleware


@pytest.fixture(autouse=True)
def isolated_origins(monkeypatch):
    monkeypatch.delenv("KAZMA_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)


def client_for(base_url="http://localhost:9090"):
    app = FastAPI()
    app.state.mutations = 0

    @app.post("/api/mutate")
    def mutate():
        app.state.mutations += 1
        return {"mutations": app.state.mutations}

    app.middleware("http")(create_csrf_middleware())
    app.add_middleware(CORSMiddleware, allow_origins=configured_browser_origins(),
                       allow_credentials=True, allow_methods=["POST"])
    # Some Starlette TestClient versions split IPv6 network authorities at
    # the first colon. Supply the real Host header to the ASGI app while
    # keeping the in-memory client's transport authority parseable.
    origin = urlsplit(base_url)
    return TestClient(app, base_url=f"{origin.scheme}://testserver",
                      headers={"host": origin.netloc})


@pytest.mark.parametrize("base,origin,expected", [
    ("http://localhost:9090", "http://localhost:9090", 200),
    ("http://[::1]:9090", "http://[::1]:9090", 200),
    ("http://[::1]:9090", "http://[::1]:8000", 403),
    ("http://localhost:9090", "http://localhost:8000", 403),
    ("http://localhost:9090", "https://localhost:9090", 403),
    ("https://example.com", "https://example.com:443", 200),
    ("http://localhost:9090", "null", 403),
    ("http://localhost:9090", "http://localhost:9090/forged", 403),
])
def test_actual_mutation_is_gated_by_complete_origin(base, origin, expected):
    with client_for(base) as client:
        response = client.post("/api/mutate", headers={"origin": origin})
        assert response.status_code == expected
        assert client.app.state.mutations == int(expected == 200)


def test_public_proxy_origin_is_explicit_not_inferred_from_headers(monkeypatch):
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example:9443/base")
    with client_for() as client:
        assert client.post("/api/mutate", headers={"origin": "https://kazma.example:9443"}).status_code == 200
        assert client.post("/api/mutate", headers={
            "origin": "https://evil.example", "x-forwarded-host": "evil.example",
        }).status_code == 403
        assert client.post("/api/mutate", headers={"origin": "https://kazma.example"}).status_code == 403
        assert client.app.state.mutations == 1


def test_explicit_client_grants_cors_and_csrf_together(monkeypatch):
    monkeypatch.setenv("KAZMA_CORS_ORIGINS", "http://localhost:8000")
    with client_for() as client:
        response = client.post("/api/mutate", headers={"origin": "http://localhost:8000"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:8000"
        assert response.headers["access-control-allow-credentials"] == "true"


def test_referer_fallback_preserves_scheme_and_port():
    with client_for() as client:
        assert client.post("/api/mutate", headers={"referer": "http://localhost:9090/chat?a=b"}).status_code == 200
        assert client.post("/api/mutate", headers={"referer": "http://localhost:8000/chat"}).status_code == 403


@pytest.mark.parametrize("value", ["*", "null", "file:///tmp/x", "https://user@host", "https://host:bad", "https://host:0", "https://host\n", "http://[fe80::1%25eth0]"])
def test_invalid_origin_cannot_enlarge_trust(value, monkeypatch):
    assert normalize_origin(value) is None
    monkeypatch.setenv("KAZMA_CORS_ORIGINS", value)
    assert configured_browser_origins() == []
