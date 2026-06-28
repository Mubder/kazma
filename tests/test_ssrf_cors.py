"""Tests for SSRF protection (VAL-SEC-002) and CORS middleware (VAL-SEC-003).

SSRF tests cover:
  - Literal private IPs (10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x, ::1, fc00::/7)
  - Cloud metadata endpoint 169.254.169.254
  - Hostnames that resolve to private IPs (via getaddrinfo patching)
  - localhost / 0.0.0.0 / .local / .internal hostnames
  - Non-http(s) schemes
  - Public IPs / hostnames are allowed

CORS tests cover:
  - Default origins allow localhost:8000 and 127.0.0.1:8000
  - Arbitrary origins are NOT reflected in Access-Control-Allow-Origin
  - KAZMA_CORS_ORIGINS env var overrides defaults
  - Preflight (OPTIONS) requests receive correct CORS headers
"""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_core.security.ssrf import SSRFError, is_url_safe, validate_url

# ═══════════════════════════════════════════════════════════════════
# Helper: patch getaddrinfo so hostnames "resolve" to chosen IPs
# ═══════════════════════════════════════════════════════════════════


def _make_getaddrinfo(ips: list[str]):
    """Return a fake getaddrinfo that maps any host to *ips*."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in ips
            if ":" not in ip
        ] + [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))
            for ip in ips
            if ":" in ip
        ]

    return fake_getaddrinfo


# ═══════════════════════════════════════════════════════════════════
# 1. Literal private IPv4 addresses
# ═══════════════════════════════════════════════════════════════════


class TestLiteralPrivateIPv4:
    """Direct literal-IP URLs in private ranges must be blocked."""

    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.1",
            "192.168.0.100",
            "127.0.0.1",
            "127.255.255.255",
            "169.254.169.254",  # cloud metadata
            "169.254.1.1",  # link-local
            "0.0.0.0",
            "224.0.0.1",  # multicast
        ],
    )
    def test_blocked(self, ip):
        with pytest.raises(SSRFError):
            validate_url(f"http://{ip}/")


# ═══════════════════════════════════════════════════════════════════
# 2. Literal public IPv4 addresses are allowed
# ═══════════════════════════════════════════════════════════════════


class TestLiteralPublicIPv4:
    @pytest.mark.parametrize(
        "ip",
        ["93.184.216.34", "1.1.1.1", "8.8.8.8"],
    )
    def test_allowed(self, ip):
        validate_url(f"http://{ip}/")  # must not raise


# ═══════════════════════════════════════════════════════════════════
# 3. 172.15.x and 172.32.x are NOT private (boundary check)
# ═══════════════════════════════════════════════════════════════════


class TestPrivateRangeBoundaries:
    @pytest.mark.parametrize("octet", ["15", "32", "33"])
    def test_172_outside_private_range_allowed(self, octet):
        ip = f"172.{octet}.0.1"
        # These should be public, so no exception
        validate_url(f"http://{ip}/")


# ═══════════════════════════════════════════════════════════════════
# 4. IPv6 private addresses
# ═══════════════════════════════════════════════════════════════════


class TestIPv6Private:
    @pytest.mark.parametrize(
        "ip",
        [
            "::1",  # loopback
            "fe80::1",  # link-local
            "fc00::1",  # unique-local fc00::/7
            "fd00::1",  # unique-local fc00::/7
            "ff02::1",  # multicast
        ],
    )
    def test_blocked(self, ip):
        with pytest.raises(SSRFError):
            validate_url(f"http://[{ip}]/")


# ═══════════════════════════════════════════════════════════════════
# 5. Special hostnames blocked
# ═══════════════════════════════════════════════════════════════════


class TestSpecialHostnames:
    @pytest.mark.parametrize(
        "host",
        ["localhost", "0.0.0.0"],
    )
    def test_blocked(self, host):
        with pytest.raises(SSRFError):
            validate_url(f"http://{host}/")

    @pytest.mark.parametrize(
        "host",
        ["myhost.local", "service.internal", "x.y.local", "a.b.internal"],
    )
    def test_local_internal_suffix_blocked(self, host):
        with pytest.raises(SSRFError):
            validate_url(f"http://{host}/")


# ═══════════════════════════════════════════════════════════════════
# 6. Non-http(s) schemes blocked
# ═══════════════════════════════════════════════════════════════════


class TestSchemeBlocking:
    @pytest.mark.parametrize(
        "scheme,url",
        [
            ("file", "file:///etc/passwd"),
            ("ftp", "ftp://example.com/file"),
            ("gopher", "gopher://localhost/"),
            ("dict", "dict://localhost:11211/"),
            ("data", "data:text/html,<h1>hi</h1>"),
            ("", "//169.254.169.254/"),
        ],
    )
    def test_blocked(self, scheme, url):
        with pytest.raises(SSRFError):
            validate_url(url)


# ═══════════════════════════════════════════════════════════════════
# 7. DNS resolution: hostname resolves to private IP
# ═══════════════════════════════════════════════════════════════════


class TestDNSResolutionBlocking:
    """Hostnames that resolve to private IPs must be blocked."""

    def test_hostname_resolves_to_private_blocked(self):
        fake = _make_getaddrinfo(["10.0.0.5"])
        with patch("socket.getaddrinfo", side_effect=fake):
            with pytest.raises(SSRFError, match="private"):
                validate_url("http://internal-server.example.com/")

    def test_hostname_resolves_to_metadata_blocked(self):
        fake = _make_getaddrinfo(["169.254.169.254"])
        with patch("socket.getaddrinfo", side_effect=fake):
            with pytest.raises(SSRFError):
                validate_url("http://metadata.attacker.com/")

    def test_hostname_resolves_to_loopback_blocked(self):
        fake = _make_getaddrinfo(["127.0.0.1"])
        with patch("socket.getaddrinfo", side_effect=fake):
            with pytest.raises(SSRFError):
                validate_url("http://loopback.attacker.com/")

    def test_hostname_resolves_to_public_allowed(self):
        fake = _make_getaddrinfo(["93.184.216.34"])
        with patch("socket.getaddrinfo", side_effect=fake):
            validate_url("https://example.com/")  # must not raise

    def test_mixed_public_and_private_blocked(self):
        """DNS-rebinding: first IP public, second private -> blocked."""
        fake = _make_getaddrinfo(["93.184.216.34", "10.0.0.1"])
        with patch("socket.getaddrinfo", side_effect=fake):
            with pytest.raises(SSRFError):
                validate_url("http://rebinding.attacker.com/")

    def test_unresolved_host_allowed_by_default(self):
        """An unresolvable host should be allowed (the HTTP client will fail)."""
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("no host")):
            validate_url("http://this-host-does-not-exist.invalid/")  # no raise

    def test_unresolved_host_blocked_when_configured(self):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("no host")):
            with pytest.raises(SSRFError, match="could not be resolved"):
                validate_url(
                    "http://this-host-does-not-exist.invalid/",
                    block_unresolved=True,
                )


# ═══════════════════════════════════════════════════════════════════
# 8. is_url_safe helper
# ═══════════════════════════════════════════════════════════════════


class TestIsUrlSafe:
    def test_private_returns_false(self):
        assert is_url_safe("http://10.0.0.1/") is False

    def test_metadata_returns_false(self):
        assert is_url_safe("http://169.254.169.254/") is False

    def test_public_returns_true(self):
        assert is_url_safe("http://93.184.216.34/") is True

    def test_localhost_returns_false(self):
        assert is_url_safe("http://localhost:8000/") is False

    def test_invalid_scheme_returns_false(self):
        assert is_url_safe("file:///etc/passwd") is False


# ═══════════════════════════════════════════════════════════════════
# 9. Empty / invalid URLs
# ═══════════════════════════════════════════════════════════════════


class TestEmptyAndInvalid:
    def test_empty_url_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_url("")

    def test_whitespace_url_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_url("   ")

    def test_no_hostname_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_url("http://")


# ═══════════════════════════════════════════════════════════════════
# 10. Integration: read_url tool blocks SSRF
# ═══════════════════════════════════════════════════════════════════


class TestReadUrlSSRFIntegration:
    """read_url must reject SSRF attempts before any network fetch."""

    @pytest.mark.asyncio
    async def test_read_url_blocks_private_ip(self):
        from kazma_core.tools.read_url import read_url

        result = await read_url("http://10.0.0.1/secret")
        assert result.startswith("Error:")
        assert "Blocked" in result or "private" in result.lower()

    @pytest.mark.asyncio
    async def test_read_url_blocks_metadata(self):
        from kazma_core.tools.read_url import read_url

        result = await read_url("http://169.254.169.254/latest/meta-data/")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_read_url_blocks_localhost(self):
        from kazma_core.tools.read_url import read_url

        result = await read_url("http://localhost:8080/admin")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_read_url_blocks_loopback(self):
        from kazma_core.tools.read_url import read_url

        result = await read_url("http://127.0.0.1/admin")
        assert result.startswith("Error:")

    @pytest.mark.asyncio
    async def test_read_url_allows_public(self):
        """Public URLs must still work (fetch mocked)."""
        html = "<html><body><p>Hello</p></body></html>"
        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            from kazma_core.tools.read_url import read_url

            result = await read_url("https://example.com")

        assert "Hello" in result


# ═══════════════════════════════════════════════════════════════════
# 11. Integration: vision_analyze _is_safe_url / _download_image
# ═══════════════════════════════════════════════════════════════════


class TestVisionAnalyzeSSRFIntegration:
    def test_is_safe_url_rejects_private_ip(self):
        from kazma_core.tools.vision_analyze import _is_safe_url

        assert _is_safe_url("http://10.0.0.1/img.png") is False

    def test_is_safe_url_rejects_metadata(self):
        from kazma_core.tools.vision_analyze import _is_safe_url

        assert _is_safe_url("http://169.254.169.254/img.png") is False

    def test_is_safe_url_rejects_localhost(self):
        from kazma_core.tools.vision_analyze import _is_safe_url

        assert _is_safe_url("http://localhost/img.png") is False

    def test_is_safe_url_rejects_non_http(self):
        from kazma_core.tools.vision_analyze import _is_safe_url

        assert _is_safe_url("file:///etc/passwd") is False

    def test_is_safe_url_allows_public_ip(self):
        from kazma_core.tools.vision_analyze import _is_safe_url

        assert _is_safe_url("http://93.184.216.34/img.png") is True

    def test_is_safe_url_blocks_hostname_resolving_private(self):
        from kazma_core.tools.vision_analyze import _is_safe_url

        fake = _make_getaddrinfo(["10.0.0.5"])
        with patch("socket.getaddrinfo", side_effect=fake):
            assert _is_safe_url("http://internal.example.com/img.png") is False

    @pytest.mark.asyncio
    async def test_download_image_blocks_private_url(self):
        from kazma_core.tools.vision_analyze import _download_image

        with pytest.raises(ValueError, match="unsafe|Blocked"):
            await _download_image("http://10.0.0.1/secret.png")

    @pytest.mark.asyncio
    async def test_download_image_blocks_metadata_url(self):
        from kazma_core.tools.vision_analyze import _download_image

        with pytest.raises(ValueError, match="unsafe|Blocked"):
            await _download_image("http://169.254.169.254/meta.png")


# ═══════════════════════════════════════════════════════════════════
# 12. CORS Middleware (VAL-SEC-003)
# ═══════════════════════════════════════════════════════════════════


class TestCORSMiddleware:
    """CORS headers must only be present for allowed origins."""

    def test_default_origins_allow_localhost(self):
        """A request from http://localhost:8000 gets CORS allow headers."""
        from kazma_ui.app import create_app
        from starlette.testclient import TestClient

        # Ensure KAZMA_CORS_ORIGINS is unset to test defaults
        with patch.dict("os.environ", {}, clear=False):
            import os

            old = os.environ.pop("KAZMA_CORS_ORIGINS", None)
            try:
                app = create_app()
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.options(
                        "/health",
                        headers={
                            "Origin": "http://localhost:8000",
                            "Access-Control-Request-Method": "GET",
                        },
                    )
                    assert resp.status_code in (200, 204)
                    assert (
                        resp.headers.get("access-control-allow-origin")
                        == "http://localhost:8000"
                    )
            finally:
                if old is not None:
                    os.environ["KAZMA_CORS_ORIGINS"] = old

    def test_default_origins_allow_127(self):
        from kazma_ui.app import create_app
        from starlette.testclient import TestClient

        with patch.dict("os.environ", {}, clear=False):
            import os

            old = os.environ.pop("KAZMA_CORS_ORIGINS", None)
            try:
                app = create_app()
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.options(
                        "/health",
                        headers={
                            "Origin": "http://127.0.0.1:8000",
                            "Access-Control-Request-Method": "GET",
                        },
                    )
                    assert resp.status_code in (200, 204)
                    assert (
                        resp.headers.get("access-control-allow-origin")
                        == "http://127.0.0.1:8000"
                    )
            finally:
                if old is not None:
                    os.environ["KAZMA_CORS_ORIGINS"] = old

    def test_arbitrary_origin_blocked(self):
        """A request from an arbitrary domain must NOT get CORS allow headers."""
        from kazma_ui.app import create_app
        from starlette.testclient import TestClient

        with patch.dict("os.environ", {}, clear=False):
            import os

            old = os.environ.pop("KAZMA_CORS_ORIGINS", None)
            try:
                app = create_app()
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.options(
                        "/health",
                        headers={
                            "Origin": "http://evil.example.com",
                            "Access-Control-Request-Method": "GET",
                        },
                    )
                    # CORS middleware does not set allow-origin for disallowed
                    allow_origin = resp.headers.get("access-control-allow-origin")
                    assert allow_origin != "http://evil.example.com"
            finally:
                if old is not None:
                    os.environ["KAZMA_CORS_ORIGINS"] = old

    def test_env_var_override(self):
        """KAZMA_CORS_ORIGINS env var overrides defaults."""
        from kazma_ui.app import create_app
        from starlette.testclient import TestClient

        custom = "https://myapp.com,https://staging.myapp.com"
        with patch.dict("os.environ", {"KAZMA_CORS_ORIGINS": custom}):
            app = create_app()
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.options(
                    "/health",
                    headers={
                        "Origin": "https://myapp.com",
                        "Access-Control-Request-Method": "POST",
                    },
                )
                assert resp.status_code in (200, 204)
                assert (
                    resp.headers.get("access-control-allow-origin")
                    == "https://myapp.com"
                )

    def test_env_var_override_blocks_default(self):
        """When env override is set, default localhost origin is no longer allowed."""
        from kazma_ui.app import create_app
        from starlette.testclient import TestClient

        custom = "https://myapp.com"
        with patch.dict("os.environ", {"KAZMA_CORS_ORIGINS": custom}):
            app = create_app()
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.options(
                    "/health",
                    headers={
                        "Origin": "http://localhost:8000",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                allow_origin = resp.headers.get("access-control-allow-origin")
                assert allow_origin != "http://localhost:8000"

    def test_allowed_methods_in_preflight(self):
        """Preflight response must advertise allowed methods."""
        from kazma_ui.app import create_app
        from starlette.testclient import TestClient

        with patch.dict("os.environ", {}, clear=False):
            import os

            old = os.environ.pop("KAZMA_CORS_ORIGINS", None)
            try:
                app = create_app()
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.options(
                        "/health",
                        headers={
                            "Origin": "http://localhost:8000",
                            "Access-Control-Request-Method": "DELETE",
                        },
                    )
                    allow_methods = resp.headers.get("access-control-allow-methods", "")
                    for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                        assert method in allow_methods
            finally:
                if old is not None:
                    os.environ["KAZMA_CORS_ORIGINS"] = old
