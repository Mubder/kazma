"""Sign-in switched off is for one machine, never for a proxy's visitors.

``KAZMA_AUTH_DISABLED`` was refused only beside a non-loopback bind. Behind
a reverse proxy on the same machine (a Cloudflare tunnel, nginx, Caddy) the
bind IS loopback, so it started cleanly and every ``/api`` route was open
to whoever reached the proxy (found 2026-10-02 while making the security
report measure exposure). A declared proxy now counts as exposure at boot,
and a proxy nobody declared is refused per request.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from kazma_core.security import boot_guard
from kazma_core.security.boot_guard import check_exposure_posture
from kazma_ui import auth
from kazma_ui.proxy_headers import ForwardedHeadersMiddleware
from starlette.testclient import TestClient

TUNNEL = ("127.0.0.1", 40123)
FORWARDED = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("KAZMA_TRUSTED_PROXIES", "KAZMA_AUTH_DISABLED", "KAZMA_DEV_WS_BYPASS",
                "KAZMA_DEMO_MODE", "KAZMA_PRODUCTION", "KAZMA_SECRET"):
        monkeypatch.delenv(var, raising=False)
    auth.reset_proxy_detection()
    yield
    auth.reset_proxy_detection()


# ── at boot ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("switch", ["KAZMA_AUTH_DISABLED", "KAZMA_DEV_WS_BYPASS"])
def test_a_declared_proxy_is_exposure(monkeypatch: pytest.MonkeyPatch, switch: str) -> None:
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")
    monkeypatch.setenv(switch, "1")
    ok, message = check_exposure_posture("127.0.0.1")
    assert ok is False
    assert switch in message and "declared reverse proxy" in message

    # Negative control: the rule before 2026-10-02 asked about the bind only.
    monkeypatch.setattr(boot_guard, "exposure", lambda host: "" if boot_guard.is_loopback(host) else host)
    assert check_exposure_posture("127.0.0.1") == (True, "")


def test_a_proxy_without_switches_starts_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1, 172.17.0.0/16")
    assert check_exposure_posture("127.0.0.1") == (True, "")
    monkeypatch.setenv("KAZMA_DEMO_MODE", "1")
    ok, message = check_exposure_posture("127.0.0.1")
    assert ok is True and "declared reverse proxy" in message


def test_local_development_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    assert check_exposure_posture("127.0.0.1") == (True, "")
    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "  ,  ")  # nothing declared
    assert check_exposure_posture("127.0.0.1") == (True, "")


# ── per request ─────────────────────────────────────────────────────────


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/api/settings/probe")
    def probe() -> dict[str, str]:
        return {"ok": "yes"}

    app.middleware("http")(auth.create_auth_middleware())
    return TestClient(ForwardedHeadersMiddleware(app), client=TUNNEL)


def test_a_proxied_request_is_refused_while_sign_in_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    client = _client()
    assert client.get("/api/settings/probe").status_code == 200, "this machine keeps working"

    refused = client.get("/api/settings/probe", headers=FORWARDED)
    assert refused.status_code == 503
    assert "KAZMA_AUTH_DISABLED" in refused.json()["detail"]

    monkeypatch.setenv("KAZMA_TRUSTED_PROXIES", "127.0.0.1")  # declared: refused even without headers
    assert client.get("/api/settings/probe").status_code == 503


def test_the_refusal_is_what_closes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: without the per-request check the proxied visitor
    reached the route with sign-in off."""
    monkeypatch.setenv("KAZMA_AUTH_DISABLED", "1")
    monkeypatch.setattr(auth, "_arrived_through_a_proxy", lambda request: False)
    assert _client().get("/api/settings/probe", headers=FORWARDED).status_code == 200
