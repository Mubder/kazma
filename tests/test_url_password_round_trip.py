"""A password inside a provider, profile or connector URL: masked on the way
out, kept on an unchanged save, never carried to a changed URL.

KNOWN_GAPS (2026-09-25): these three displays masked the API key but returned
a password inside ``base_url`` / a webhook URL in the clear, and could not
simply mask it, because their saves restored a masked ``api_key`` but not a
masked URL -- the stars would have been stored as the password. Closed
2026-09-26 by ``restore_masked_url`` (kazma_core.security.url_credentials):
the stored URL comes back only when the posted value is EXACTLY its masked
form; stars with any other change are refused (400), so a stored password is
never moved to a host someone just typed in.
"""

from __future__ import annotations

import pytest

SECRET = "s3cret-pw"
URL = f"https://svc:{SECRET}@llm.example.com/v1"
MASKED = "https://svc:****@llm.example.com/v1"


# ── the rule ─────────────────────────────────────────────────────────────


def test_an_unchanged_masked_url_is_the_stored_url() -> None:
    from kazma_core.security.url_credentials import restore_masked_url

    assert restore_masked_url(MASKED, URL) == URL


@pytest.mark.parametrize("posted", [
    "https://svc:****@evil.example.com/v1",   # another host
    "https://other:****@llm.example.com/v1",  # another user
    "https://svc:****@llm.example.com/v2",    # another path
])
def test_a_changed_url_never_gets_the_stored_password(posted) -> None:
    from kazma_core.security.url_credentials import restore_masked_url

    assert restore_masked_url(posted, URL) == posted


def test_nothing_to_restore_leaves_the_value_alone() -> None:
    from kazma_core.security.url_credentials import restore_masked_url

    assert restore_masked_url("https://llm.example.com/v1", URL) == "https://llm.example.com/v1"
    assert restore_masked_url(MASKED, "https://llm.example.com/v1") == MASKED  # nothing stored
    assert restore_masked_url(f"https://svc:new@llm.example.com/v1", URL).endswith("new@llm.example.com/v1")


# ── the three routes ─────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import initialize_model_registry, reset_model_registry
    from kazma_ui.providers import create_providers_router

    cs = ConfigStore(db_path=str(tmp_path / "url_round_trip.db"))
    initialize_model_registry(cs)
    app = FastAPI()
    app.include_router(create_providers_router(cs))
    yield TestClient(app), cs
    reset_model_registry()


def _stored_provider_url(name: str) -> str:
    from kazma_core.model_registry import get_model_registry

    return (get_model_registry().get_provider(name) or {}).get("base_url", "")


def test_provider_url_round_trip(client) -> None:
    c, _ = client
    assert c.post("/api/providers", json={"name": "rt", "base_url": URL, "api_key": "k-123456"}).status_code == 200
    shown = next(p for p in c.get("/api/providers").json() if p["name"] == "rt")
    assert SECRET not in str(shown) and shown["base_url"] == MASKED
    # The form posts back what it showed: the password stays.
    assert c.post("/api/providers", json={**shown, "name": "rt"}).status_code == 200
    assert _stored_provider_url("rt") == URL
    # Stars with a new host: refused, and nothing changes.
    r = c.post("/api/providers", json={**shown, "name": "rt",
                                       "base_url": "https://svc:****@evil.example.com/v1"})
    assert r.status_code == 400 and "masked" in r.json()["detail"]
    assert _stored_provider_url("rt") == URL
    # A new real password is simply saved.
    new = "https://svc:rotated@llm.example.com/v1"
    assert c.post("/api/providers", json={**shown, "name": "rt", "base_url": new}).status_code == 200
    assert _stored_provider_url("rt") == new


def test_profile_url_round_trip(client) -> None:
    from kazma_core.model_registry import get_model_registry

    c, _ = client
    body = {"name": "prof", "base_url": URL, "api_key": "k-123456", "model": "m", "provider": "custom"}
    assert c.post("/api/models/profiles", json=body).status_code == 200
    shown = next(p for p in c.get("/api/models/profiles").json() if p.get("name") == "prof")
    assert SECRET not in str(shown) and shown["base_url"] == MASKED
    assert c.post("/api/models/profiles", json={**body, "api_key": "***", "base_url": MASKED}).status_code == 200
    assert get_model_registry().get_model_profile("prof")["base_url"] == URL
    r = c.post("/api/models/profiles", json={**body, "base_url": "https://svc:****@evil.example.com/v1"})
    assert r.status_code == 400
    assert get_model_registry().get_model_profile("prof")["base_url"] == URL


def test_connector_url_round_trip(client) -> None:
    c, cs = client
    hook = f"https://hook:{SECRET}@hooks.example.com/in"
    body = {"name": "slack", "token": "xoxb-123456", "enabled": True, "extras": {"webhook_url": hook}}
    assert c.post("/api/connectors", json=body).status_code == 200
    shown = next(e for e in c.get("/api/connectors").json() if e["name"] == "slack")
    assert SECRET not in str(shown)
    masked_hook = "https://hook:****@hooks.example.com/in"
    assert c.post("/api/connectors", json={**body, "token": "****3456",
                                           "extras": {"webhook_url": masked_hook}}).status_code == 200
    assert cs.get("connectors.slack.webhook_url") == hook
    r = c.post("/api/connectors", json={**body, "extras": {"webhook_url": "https://hook:****@evil.example.com/in"}})
    assert r.status_code == 400
    assert cs.get("connectors.slack.webhook_url") == hook


def test_negative_control_without_the_restore_the_stars_would_be_stored(client, monkeypatch) -> None:
    """With restore_masked_url a no-op, the unchanged save is refused rather
    than storing the stars -- the refusal is the floor under the restore."""
    import kazma_ui.providers as providers

    c, _ = client
    assert c.post("/api/providers", json={"name": "nc", "base_url": URL, "api_key": "k-123456"}).status_code == 200
    monkeypatch.setattr(providers, "restore_masked_url", lambda posted, stored: posted)
    r = c.post("/api/providers", json={"name": "nc", "base_url": MASKED, "api_key": "****3456"})
    assert r.status_code == 400
    assert _stored_provider_url("nc") == URL
