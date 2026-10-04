"""Previously shared guide links reach the public docs, never arbitrary URLs."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_ui.auth import create_auth_middleware, is_always_open
from kazma_ui.documentation import PUBLIC_GUIDE_PATHS, router
from kazma_ui.i18n import _REQUEST_LANG, set_request_language


@pytest.mark.parametrize("language,prefix", [("en", ""), ("ar", "/ar")])
def test_legacy_guide_redirect(language: str, prefix: str) -> None:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    token = set_request_language(language)
    try:
        with TestClient(app) as client:
            response = client.get("/docs/guide/x-evaluation-dataset", follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"] == f"https://kazma.ai{prefix}/docs/x-evaluation-dataset/"
            assert client.get("/docs/guide/evil.example", follow_redirects=False).status_code == 404
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404
    finally:
        _REQUEST_LANG.reset(token)


def test_public_redirects_with_auth_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_SECRET", "test-guide-auth-secret")
    monkeypatch.delenv("KAZMA_AUTH_DISABLED", raising=False)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)
    app.middleware("http")(create_auth_middleware())
    with TestClient(app) as client:
        for path in PUBLIC_GUIDE_PATHS:
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 302
            assert response.headers["location"].startswith("https://kazma.ai/")
        for path in ("/docs", "/openapi.json", "/docs/guide/unknown", "/api/settings"):
            assert not is_always_open(path)
            assert client.get(path, follow_redirects=False).status_code in (303, 401)
