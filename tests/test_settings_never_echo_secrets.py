"""No Settings read answers a stored secret (FA-206, FA-129), and an unhandled
error answers a code, never the exception (FA-222).

The 2026-11 functional audit: ``GET /api/settings`` returned the S3 access
key id in the clear (the masker's fragment list had no ``access_key``), and
``GET /api/settings/proxy`` returned the proxy password decrypted. The app's
catch-all handler returned an unhandled exception's type and text -- a
database address included.

Every GET route under ``/api/settings`` the built app serves (no path
parameter) is called with every sensitive key seeded with a sentinel; no
answer holds a sentinel. A new route is walked the day it is added.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

SECRET = "settings-walk-test-secret"
SENTINEL = "SENTINEL-7f3a9c-DO-NOT-ECHO"
SEEDED = (
    "proxy.password",
    "backups.offsite.s3.access_key",
    "backups.offsite.s3.secret_key",
    # Chat-app tokens would start their adapters (and reach the network):
    # these start nothing and are as secret.
    "connectors.github.app_private_key",
    "connectors.github.token",
    "email.imap.password",
    "embedding.api_key",
)


@pytest.fixture()
def client(monkeypatch):
    """The app started (the Settings routers mount at startup), every
    sensitive key holding a sentinel."""
    from kazma_core.config_store import get_config_store
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    store = get_config_store()
    for key in SEEDED:
        store.set(key, f"{SENTINEL}-{key}", category=key.split(".")[0])
    from kazma_ui.app import create_app

    with TestClient(create_app(), headers={"X-Kazma-Secret": SECRET},
                    raise_server_exceptions=False) as started:
        yield started
    finish_turn_memory()
    for key in SEEDED:
        store.delete(key)


#: GET routes this walk does not call, each with why.
NOT_WALKED = {
    "/api/settings/system/updates": "asks GitHub for the newest release (the suite's network is closed); "
                                    "it answers versions, no setting",
}


def _get_routes(app) -> list[str]:
    from tests.test_api_route_callers import _all_routes  # included routers, with their prefix

    return sorted({r.path for r in _all_routes(app.routes)
                   if getattr(r, "path", "").startswith("/api/settings") and "{" not in r.path
                   and "GET" in (getattr(r, "methods", None) or set())})


def test_no_settings_read_answers_a_stored_secret(client):
    paths = _get_routes(client.app)
    assert "/api/settings" in paths and "/api/settings/proxy" in paths
    leaks = []
    for path in paths:
        if path in NOT_WALKED:
            continue
        resp = client.get(path)
        if SENTINEL in resp.text:
            leaks.append(f"{path} ({resp.status_code})")
    assert not leaks, "a Settings read answers a stored secret:\n  " + "\n  ".join(leaks)


def test_the_proxy_password_round_trips_masked(client):
    from kazma_core.config_store import get_config_store

    shown = client.get("/api/settings/proxy").json()
    assert shown["password"] == "***"
    resp = client.put("/api/settings/proxy", json={**shown, "host": "proxy.example.test"})
    assert resp.status_code == 200
    assert get_config_store().get("proxy.password") == f"{SENTINEL}-proxy.password"  # kept, not "***"


def test_the_old_masker_left_the_access_key_out():
    """Negative control: the fragment list before FA-206."""
    import re

    old = ("api_key", "apikey", "token", "secret", "password", "passphrase", "passwd",
           "credential", "private_key", "authorization", "webhook", "pat", "bearer")
    pattern = re.compile(r"(?:^|[._])(?:" + "|".join(map(re.escape, old)) + r")(?:$|[._])")
    assert not pattern.search("backups.offsite.s3.access_key")
    from kazma_core.security.config_mask import key_is_secret

    assert key_is_secret("backups.offsite.s3.access_key")


def test_an_unhandled_error_answers_a_code_not_its_text(monkeypatch):
    from kazma_ui.app import create_app

    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    app = create_app()
    planted = "postgresql://kazma:hunter2@db.internal:5432/kazma"

    @app.get("/api/zz-boom")
    async def boom():
        raise RuntimeError(f"could not connect to {planted}")

    resp = TestClient(app, headers={"X-Kazma-Secret": SECRET}, raise_server_exceptions=False).get("/api/zz-boom")
    assert resp.status_code == 500
    assert "hunter2" not in resp.text and "db.internal" not in resp.text and "RuntimeError" not in resp.text
    assert "(ref: " in resp.json()["error"]
