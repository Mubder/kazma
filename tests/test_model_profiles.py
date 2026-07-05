"""Tests for the saved model profiles feature (VAL-UI-002, VAL-UI-003).

Covers:
  - SettingsManager.save_model_profile / get_saved_model_profiles / delete_model_profile
  - /api/models/saved GET endpoint (list profiles)
  - /api/models/saved POST endpoint (save profile)
  - /api/models/saved/{name} DELETE endpoint (delete profile)
  - API key masking in list responses
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def config_store(tmp_path):
    """Create a temporary ConfigStore for testing."""
    from kazma_core.config_store import ConfigStore

    db_path = str(tmp_path / "test_profiles.db")
    return ConfigStore(db_path=db_path)


@pytest.fixture
def sm(config_store):
    """Create a SettingsManager with the test ConfigStore."""
    from kazma_core.settings_manager import SettingsManager

    return SettingsManager(config_store)


@pytest.fixture
def app(config_store):
    """Create a FastAPI app with the models router wired to config_store."""
    from kazma_ui.models_route import create_models_router

    app = FastAPI()
    app.include_router(create_models_router(config_store=config_store))
    return app


@pytest.fixture
def client(app):
    """Create a TestClient for the app."""
    return TestClient(app)


# ══════════════════════════════════════════════════════════════════════
# SettingsManager unit tests
# ══════════════════════════════════════════════════════════════════════


class TestSettingsManagerModelProfiles:
    """Unit tests for SettingsManager saved model profile methods."""

    def test_save_profile(self, sm, config_store):
        """Saving a profile stores it in ConfigStore under models.saved.{name}."""
        result = sm.save_model_profile("my-gpt4", {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-secret",
            "model": "gpt-4o",
            "provider": "openai",
        })
        assert "error" not in result
        assert result["name"] == "my-gpt4"
        # api_key should be masked in the return value
        assert result["api_key"] == "***"

        # Verify it's stored in ConfigStore
        stored = config_store.get("models.saved.my-gpt4")
        assert stored is not None
        assert stored["base_url"] == "https://api.openai.com/v1"
        assert stored["api_key"] == "sk-secret"
        assert stored["model"] == "gpt-4o"
        assert stored["provider"] == "openai"

    def test_save_profile_empty_name_returns_error(self, sm):
        """Empty profile name returns an error dict."""
        result = sm.save_model_profile("", {"model": "gpt-4o"})
        assert "error" in result

    def test_save_profile_strips_whitespace(self, sm, config_store):
        """Profile name is stripped of whitespace."""
        sm.save_model_profile("  spaced  ", {"model": "gpt-4o", "base_url": "http://x"})
        stored = config_store.get("models.saved.spaced")
        assert stored is not None

    def test_save_profile_defaults_provider(self, sm, config_store):
        """Provider defaults to 'custom' when not provided."""
        sm.save_model_profile("p1", {"model": "gpt-4o", "base_url": "http://x"})
        stored = config_store.get("models.saved.p1")
        assert stored["provider"] == "custom"

    def test_save_profile_overwrites(self, sm, config_store):
        """Saving a profile with same name overwrites the previous one."""
        sm.save_model_profile("p1", {"model": "gpt-4o", "base_url": "http://a"})
        sm.save_model_profile("p1", {"model": "claude-3", "base_url": "http://b"})
        stored = config_store.get("models.saved.p1")
        assert stored["model"] == "claude-3"
        assert stored["base_url"] == "http://b"

    def test_get_saved_profiles_empty(self, sm):
        """get_saved_model_profiles returns empty list when no profiles."""
        profiles = sm.get_saved_model_profiles()
        assert profiles == []

    def test_get_saved_profiles_returns_all(self, sm):
        """get_saved_model_profiles returns all saved profiles."""
        sm.save_model_profile("p1", {"model": "gpt-4o", "api_key": "k1", "base_url": "http://a"})
        sm.save_model_profile("p2", {"model": "claude-3", "api_key": "k2", "base_url": "http://b"})
        profiles = sm.get_saved_model_profiles()
        assert len(profiles) == 2
        names = [p["name"] for p in profiles]
        assert "p1" in names
        assert "p2" in names

    def test_get_saved_profiles_masks_api_key(self, sm):
        """Returned profiles have masked api_key."""
        sm.save_model_profile("p1", {"model": "gpt-4o", "api_key": "sk-secret-key", "base_url": "http://a"})
        profiles = sm.get_saved_model_profiles()
        for p in profiles:
            assert p["api_key"] == "***"

    def test_get_saved_profiles_sorted_by_name(self, sm):
        """Profiles are sorted by name."""
        sm.save_model_profile("zebra", {"model": "z"})
        sm.save_model_profile("alpha", {"model": "a"})
        sm.save_model_profile("mango", {"model": "m"})
        profiles = sm.get_saved_model_profiles()
        names = [p["name"] for p in profiles]
        assert names == ["alpha", "mango", "zebra"]

    def test_delete_profile(self, sm, config_store):
        """Deleting a profile removes it from ConfigStore."""
        sm.save_model_profile("p1", {"model": "gpt-4o", "base_url": "http://a"})
        deleted = sm.delete_model_profile("p1")
        assert deleted is True
        assert config_store.get("models.saved.p1") is None

    def test_delete_nonexistent_profile(self, sm):
        """Deleting a non-existent profile returns False."""
        deleted = sm.delete_model_profile("nonexistent")
        assert deleted is False

    def test_get_model_profile_returns_raw(self, sm):
        """get_model_profile returns the raw profile with real api_key."""
        sm.save_model_profile("p1", {"model": "gpt-4o", "api_key": "sk-real", "base_url": "http://a"})
        profile = sm.get_model_profile("p1")
        assert profile is not None
        assert profile["api_key"] == "sk-real"

    def test_get_model_profile_not_found(self, sm):
        """get_model_profile returns None for non-existent profile."""
        assert sm.get_model_profile("nonexistent") is None

    def test_profile_ignores_non_dict_values(self, sm, config_store):
        """get_saved_model_profiles ignores non-dict values in models category."""
        # Manually insert a non-dict value in the models category
        config_store.set("models.saved.bad", "not-a-dict", category="models")
        config_store.set("models.registry", [], category="models")
        profiles = sm.get_saved_model_profiles()
        # Should not include the bad entry
        assert all(isinstance(p, dict) for p in profiles)


# ══════════════════════════════════════════════════════════════════════
# UnifiedModelRegistry unit tests
# ══════════════════════════════════════════════════════════════════════


class TestUnifiedModelRegistry:
    """Unit tests for unified provider/model options aggregation."""

    def test_unified_options_merge_sources(self, config_store):
        """Registry options include provider models, profiles, llm model, and defaults."""
        from kazma_core.model_registry import UnifiedModelRegistry

        registry = UnifiedModelRegistry(config_store)
        registry.upsert_provider({
            "name": "reg-provider",
            "models": ["provider-model"],
            "base_url": "https://provider.example/v1",
        })
        registry.save_model_profile("reg-profile", {
            "model": "profile-model",
            "provider": "profile-provider",
            "base_url": "https://profile.example/v1",
            "api_key": "secret-key",
        })
        config_store.set("llm.model", "runtime-model", category="llm")
        config_store.set("models.defaults.chat", "chat-model", category="models")

        options = registry.list_unified_options()
        assert "provider-model" in options["models"]
        assert "profile-model" in options["models"]
        assert "runtime-model" in options["models"]
        assert "chat-model" in options["models"]
        assert "reg-provider" in options["providers"]
        assert "profile-provider" in options["providers"]
        profile = next(p for p in options["profiles"] if p["name"] == "reg-profile")
        assert profile["api_key"] == "***"


# ══════════════════════════════════════════════════════════════════════
# API Endpoint tests
# ══════════════════════════════════════════════════════════════════════


class TestModelProfilesAPI:
    """Integration tests for /api/models/saved endpoints."""

    def test_list_empty(self, client):
        """GET /api/models/saved returns empty list initially."""
        resp = client.get("/api/models/saved")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_save_and_list(self, client):
        """POST then GET returns the saved profile."""
        # Save a profile
        resp = client.post("/api/models/saved", json={
            "name": "my-gpt4",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-secret",
            "model": "gpt-4o",
            "provider": "openai",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-gpt4"
        # api_key must be masked
        assert data["api_key"] == "***"

        # List should show it
        resp = client.get("/api/models/saved")
        assert resp.status_code == 200
        profiles = resp.json()
        assert len(profiles) == 1
        assert profiles[0]["name"] == "my-gpt4"
        assert profiles[0]["model"] == "gpt-4o"
        assert profiles[0]["api_key"] == "***"

    def test_save_empty_name_returns_400(self, client):
        """POST with empty name returns 400."""
        resp = client.post("/api/models/saved", json={
            "name": "",
            "model": "gpt-4o",
        })
        assert resp.status_code == 400

    def test_delete(self, client):
        """DELETE removes the profile."""
        # Save first
        client.post("/api/models/saved", json={
            "name": "to-delete",
            "model": "gpt-4o",
            "base_url": "http://x",
        })
        # Delete
        resp = client.delete("/api/models/saved/to-delete")
        assert resp.status_code == 200
        # List should be empty
        resp = client.get("/api/models/saved")
        assert resp.json() == []

    def test_delete_nonexistent_returns_404(self, client):
        """DELETE non-existent profile returns 404."""
        resp = client.delete("/api/models/saved/nonexistent")
        assert resp.status_code == 404

    def test_save_overwrites(self, client):
        """POST with same name overwrites."""
        client.post("/api/models/saved", json={
            "name": "p1",
            "model": "gpt-4o",
            "base_url": "http://a",
        })
        client.post("/api/models/saved", json={
            "name": "p1",
            "model": "claude-3",
            "base_url": "http://b",
        })
        resp = client.get("/api/models/saved")
        profiles = resp.json()
        assert len(profiles) == 1
        assert profiles[0]["model"] == "claude-3"

    def test_multiple_profiles(self, client):
        """Multiple profiles can be saved and listed."""
        for i in range(5):
            client.post("/api/models/saved", json={
                "name": f"model-{i}",
                "model": f"gpt-4o-{i}",
                "base_url": f"http://server-{i}",
            })
        resp = client.get("/api/models/saved")
        profiles = resp.json()
        assert len(profiles) == 5


# ══════════════════════════════════════════════════════════════════════
# Router without config_store (graceful degradation)
# ══════════════════════════════════════════════════════════════════════


class TestModelProfilesNoConfigStore:
    """When config_store is None, the endpoints degrade gracefully."""

    def test_list_without_config_store(self):
        from kazma_ui.models_route import create_models_router

        app = FastAPI()
        app.include_router(create_models_router())
        client = TestClient(app)

        resp = client.get("/api/models/saved")
        assert resp.status_code == 200
        assert resp.json() == []
