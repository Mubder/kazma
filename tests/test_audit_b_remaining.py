"""Audit B remaining: no raw-secret cookie, RBAC fail-closed, exec cwd pin."""

from __future__ import annotations

import os
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response

from kazma_ui.auth import (
    SECRET_COOKIE,
    _accept_legacy_secret_cookie,
    _mint_auth_cookie,
    extract_provided_credential,
    websocket_is_authenticated,
)


def _http_request(*, cookies: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> Request:
    raw_headers = []
    cookie = "; ".join(f"{k}={v}" for k, v in (cookies or {}).items())
    if cookie:
        raw_headers.append((b"cookie", cookie.encode("latin-1")))
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/settings",
        "raw_path": b"/api/settings",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("203.0.113.9", 9),
        "server": ("127.0.0.1", 9090),
    }
    return Request(scope)


class _FakeWS:
    def __init__(
        self,
        *,
        host: str = "203.0.113.9",
        cookies: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> None:
        self.cookies = cookies or {}
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}

        class _H(dict):
            def get(self, key, default=""):  # type: ignore[override]
                if key is None:
                    return default
                return dict.get(self, str(key).lower(), default)

        self.headers = _H(hdrs)
        self.query_params = query or {}
        self.client = type("C", (), {"host": host})()


def test_legacy_secret_cookie_rejected_when_opaque_on():
    assert _accept_legacy_secret_cookie() is False
    req = _http_request(cookies={SECRET_COOKIE: "sekrit-value"})
    assert extract_provided_credential(req) == ""


def test_mint_auth_cookie_never_writes_raw_secret():
    req = _http_request()
    resp = Response()
    _mint_auth_cookie(resp, req, "sekrit-value")
    set_cookie = resp.headers.get("set-cookie") or ""
    assert "sekrit-value" not in set_cookie
    assert "kazma-secret=" not in set_cookie.lower() or "kazma-secret=;" in set_cookie.lower()


def test_remote_ws_rejects_raw_secret_query_and_cookie():
    with patch.dict(os.environ, {"KAZMA_SECRET": "sekrit-value", "KAZMA_TRUST_LAN": "0"}):
        ws_q = _FakeWS(query={"token": "sekrit-value"})
        assert websocket_is_authenticated(ws_q) is False
        ws_c = _FakeWS(cookies={SECRET_COOKIE: "sekrit-value"})
        assert websocket_is_authenticated(ws_c) is False
        ws_h = _FakeWS(headers={"x-kazma-secret": "sekrit-value"})
        assert websocket_is_authenticated(ws_h) is True


def test_start_web_does_not_default_trust_lan():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "scripts" / "start-web.sh").read_text(
        encoding="utf-8"
    )
    assert 'KAZMA_TRUST_LAN="${KAZMA_TRUST_LAN:-0}"' in text
    assert 'KAZMA_TRUST_LAN="${KAZMA_TRUST_LAN:-1}"' not in text


def test_notifications_store_fetches_live_alerts():
    from pathlib import Path

    js = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "static"
        / "js"
        / "modules"
        / "stores.js"
    ).read_text(encoding="utf-8")
    header = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "templates"
        / "components"
        / "header.html"
    ).read_text(encoding="utf-8")
    assert "/api/alerts/recent" in js
    assert "$store.notifications.items" in header


def test_exec_cwd_resolution_failure_is_clarify_not_allow(tmp_path, monkeypatch):
    from kazma_core.safety.commitment.authorize import authorize_effect

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    with patch(
        "kazma_core.ide.workspace_scope.resolve_workspace_root",
        side_effect=RuntimeError("boom"),
    ):
        decision = authorize_effect(
            "shell_exec",
            {"command": "echo hi", "cwd": str(tmp_path)},
        )
    assert decision.decision == "clarify"
    assert "Could not verify cwd" in (decision.reason or "")


def test_rbac_exception_denies_when_multi_user_on():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.auth import SECRET_HEADER, create_auth_middleware

    app = FastAPI()
    app.middleware("http")(create_auth_middleware())

    @app.get("/api/settings")
    async def settings():
        return {"ok": True}

    with patch.dict(
        os.environ,
        {"KAZMA_SECRET": "sekrit-value", "KAZMA_MULTI_USER": "1", "KAZMA_TRUST_LAN": "0"},
    ):
        with patch(
            "kazma_core.security.platform_rbac.multi_user_enabled",
            side_effect=RuntimeError("store down"),
        ):
            client = TestClient(app)
            resp = client.get("/api/settings", headers={SECRET_HEADER: "sekrit-value"})
    assert resp.status_code == 403
    assert "RBAC" in (resp.text or "")
