"""Tests for the KAZMA_SECRET authentication middleware (VAL-SEC-001).

Covers:
  - Unit tests for helpers (is_sensitive_path, verify_secret, require_kazma_secret)
  - Integration tests via FastAPI TestClient covering all sensitive prefixes
  - Backward compatibility when KAZMA_SECRET is unset
  - Read-only endpoints always remain open
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from kazma_ui.auth import (
    SECRET_COOKIE,
    SECRET_HEADER,
    SENSITIVE_PREFIXES,
    create_auth_middleware,
    get_kazma_secret,
    is_always_open,
    is_sensitive_path,
    require_kazma_secret,
    verify_secret,
)

# ══════════════════════════════════════════════════════════════════════════
# Test App Factory — mounts sensitive + open endpoints for integration tests
# ══════════════════════════════════════════════════════════════════════════


def _build_test_app() -> FastAPI:
    """Create a minimal FastAPI app with representative sensitive and open routes."""
    app = FastAPI()

    # Apply the auth middleware under test.
    app.middleware("http")(create_auth_middleware())

    # --- Auth bootstrap (always open) — mirrors production cookie issue ---
    @app.get("/login")
    async def login_page() -> dict:
        return {"ok": True}

    @app.post("/api/auth/login")
    async def auth_login(request: Request) -> Response:
        from kazma_ui.auth import SECRET_COOKIE, get_kazma_secret, verify_secret

        expected = get_kazma_secret()
        if not expected:
            return JSONResponse({"status": "ok", "authenticated": True})
        try:
            body = await request.json()
        except Exception:
            body = {}
        secret = str((body or {}).get("secret") or "")
        if not verify_secret(secret, expected):
            return JSONResponse({"detail": "Invalid secret"}, status_code=401)
        resp = JSONResponse({"status": "ok", "authenticated": True})
        resp.set_cookie(SECRET_COOKIE, expected, httponly=True, samesite="strict", path="/")
        return resp

    @app.post("/api/auth/logout")
    async def auth_logout() -> Response:
        from kazma_ui.auth import SECRET_COOKIE

        resp = JSONResponse({"status": "ok"})
        resp.delete_cookie(SECRET_COOKIE, path="/")
        return resp

    @app.get("/api/auth/status")
    async def auth_status() -> dict:
        return {"ok": True}

    # --- Sensitive endpoints (one per prefix) ---
    @app.get("/api/settings")
    async def get_settings() -> dict:
        return {"ok": True}

    @app.get("/api/swarm/status")
    async def swarm_status() -> dict:
        return {"ok": True}

    @app.post("/api/swarm/dispatch")
    async def swarm_dispatch() -> dict:
        return {"ok": True}

    @app.get("/api/mcp/servers")
    async def mcp_servers() -> dict:
        return {"ok": True}

    @app.get("/api/skills")
    async def skills_list() -> dict:
        return {"ok": True}

    @app.get("/api/models")
    async def models_list() -> dict:
        return {"ok": True}

    @app.get("/api/ollama/check")
    async def ollama_check() -> dict:
        return {"ok": True}

    # --- Read-only / page routes (always open) ---
    @app.get("/")
    async def root() -> dict:
        return {"ok": True}

    @app.get("/api/status")
    async def status_endpoint() -> dict:
        return {"ok": True}

    @app.get("/api/telemetry")
    async def telemetry() -> dict:
        return {"ok": True}

    # A non-sensitive API route (not in SENSITIVE_PREFIXES)
    @app.get("/api/public/info")
    async def public_info() -> dict:
        return {"ok": True}

    @app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
        from kazma_ui.auth import get_kazma_secret, SECRET_COOKIE
        expected = get_kazma_secret()
        if expected:
            provided = websocket.headers.get("x-kazma-secret", "")
            if not provided:
                provided = websocket.cookies.get(SECRET_COOKIE, "")
            import hmac as _hmac

            if not provided or not _hmac.compare_digest(provided, expected):
                await websocket.close(code=4003, reason="Unauthorized")
                return
        await websocket.accept()
        await websocket.send_text("connected")
        await websocket.close()

    return app


# ══════════════════════════════════════════════════════════════════════════
# Unit Tests — Helpers
# ══════════════════════════════════════════════════════════════════════════


class TestIsSensitivePath:
    """Test path classification logic."""

    @pytest.mark.parametrize("prefix", list(SENSITIVE_PREFIXES))
    def test_exact_prefix_is_sensitive(self, prefix: str):
        assert is_sensitive_path(prefix) is True

    @pytest.mark.parametrize("prefix", list(SENSITIVE_PREFIXES))
    def test_subpath_is_sensitive(self, prefix: str):
        assert is_sensitive_path(f"{prefix}/deeply/nested/endpoint") is True

    @pytest.mark.parametrize("prefix", list(SENSITIVE_PREFIXES))
    def test_prefix_with_trailing_slash_is_sensitive(self, prefix: str):
        assert is_sensitive_path(f"{prefix}/") is True

    def test_root_is_not_sensitive(self):
        assert is_sensitive_path("/") is False

    def test_status_is_not_sensitive(self):
        assert is_sensitive_path("/api/status") is False

    def test_unrelated_api_is_not_sensitive(self):
        assert is_sensitive_path("/api/telemetry") is False
        assert is_sensitive_path("/api/something-random") is False

    def test_chat_stream_is_sensitive(self):
        """/api/chat/* is a sensitive prefix (chat can invoke tools/models)."""
        assert is_sensitive_path("/api/chat/stream") is True

    def test_partial_match_not_sensitive(self):
        """A path like /api/mod should NOT match /api/models prefix."""
        assert is_sensitive_path("/api/mod") is False
        assert is_sensitive_path("/api/setting") is False


class TestIsAlwaysOpen:
    """Test the always-open path set."""

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/api/status",
            "/api/telemetry",
            "/health",
            "/login",
            "/api/auth/login",
            "/api/auth/logout",
            "/api/auth/status",
        ],
    )
    def test_known_open_paths(self, path: str):
        assert is_always_open(path) is True

    def test_sensitive_path_not_in_open_set(self):
        assert is_always_open("/api/settings") is False


class TestVerifySecret:
    """Test timing-safe comparison."""

    def test_matching_secrets(self):
        assert verify_secret("abc123", "abc123") is True

    def test_non_matching_secrets(self):
        assert verify_secret("abc123", "wrong") is False

    def test_empty_provided(self):
        assert verify_secret("", "abc123") is False

    def test_empty_expected(self):
        assert verify_secret("abc123", "") is False

    def test_both_empty(self):
        """Two empty strings are equal — caller must guard against this."""
        assert verify_secret("", "") is True


class TestGetKazmaSecret:
    """Test env var resolution."""

    def test_returns_stripped_value(self):
        with patch.dict(os.environ, {"KAZMA_SECRET": "  my-secret  "}):
            assert get_kazma_secret() == "my-secret"

    def test_returns_empty_when_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_kazma_secret() == ""


# ══════════════════════════════════════════════════════════════════════════
# Unit Tests — require_kazma_secret dependency
# ══════════════════════════════════════════════════════════════════════════


class TestRequireKazmaSecretDependency:
    """Test the FastAPI dependency variant."""

    def test_no_secret_env_allows_access(self):
        with patch.dict(os.environ, {}, clear=True):
            # Should not raise
            require_kazma_secret(x_kazma_secret="")

    def test_correct_secret_allows_access(self):
        with patch.dict(os.environ, {"KAZMA_SECRET": "test-secret"}):
            require_kazma_secret(x_kazma_secret="test-secret")

    def test_wrong_secret_raises_401(self):
        from fastapi import HTTPException

        with patch.dict(os.environ, {"KAZMA_SECRET": "test-secret"}):
            with pytest.raises(HTTPException) as exc_info:
                require_kazma_secret(x_kazma_secret="wrong")
            assert exc_info.value.status_code == 401

    def test_missing_secret_raises_401(self):
        from fastapi import HTTPException

        with patch.dict(os.environ, {"KAZMA_SECRET": "test-secret"}):
            with pytest.raises(HTTPException) as exc_info:
                require_kazma_secret(x_kazma_secret="")
            assert exc_info.value.status_code == 401


# ══════════════════════════════════════════════════════════════════════════
# Integration Tests — Middleware via TestClient
# ══════════════════════════════════════════════════════════════════════════

TEST_SECRET = "super-secret-key-123"

SENSITIVE_TEST_PATHS = [
    "/api/settings",
    "/api/swarm/status",
    "/api/swarm/dispatch",
    "/api/mcp/servers",
    "/api/skills",
    "/api/models",
    "/api/ollama/check",
]

OPEN_TEST_PATHS = [
    ("/", "GET"),
    ("/api/status", "GET"),
    ("/api/telemetry", "GET"),
    ("/api/public/info", "GET"),
]


class TestAuthMiddlewareWithSecret:
    """Tests when KAZMA_SECRET IS set (auth enabled)."""

    @pytest.fixture(autouse=True)
    def _set_secret(self):
        # TRUST_LAN off so TestClient host is not auto-authed as private LAN
        with patch.dict(os.environ, {"KAZMA_SECRET": TEST_SECRET, "KAZMA_TRUST_LAN": "0"}):
            # Rebuild app per-class so middleware captures the env.
            self.client = TestClient(_build_test_app())
            yield

    @pytest.mark.parametrize("path", SENSITIVE_TEST_PATHS)
    def test_sensitive_endpoint_401_without_header(self, path: str):
        """VAL-SEC-001: sensitive endpoints return 401 without X-Kazma-Secret header."""
        if "dispatch" in path:
            resp = self.client.post(path)
        else:
            resp = self.client.get(path)
        assert resp.status_code == 401, f"Expected 401 for {path}, got {resp.status_code}"
        assert "X-Kazma-Secret" in resp.headers.get("WWW-Authenticate", "")

    @pytest.mark.parametrize("path", SENSITIVE_TEST_PATHS)
    def test_sensitive_endpoint_200_with_correct_header(self, path: str):
        """VAL-SEC-001: sensitive endpoints return 200 with correct header."""
        headers = {SECRET_HEADER: TEST_SECRET}
        if "dispatch" in path:
            resp = self.client.post(path, headers=headers)
        else:
            resp = self.client.get(path, headers=headers)
        assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"
        assert resp.json() == {"ok": True}

    @pytest.mark.parametrize("path", SENSITIVE_TEST_PATHS)
    def test_sensitive_endpoint_401_with_wrong_header(self, path: str):
        """Wrong secret header returns 401."""
        headers = {SECRET_HEADER: "wrong-secret"}
        if "dispatch" in path:
            resp = self.client.post(path, headers=headers)
        else:
            resp = self.client.get(path, headers=headers)
        assert resp.status_code == 401

    @pytest.mark.parametrize("path,method", OPEN_TEST_PATHS)
    def test_open_endpoints_accessible_without_header(self, path: str, method: str):
        """Read-only and page routes remain open even when secret is set."""
        if method == "POST":
            resp = self.client.post(path)
        else:
            resp = self.client.get(path)
        assert resp.status_code == 200, f"Expected 200 for open {path}, got {resp.status_code}"

    def test_websocket_unauthorized_without_secret(self):
        """Websocket connection fails when secret is required but not provided."""
        with pytest.raises(Exception):
            with self.client.websocket_connect("/ws/dashboard") as ws:
                pass

    def test_login_open_without_header(self):
        """Login endpoints stay reachable without prior auth."""
        assert self.client.get("/login").status_code == 200
        assert self.client.get("/api/auth/status").status_code == 200

    def test_login_sets_cookie_and_unlocks_api(self):
        """POST /api/auth/login with correct secret sets cookie for API access."""
        bad = self.client.post("/api/auth/login", json={"secret": "nope"})
        assert bad.status_code == 401

        # Fresh client so loopback auto-cookie from open routes doesn't leak in
        with patch.dict(os.environ, {"KAZMA_SECRET": TEST_SECRET, "KAZMA_TRUST_LAN": "0"}):
            c = TestClient(_build_test_app())
            c.cookies.clear()
            # Hit settings without cookie → 401
            assert c.get("/api/settings").status_code == 401
            ok = c.post("/api/auth/login", json={"secret": TEST_SECRET})
            assert ok.status_code == 200
            assert ok.json().get("authenticated") is True
            # Cookie should unlock sensitive API
            assert c.get("/api/settings").status_code == 200
            c.post("/api/auth/logout")
            # After logout cookie cleared — may still fail depending on TestClient
            # cookie jar; force clear and re-check
            c.cookies.clear()
            assert c.get("/api/settings").status_code == 401

    def test_html_navigation_redirects_to_login(self):
        """Browser GET to sensitive page without cookie redirects to /login."""
        with patch.dict(os.environ, {"KAZMA_SECRET": TEST_SECRET, "KAZMA_TRUST_LAN": "0"}):
            app = FastAPI()
            app.middleware("http")(create_auth_middleware())

            @app.get("/dashboard")
            async def dash():
                return {"ok": True}

            c = TestClient(app, follow_redirects=False)
            resp = c.get("/dashboard", headers={"Accept": "text/html"})
            assert resp.status_code in (302, 303, 307)
            assert "/login" in resp.headers.get("location", "")

    def test_websocket_authorized_with_header(self):
        """Websocket connection succeeds when secret is provided in the headers."""
        with self.client.websocket_connect(
            "/ws/dashboard",
            headers={"x-kazma-secret": TEST_SECRET}
        ) as ws:
            data = ws.receive_text()
            assert data == "connected"

    def test_websocket_authorized_with_cookie(self):
        """Websocket connection succeeds when secret is provided in cookies (browser fallback)."""
        # Pass the secret via cookies, representing browser WebSocket behavior
        self.client.cookies.set("kazma-secret", TEST_SECRET)
        try:
            with self.client.websocket_connect("/ws/dashboard") as ws:
                data = ws.receive_text()
                assert data == "connected"
        finally:
            self.client.cookies.clear()


class TestAuthMiddlewareWithoutSecret:
    """Tests when KAZMA_SECRET is NOT set (backward-compatible open mode)."""

    @pytest.fixture(autouse=True)
    def _unset_secret(self):
        # Ensure KAZMA_SECRET is not present.
        env = {k: v for k, v in os.environ.items() if k != "KAZMA_SECRET"}
        with patch.dict(os.environ, env, clear=True):
            self.client = TestClient(_build_test_app())
            yield

    @pytest.mark.parametrize("path", SENSITIVE_TEST_PATHS)
    def test_sensitive_endpoint_open_without_env_secret(self, path: str):
        """When KAZMA_SECRET is unset, sensitive endpoints are open (backward compatible)."""
        if "dispatch" in path:
            resp = self.client.post(path)
        else:
            resp = self.client.get(path)
        assert resp.status_code == 200, f"Expected 200 for {path}, got {resp.status_code}"

    @pytest.mark.parametrize("path,method", OPEN_TEST_PATHS)
    def test_open_endpoints_still_open(self, path: str, method: str):
        if method == "POST":
            resp = self.client.post(path)
        else:
            resp = self.client.get(path)
        assert resp.status_code == 200


class TestAuthMiddlewareEdgeCases:
    """Edge case tests."""

    def test_static_secret_overrides_env(self):
        """When a static secret is passed, it takes precedence over env."""
        app = FastAPI()

        @app.get("/api/settings")
        async def settings() -> dict:
            return {"ok": True}

        # Pass a static secret different from env.
        app.middleware("http")(create_auth_middleware(secret="static-secret"))
        client = TestClient(app)

        # Env secret should NOT work because static secret is used.
        with patch.dict(os.environ, {"KAZMA_SECRET": "env-secret"}):
            resp = client.get("/api/settings", headers={SECRET_HEADER: "env-secret"})
            assert resp.status_code == 401

            resp2 = client.get("/api/settings", headers={SECRET_HEADER: "static-secret"})
            assert resp2.status_code == 200

    def test_empty_header_string_returns_401(self):
        """Empty X-Kazma-Secret header value is treated as missing."""
        app = FastAPI()

        @app.get("/api/settings")
        async def settings() -> dict:
            return {"ok": True}

        app.middleware("http")(create_auth_middleware())
        client = TestClient(app)

        with patch.dict(os.environ, {"KAZMA_SECRET": TEST_SECRET}):
            resp = client.get("/api/settings", headers={SECRET_HEADER: ""})
            assert resp.status_code == 401
