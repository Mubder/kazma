"""No GET route answers a provider's credentials (2026-11-15).

Found by the defensive check before the 2026-11 security review:
``ModelRegistry.list_unified_options`` returned ``list_providers()`` as it
is, and two routes handed it to the browser -- ``GET
/api/settings/models/options`` and ``GET /api/swarm/models``, the second open
to any signed-in user. Every provider's API key went out decrypted, with the
password in its address. The Providers API masked the same rows; the options
did not.

Now one masker (``kazma_core.security.provider_mask``) serves both. The walk
below seeds a provider whose every secret field holds a sentinel and calls
every GET ``/api`` route the built app serves (no path parameter, no stream);
no answer holds a sentinel, so a new route that shows provider rows is
checked the day it is added. The registry's raw options are the negative
control.
"""

from __future__ import annotations

import re
import socket
from typing import Any

import pytest
from fastapi.testclient import TestClient

SECRET = "provider-walk-test-secret"
SENTINEL = "SENTINELPROV7c1e"
_LOCAL = {"127.0.0.1", "localhost", "::1", "testserver"}

#: GET routes this walk does not call, each with why.
NOT_WALKED: dict[str, str] = {
    "/api/settings/system/updates": "asks GitHub for the newest release; it answers versions, no setting",
}


def _provider() -> dict[str, Any]:
    return {
        "name": "walkprov",
        "base_url": f"https://user:{SENTINEL}-pw@llm.example.invalid/v1",
        "api_key": f"{SENTINEL}-key",
        "secret_headers": {"X-Extra": f"{SENTINEL}-header"},
        "api_keys": [{"id": "k2", "label": "spare", "api_key": f"{SENTINEL}-spare"}],
        "enabled": True,
        "models": ["walk-model"],
    }


@pytest.fixture()
def client(monkeypatch):
    from kazma_core.model_registry import get_model_registry, reset_model_registry
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_SECRET", SECRET)
    reset_model_registry()
    registry = get_model_registry()
    registry.upsert_provider(_provider())
    assert any(str(p.get("api_key") or "").startswith(SENTINEL) for p in registry.list_providers())

    real_connect = socket.create_connection
    real_resolve = socket.getaddrinfo

    def local_only(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        if str(address[0]) not in _LOCAL:
            raise OSError(f"no network in this test: {address[0]}")
        return real_connect(address, *args, **kwargs)

    def local_names_only(host: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and str(name) not in _LOCAL:
            raise socket.gaierror(socket.EAI_NONAME, f"no network in this test: {name}")
        return real_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", local_only)
    monkeypatch.setattr(socket, "getaddrinfo", local_names_only)
    from kazma_ui.app import create_app

    with TestClient(create_app(), headers={"X-Kazma-Secret": SECRET},
                    raise_server_exceptions=False) as started:
        yield started
    finish_turn_memory()
    registry.delete_provider("walkprov") if hasattr(registry, "delete_provider") else None
    reset_model_registry()


def _get_routes(app) -> list[str]:
    from tests.test_api_route_callers import _all_routes

    return sorted({r.path for r in _all_routes(app.routes)
                   if getattr(r, "path", "").startswith("/api") and "{" not in r.path
                   and "stream" not in r.path
                   and "GET" in (getattr(r, "methods", None) or set())})


def test_no_get_route_answers_a_provider_secret(client):
    paths = _get_routes(client.app)
    assert "/api/swarm/models" in paths and "/api/settings/models/options" in paths
    leaks = []
    for path in paths:
        if path in NOT_WALKED:
            continue
        resp = client.get(path)
        if SENTINEL in resp.text:
            leaks.append(f"{path} ({resp.status_code}): " + ", ".join(sorted(set(
                re.findall(SENTINEL + r"-[a-z]+", resp.text)))))
    assert not leaks, "a GET route answers a provider's secret:\n  " + "\n  ".join(leaks)


def test_the_options_still_name_the_provider_and_its_models(client):
    body = client.get("/api/swarm/models").json()
    entry = next(e for e in body["provider_entries"] if e.get("name") == "walkprov")
    assert entry["api_key"].startswith("****") and entry["base_url"].startswith("https://")
    assert "walkprov" in body["providers"]


def test_negative_control_the_unmasked_rows_hold_every_secret():
    """The rows ``list_unified_options`` returned before: the raw registry list."""
    from kazma_core.security.provider_mask import mask_provider_entry

    raw = str(_provider())
    for field in ("key", "pw", "header", "spare"):
        assert f"{SENTINEL}-{field}" in raw
    assert SENTINEL not in str(mask_provider_entry(_provider()))
