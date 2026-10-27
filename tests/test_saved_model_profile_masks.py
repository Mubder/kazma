"""AUD-105: ``POST /api/models/saved`` never stores a masked secret.

``GET /api/models/saved`` masks the key (``***``) and a URL's password
(``****``), and a form posts the profile back. Its sibling
``POST /api/models/profiles`` (same store, ``models.saved.<name>``) restored
them; this route wrote the stars over the real key and URL password (§26D).
The rule now lives in ``ModelRegistry.save_model_profile``, which both routes
call.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

KEY = "sk-real-key-1234567890"
URL = "https://lee:hunter2-pass@llm.example.com/v1"


@pytest.fixture
def config_store(tmp_path):
    from kazma_core.config_store import ConfigStore

    return ConfigStore(db_path=str(tmp_path / "profiles.db"))


@pytest.fixture
def client(config_store):
    from kazma_ui.models_route import create_models_router

    app = FastAPI()
    app.include_router(create_models_router(config_store=config_store))
    return TestClient(app)


def _stored(config_store) -> dict:
    from kazma_core.settings_manager import SettingsManager

    return SettingsManager(config_store).get_model_profile("p1") or {}


def _save_real(client) -> dict:
    r = client.post(
        "/api/models/saved",
        json={"name": "p1", "base_url": URL, "api_key": KEY, "model": "m", "provider": "custom"},
    )
    assert r.status_code == 201, r.text
    shown = next(p for p in client.get("/api/models/saved").json() if p["name"] == "p1")
    assert shown["api_key"] != KEY and "hunter2" not in shown["base_url"]
    return shown


def test_the_profile_posted_back_as_shown_keeps_its_secrets(client, config_store) -> None:
    shown = _save_real(client)
    r = client.post(
        "/api/models/saved",
        json={
            "name": "p1",
            "base_url": shown["base_url"],
            "api_key": shown["api_key"],
            "model": "m2",
            "provider": "custom",
        },
    )
    assert r.status_code == 201, r.text
    stored = _stored(config_store)
    assert stored["api_key"] == KEY
    assert stored["base_url"] == URL
    assert stored["model"] == "m2"


def test_a_masked_url_password_on_another_host_is_refused(client, config_store) -> None:
    shown = _save_real(client)
    moved = shown["base_url"].replace("llm.example.com", "attacker.example.net")
    r = client.post(
        "/api/models/saved",
        json={"name": "p1", "base_url": moved, "api_key": KEY, "model": "m"},
    )
    assert r.status_code == 400
    assert _stored(config_store)["base_url"] == URL


def test_a_masked_key_with_nothing_stored_is_refused(client, config_store) -> None:
    r = client.post(
        "/api/models/saved",
        json={"name": "p1", "base_url": "https://x.example/v1", "api_key": "***", "model": "m"},
    )
    assert r.status_code == 400
    assert _stored(config_store) == {}


def test_the_sibling_route_follows_the_same_rule(config_store, monkeypatch) -> None:
    """``/api/models/profiles`` keeps restoring, now through the registry."""
    from kazma_core.model_registry import ModelRegistry

    registry = ModelRegistry(config_store)
    registry.save_model_profile("p1", {"base_url": URL, "api_key": KEY, "model": "m"})
    masked = next(p for p in registry.list_model_profiles(mask_api_key=True) if p["name"] == "p1")
    out = registry.save_model_profile(
        "p1", {"base_url": masked["base_url"], "api_key": masked["api_key"], "model": "m"}
    )
    assert "error" not in out
    raw = registry.get_model_profile("p1")
    assert raw["api_key"] == KEY and raw["base_url"] == URL
