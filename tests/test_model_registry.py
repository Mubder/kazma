"""Tests for the singleton ModelRegistry.

Covers:
  - Singleton lifecycle (initialize, get, reset)
  - Active profile management (set/get active provider/model)
  - get_client() returns configured LLMProvider and caches by provider name
  - discover_models() with mocked httpx response
  - Backward compat: list_providers, save_model_profile, etc. still work
  - Serialization round-trip
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project roots to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kazma-core"))


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def config_store(tmp_path):
    """Create a temporary ConfigStore for testing (no YAML fallback)."""
    from kazma_core.config_store import ConfigStore

    db_path = str(tmp_path / "test_registry.db")
    # Use a nonexistent yaml path so YAML fallback does not contaminate tests
    yaml_path = str(tmp_path / "nonexistent.yaml")
    return ConfigStore(db_path=db_path, yaml_path=yaml_path)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the singleton is reset before and after each test."""
    from kazma_core.model_registry import reset_model_registry

    reset_model_registry()
    yield
    reset_model_registry()


# ══════════════════════════════════════════════════════════════════════
# Singleton lifecycle
# ══════════════════════════════════════════════════════════════════════


class TestSingletonLifecycle:
    """Tests for initialize_model_registry / get_model_registry / reset_model_registry."""

    def test_initialize_returns_registry(self, config_store):
        from kazma_core.model_registry import ModelRegistry, initialize_model_registry

        registry = initialize_model_registry(config_store)
        assert isinstance(registry, ModelRegistry)

    def test_get_returns_same_instance(self, config_store):
        from kazma_core.model_registry import get_model_registry, initialize_model_registry

        first = initialize_model_registry(config_store)
        second = get_model_registry()
        assert first is second

    def test_get_before_init_raises(self):
        from kazma_core.model_registry import get_model_registry

        with pytest.raises(RuntimeError, match="not initialized"):
            get_model_registry()

    def test_reset_clears_singleton(self, config_store):
        from kazma_core.model_registry import get_model_registry, initialize_model_registry, reset_model_registry

        initialize_model_registry(config_store)
        reset_model_registry()
        with pytest.raises(RuntimeError):
            get_model_registry()

    def test_reinitialize_replaces_instance(self, config_store):
        from kazma_core.model_registry import get_model_registry, initialize_model_registry

        first = initialize_model_registry(config_store)
        second = initialize_model_registry(config_store)
        assert first is not second
        assert get_model_registry() is second


# ══════════════════════════════════════════════════════════════════════
# Active profile management
# ══════════════════════════════════════════════════════════════════════


class TestActiveProfile:
    """Tests for get_active_profile / set_active_provider / set_active_model."""

    def test_get_active_profile_empty(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        profile = registry.get_active_profile()
        assert profile["provider"] == "custom"
        assert profile["base_url"] == ""
        assert profile["model"] == ""
        assert profile["api_key"] == ""

    def test_set_active_provider(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        profile = registry.set_active_provider(
            "openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
        )
        assert profile["provider"] == "openai"
        assert profile["model"] == "gpt-4o"
        assert profile["api_key"] == "***"

    def test_set_active_provider_empty_name_returns_error(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        result = registry.set_active_provider("")
        assert "error" in result

    def test_set_active_model(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.set_active_provider("openai", base_url="https://api.openai.com/v1")
        registry.set_active_model("gpt-4o-mini")
        profile = registry.get_active_profile()
        assert profile["model"] == "gpt-4o-mini"

    def test_active_profile_persists(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.set_active_provider("openai", model="gpt-4o")

        # Re-initialize to verify persistence
        registry2 = initialize_model_registry(config_store)
        profile = registry2.get_active_profile()
        assert profile["provider"] == "openai"
        assert profile["model"] == "gpt-4o"

    def test_legacy_fallback(self, config_store):
        """When no active provider is set, falls back to llm.* keys."""
        from kazma_core.model_registry import initialize_model_registry

        config_store.set("llm.base_url", "http://localhost:1234/v1", category="llm")
        config_store.set("llm.model", "local-model", category="llm")

        registry = initialize_model_registry(config_store)
        profile = registry.get_active_profile()
        assert profile["provider"] == "custom"
        assert profile["base_url"] == "http://localhost:1234/v1"
        assert profile["model"] == "local-model"


# ══════════════════════════════════════════════════════════════════════
# LLM client management
# ══════════════════════════════════════════════════════════════════════


class TestClientManagement:
    """Tests for get_client / get_model."""

    def test_get_client_returns_provider(self, config_store):
        from kazma_core.llm_provider import LLMProvider
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.set_active_provider(
            "openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
        )
        client = registry.get_client()
        assert isinstance(client, LLMProvider)
        assert client.config.model == "gpt-4o"

    def test_get_client_caches_by_provider(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.set_active_provider(
            "openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
        )
        client1 = registry.get_client()
        client2 = registry.get_client()
        assert client1 is client2

    def test_get_client_with_model_override(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.set_active_provider(
            "openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o",
        )
        client = registry.get_client(model="gpt-4o-mini")
        assert client.config.model == "gpt-4o-mini"
        # Cached client should still be the original
        cached = registry.get_client()
        assert cached.config.model == "gpt-4o"

    def test_get_model_by_id(self, config_store):
        from kazma_core.llm_provider import LLMProvider
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({
            "name": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "models": ["gpt-4o", "gpt-4o-mini"],
        })
        client = registry.get_model("gpt-4o")
        assert isinstance(client, LLMProvider)
        assert client.config.model == "gpt-4o"

    def test_get_model_empty_raises(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        with pytest.raises(ValueError, match="model_id is required"):
            registry.get_model("")

    def test_client_invalidation_on_provider_update(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.set_active_provider(
            "openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-old",
            model="gpt-4o",
        )
        client1 = registry.get_client()

        # Update provider config should invalidate cache
        registry.upsert_provider({"name": "openai", "api_key": "sk-new"})
        client2 = registry.get_client()
        assert client1 is not client2


# ══════════════════════════════════════════════════════════════════════
# Model discovery
# ══════════════════════════════════════════════════════════════════════


class TestModelDiscovery:
    """Tests for discover_models / discover_all / get_discovered_models."""

    @pytest.mark.asyncio
    async def test_discover_models_success(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({
            "name": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "enabled": True,
        })

        # Use MagicMock for response — raise_for_status() and json() are sync
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"id": "gpt-4o", "object": "model"},
                {"id": "gpt-4o-mini", "object": "model"},
                {"id": "gpt-3.5-turbo", "object": "model"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("kazma_core.model_registry.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            models = await registry.discover_models("openai")

        assert "gpt-4o" in models
        assert "gpt-4o-mini" in models
        assert "gpt-3.5-turbo" in models
        # Verify caching
        assert registry.get_discovered_models("openai") == models

    @pytest.mark.asyncio
    async def test_discover_models_empty_provider(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        models = await registry.discover_models("")
        assert models == []

    @pytest.mark.asyncio
    async def test_discover_models_nonexistent_provider(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        models = await registry.discover_models("nonexistent")
        assert models == []

    @pytest.mark.asyncio
    async def test_discover_models_http_error(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({
            "name": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
        })

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("connection refused")

        with patch("kazma_core.model_registry.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            models = await registry.discover_models("openai")

        assert models == []

    @pytest.mark.asyncio
    async def test_discover_all_only_enabled(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({
            "name": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test",
            "enabled": True,
        })
        registry.upsert_provider({
            "name": "anthropic",
            "base_url": "https://api.anthropic.com/v1",
            "api_key": "sk-ant",
            "enabled": False,
        })

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": [{"id": "gpt-4o"}]}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch("kazma_core.model_registry.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            results = await registry.discover_all()

        assert "openai" in results
        assert "anthropic" not in results

    def test_get_discovered_models_all(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry._discovered_models = {
            "openai": ["gpt-4o", "gpt-4o-mini"],
            "anthropic": ["claude-3", "claude-3-opus"],
        }
        all_models = registry.get_discovered_models()
        assert "gpt-4o" in all_models
        assert "claude-3" in all_models
        # Should be sorted and deduplicated
        assert all_models == sorted(set(all_models))


# ══════════════════════════════════════════════════════════════════════
# Backward compatibility
# ══════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Ensure existing UnifiedModelRegistry methods still work."""

    def test_list_providers_returns_defaults(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        providers = registry.list_providers()
        assert isinstance(providers, list)
        names = [p["name"] for p in providers]
        assert "openai" in names
        assert "anthropic" in names

    def test_upsert_and_get_provider(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        result = registry.upsert_provider({
            "name": "test-provider",
            "base_url": "https://test.example.com/v1",
            "api_key": "sk-test",
            "models": ["model-a"],
        })
        assert result["name"] == "test-provider"

        found = registry.get_provider("test-provider")
        assert found is not None
        assert found["base_url"] == "https://test.example.com/v1"

    def test_delete_provider(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({"name": "to-delete", "base_url": "https://x.com/v1"})
        registry.delete_provider("to-delete")
        assert registry.get_provider("to-delete") is None

    def test_toggle_provider(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({"name": "toggle", "base_url": "https://x.com/v1", "enabled": True})
        registry.toggle_provider("toggle", False)
        provider = registry.get_provider("toggle")
        assert provider is not None
        assert provider["enabled"] is False

    def test_set_provider_health(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({"name": "health-test", "base_url": "https://x.com/v1"})
        registry.set_provider_health("health-test", "healthy")
        provider = registry.get_provider("health-test")
        assert provider is not None
        assert provider["health"] == "healthy"

    def test_save_and_list_model_profiles(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        result = registry.save_model_profile("my-profile", {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-secret",
            "model": "gpt-4o",
            "provider": "openai",
        })
        assert result["name"] == "my-profile"
        assert result["api_key"] == "***"

        profiles = registry.list_model_profiles()
        assert len(profiles) == 1
        assert profiles[0]["name"] == "my-profile"

    def test_get_model_profile_raw(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.save_model_profile("p1", {"model": "gpt-4o", "api_key": "sk-real", "base_url": "http://a"})
        profile = registry.get_model_profile("p1")
        assert profile is not None
        assert profile["api_key"] == "sk-real"

    def test_delete_model_profile(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.save_model_profile("to-delete", {"model": "gpt-4o"})
        assert registry.delete_model_profile("to-delete") is True
        assert registry.get_model_profile("to-delete") is None

    def test_list_unified_options(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.upsert_provider({
            "name": "unified-provider",
            "models": ["unified-model"],
            "base_url": "https://provider.example/v1",
        })
        registry.save_model_profile("unified-profile", {
            "model": "profile-model",
            "provider": "profile-provider",
            "base_url": "https://profile.example/v1",
        })
        config_store.set("llm.model", "runtime-model", category="llm")
        config_store.set("models.defaults.chat", "chat-model", category="models")

        options = registry.list_unified_options()
        assert "unified-model" in options["models"]
        assert "profile-model" in options["models"]
        assert "runtime-model" in options["models"]
        assert "chat-model" in options["models"]
        assert "unified-provider" in options["providers"]

    def test_backward_compat_alias(self, config_store):
        """UnifiedModelRegistry alias works."""
        from kazma_core.model_registry import UnifiedModelRegistry

        registry = UnifiedModelRegistry(config_store)
        providers = registry.list_providers()
        assert isinstance(providers, list)


# ══════════════════════════════════════════════════════════════════════
# Serialization round-trip
# ══════════════════════════════════════════════════════════════════════


class TestSerialization:
    """Tests for _deserialize / serialize round-trip."""

    def test_serialize_and_deserialize_active_profile(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry.set_active_provider("openai", model="gpt-4o")
        registry.serialize()

        # Verify stored values
        assert config_store.get("registry.active_provider") == "openai"
        assert config_store.get("registry.active_model") == "gpt-4o"

        # Re-initialize and verify deserialization
        registry2 = initialize_model_registry(config_store)
        assert registry2._active_provider == "openai"
        assert registry2._active_model == "gpt-4o"

    def test_serialize_and_deserialize_discovered_models(self, config_store):
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        registry._discovered_models = {
            "openai": ["gpt-4o", "gpt-4o-mini"],
            "anthropic": ["claude-3"],
        }
        registry.serialize()

        # Verify stored value
        stored = config_store.get("registry.discovered_models")
        assert isinstance(stored, str)
        parsed = json.loads(stored)
        assert "openai" in parsed

        # Re-initialize and verify
        registry2 = initialize_model_registry(config_store)
        assert registry2._discovered_models["openai"] == ["gpt-4o", "gpt-4o-mini"]
        assert registry2._discovered_models["anthropic"] == ["claude-3"]

    def test_deserialize_handles_corrupt_json(self, config_store):
        """Corrupt discovered_models JSON should not crash."""
        from kazma_core.model_registry import initialize_model_registry

        config_store.set("registry.discovered_models", "not-json", category="registry")
        registry = initialize_model_registry(config_store)
        assert registry._discovered_models == {}

    def test_deserialize_handles_missing_keys(self, config_store):
        """Missing keys default gracefully."""
        from kazma_core.model_registry import initialize_model_registry

        registry = initialize_model_registry(config_store)
        assert registry._active_provider == ""
        assert registry._active_model == ""
        assert registry._discovered_models == {}
