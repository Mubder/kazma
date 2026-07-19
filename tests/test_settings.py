"""Tests for the Kazma Settings system.

Tests both the SettingsManager (unit tests) and the API endpoints (integration tests).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def config_store(tmp_path):
    """Create a temporary ConfigStore for testing."""
    from kazma_core.config_store import ConfigStore
    db_path = str(tmp_path / "test_settings.db")
    return ConfigStore(db_path=db_path)


@pytest.fixture
def sm(config_store):
    """Create a SettingsManager with the test ConfigStore."""
    from kazma_core.settings_manager import SettingsManager
    return SettingsManager(config_store)


# ══════════════════════════════════════════════════════════════════════
# TestSettingsManager — Unit tests
# ══════════════════════════════════════════════════════════════════════


class TestSettingsManager:
    """Unit tests for SettingsManager."""

    def test_init(self, sm):
        """SettingsManager initializes without error."""
        assert sm is not None

    # ── Providers ──

    def test_get_providers_empty(self, sm):
        """Returns a list (possibly with default presets)."""
        providers = sm.get_all_providers()
        assert isinstance(providers, list)

    def test_add_provider(self, sm):
        """Adding a provider persists it."""
        result = sm.add_provider({
            "name": "test-provider",
            "display_name": "Test",
            "base_url": "https://test.example.com/v1",
            "api_key": "test-key",
            "models": ["model-a"],
            "enabled": True,
        })
        assert result["name"] == "test-provider"
        assert result["base_url"] == "https://test.example.com/v1"
        # Verify persistence
        providers = sm.get_all_providers()
        found = [p for p in providers if p["name"] == "test-provider"]
        assert len(found) == 1

    def test_add_provider_update_existing(self, sm):
        """Adding a provider with the same name updates it."""
        sm.add_provider({"name": "dup", "base_url": "https://old.com/v1"})
        sm.add_provider({"name": "dup", "base_url": "https://new.com/v1"})
        providers = sm.get_all_providers()
        found = [p for p in providers if p["name"] == "dup"]
        assert len(found) == 1
        assert found[0]["base_url"] == "https://new.com/v1"

    def test_delete_provider(self, sm):
        """Deleting a provider removes it."""
        sm.add_provider({"name": "to-delete", "base_url": "https://x.com/v1"})
        sm.delete_provider("to-delete")
        providers = sm.get_all_providers()
        found = [p for p in providers if p["name"] == "to-delete"]
        assert len(found) == 0

    def test_toggle_provider(self, sm):
        """Toggling a provider updates its enabled state."""
        sm.add_provider({"name": "toggle-me", "base_url": "https://x.com/v1", "enabled": True})
        sm.toggle_provider("toggle-me", False)
        providers = sm.get_all_providers()
        found = [p for p in providers if p["name"] == "toggle-me"]
        assert found[0]["enabled"] is False

    def test_provider_presets(self, sm):
        """Default providers include presets from kazma_core.providers."""
        providers = sm.get_all_providers()
        # Should have at least openai, anthropic, etc.
        names = [p["name"] for p in providers]
        assert "openai" in names
        assert "anthropic" in names

    # ── Models ──

    def test_model_registry_empty(self, sm):
        """Model registry starts empty."""
        registry = sm.get_model_registry()
        assert isinstance(registry, list)

    def test_unified_model_options(self, sm):
        """Unified options merge providers, profiles, and llm defaults."""
        sm.add_provider({
            "name": "merged-provider",
            "models": ["provider-model"],
            "base_url": "https://provider.example/v1",
        })
        sm.save_model_profile("saved-one", {
            "model": "saved-model",
            "provider": "saved-provider",
            "base_url": "https://saved.example/v1",
        })
        sm._cs.set("llm.model", "runtime-model", category="llm")
        sm.set_default_model("chat", "chat-default")

        options = sm.get_unified_model_options()
        assert "models" in options
        assert "providers" in options
        assert "profiles" in options
        assert "provider-model" in options["models"]
        assert "saved-model" in options["models"]
        assert "runtime-model" in options["models"]
        assert "chat-default" in options["models"]
        assert "merged-provider" in options["providers"]
        assert "saved-provider" in options["providers"]
        assert any(p["name"] == "saved-one" for p in options["profiles"])

    def test_set_default_model(self, sm):
        """Setting a default model persists it."""
        sm.set_default_model("chat", "gpt-4o")
        defaults = sm.get_model_defaults()
        assert defaults["chat"] == "gpt-4o"

    def test_get_model_defaults(self, sm):
        """Returns defaults for all task types."""
        defaults = sm.get_model_defaults()
        assert "chat" in defaults
        assert "code" in defaults
        assert "summarize" in defaults
        assert "translate" in defaults

    def test_model_usage_empty(self, sm):
        """Model usage starts empty."""
        usage = sm.get_model_usage()
        assert isinstance(usage, dict)

    # ── Agent ──

    def test_agent_config(self, sm):
        """Agent config has expected keys."""
        config = sm.get_agent_config()
        assert "name" in config
        assert "language" in config
        assert "system_prompt" in config
        assert "personality" in config

    def test_save_agent_config(self, sm):
        """Saving agent config persists changes."""
        sm.save_agent_config({"name": "test-agent", "language": "en"})
        config = sm.get_agent_config()
        assert config["name"] == "test-agent"
        assert config["language"] == "en"

    def test_personalities_list(self, sm):
        """Returns personality templates."""
        personalities = sm.get_personalities()
        assert isinstance(personalities, list)
        if personalities:
            assert "name" in personalities[0]
            assert "emoji" in personalities[0]

    # ── Safety ──

    def test_safety_settings(self, sm):
        """Safety settings have expected keys."""
        safety = sm.get_safety_settings()
        assert "hitl_enabled" in safety
        assert "require_approval_for" in safety
        assert "approval_timeout" in safety

    def test_save_safety_settings(self, sm):
        """Saving safety settings persists."""
        sm.save_safety_settings({"hitl_enabled": False, "approval_timeout": 120})
        safety = sm.get_safety_settings()
        assert safety["hitl_enabled"] is False
        assert safety["approval_timeout"] == 120

    # ── Context ──

    def test_context_settings(self, sm):
        """Context settings have expected keys."""
        ctx = sm.get_context_settings()
        assert "max_context_tokens" in ctx
        assert "context_strategy" in ctx

    def test_save_context_settings(self, sm):
        """Saving context settings persists."""
        sm.save_context_settings({"max_context_tokens": 64000})
        ctx = sm.get_context_settings()
        assert ctx["max_context_tokens"] == 64000

    # ── Appearance ──

    def test_appearance_defaults(self, sm):
        """Appearance has default values."""
        appearance = sm.get_appearance()
        assert appearance["theme"] == "dark"
        assert "accent_color" in appearance
        assert "font_size" in appearance

    def test_appearance_update(self, sm):
        """Updating appearance persists changes."""
        sm.save_appearance({"theme": "light", "font_size": 16})
        appearance = sm.get_appearance()
        assert appearance["theme"] == "light"
        assert appearance["font_size"] == 16

    # ── Shortcuts ──

    def test_shortcut_defaults(self, sm):
        """Shortcuts have default values."""
        shortcuts = sm.get_shortcuts()
        assert isinstance(shortcuts, dict)
        assert len(shortcuts) > 0
        assert "send_message" in shortcuts

    def test_save_shortcut(self, sm):
        """Saving a shortcut persists it."""
        sm.save_shortcut("custom_action", "Ctrl+Shift+Z")
        shortcuts = sm.get_shortcuts()
        assert shortcuts["custom_action"] == "Ctrl+Shift+Z"

    def test_shortcut_conflict_detection(self, sm):
        """Detects conflicting shortcuts."""
        shortcuts = {"action_a": "Ctrl+K", "action_b": "Ctrl+K", "action_c": "Ctrl+L"}
        conflicts = sm.detect_conflicts(shortcuts)
        assert len(conflicts) == 1
        assert conflicts[0]["keys"] == "Ctrl+K"

    def test_reset_shortcuts(self, sm):
        """Resetting shortcuts reverts to defaults."""
        sm.save_shortcut("custom", "Ctrl+X")
        sm.reset_shortcuts()
        shortcuts = sm.get_shortcuts()
        assert "custom" not in shortcuts

    # ── Export/Import ──

    def test_export_yaml(self, sm):
        """Exporting as YAML returns a string."""
        sm.save_agent_config({"name": "export-test"})
        result = sm.export_config("yaml")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_export_json(self, sm):
        """Exporting as JSON returns valid JSON."""
        sm.save_agent_config({"name": "json-test"})
        result = sm.export_config("json")
        assert isinstance(result, str)
        data = json.loads(result)
        assert isinstance(data, dict)

    def test_import_config(self, sm):
        """Importing YAML config works."""
        yaml_data = "agent:\n  name: imported\n  language: en\n"
        count = sm.import_config(yaml_data, "yaml")
        assert count > 0
        config = sm.get_agent_config()
        assert config["name"] == "imported"

    def test_import_selective(self, sm):
        """Selective import only imports specified sections."""
        yaml_data = "agent:\n  name: selective\nmodel:\n  model: gpt-4\n"
        count = sm.import_config(yaml_data, "yaml", selective=True, sections=["agent"])
        assert count >= 1

    def test_config_diff(self, sm):
        """Config diff detects additions, removals, and changes."""
        old = {"a": 1, "b": 2}
        new = {"a": 1, "b": 3, "c": 4}
        diff = sm.get_config_diff(old, new)
        assert "added" in diff
        assert "removed" in diff
        assert "changed" in diff
        assert "c" in diff["added"]
        assert "b" in diff["changed"]

    def test_reset_to_defaults(self, sm):
        """Reset clears all DB settings."""
        sm.save_agent_config({"name": "will-be-deleted"})
        count = sm.reset_to_defaults()
        assert count >= 0

    # ── Tools ──

    def test_tool_registry(self, sm):
        """Tool registry returns a list."""
        tools = sm.get_tool_registry()
        assert isinstance(tools, list)

    def test_toggle_tool(self, sm):
        """Toggling a tool persists."""
        sm.toggle_tool("test_tool", False)
        val = sm._cs.get("tools.test_tool.enabled", True)
        assert val is False

    # ── System ──

    def test_system_diagnostics(self, sm):
        """Diagnostics returns expected keys."""
        diag = sm.get_diagnostics()
        assert "uptime" in diag
        assert "python_version" in diag
        assert "os" in diag

    def test_get_logs(self, sm):
        """Getting logs returns a dict."""
        result = sm.get_logs(50)
        assert "lines" in result
        assert isinstance(result["lines"], list)

    # ── Account ──

    def test_account_info(self, sm):
        """Account info has expected keys."""
        info = sm.get_account_info()
        assert "username" in info

    def test_create_and_revoke_token(self, sm):
        """Creating and revoking API tokens works."""
        result = sm.create_api_token("test-token")
        assert "token" in result
        assert result["id"]
        tokens = sm.get_api_tokens()
        assert len(tokens) >= 1
        sm.revoke_api_token(result["id"])
        tokens = sm.get_api_tokens()
        found = [t for t in tokens if t["id"] == result["id"]]
        assert len(found) == 0

    def test_change_password(self, sm):
        """Password change works with valid new password."""
        result = sm.change_password("any-old", "new-secure-pw-123")
        assert result.get("status") == "ok"

    def test_change_password_short(self, sm):
        """Password change rejects short passwords."""
        result = sm.change_password("any-old", "short")
        assert "error" in result


# ══════════════════════════════════════════════════════════════════════
# TestSettingsAPI — FastAPI endpoint tests
# ══════════════════════════════════════════════════════════════════════


class TestSettingsAPI:
    """Integration tests for the settings API endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        """Create a FastAPI TestClient with settings router."""
        from fastapi import FastAPI
        from fastapi.templating import Jinja2Templates
        from kazma_core.config_store import ConfigStore

        db_path = str(tmp_path / "api_test.db")
        cs = ConfigStore(db_path=db_path)

        # Create minimal app with settings router
        app = FastAPI()
        templates = Jinja2Templates(directory=str(tmp_path / "templates"))
        (tmp_path / "templates").mkdir(exist_ok=True)
        (tmp_path / "templates" / "settings.html").write_text("ok")

        # Create a minimal mock agent
        mock_agent = MagicMock()
        mock_agent.config.name = "test"
        mock_agent.config.language = "ar"
        mock_agent.config.default_model = "gpt-4o-mini"
        mock_agent.llm_config.base_url = "https://api.openai.com/v1"
        mock_agent.llm_config.api_key = ""
        mock_agent.llm_config.model = "gpt-4o-mini"
        mock_agent.llm_config.max_tokens = 4096
        mock_agent.llm_config.temperature = 0.7
        mock_agent.llm_config.timeout = 60
        mock_agent.system_prompt = "You are a test assistant."

        from kazma_ui.settings import create_settings_router
        router = create_settings_router(mock_agent, cs, templates)
        app.include_router(router)

        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_get_settings(self, client):
        """GET /api/settings returns a dict."""
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_update_settings(self, client):
        """PUT /api/settings updates settings."""
        updates = [{"key": "test.key", "value": "test-value", "category": "test"}]
        resp = client.put("/api/settings", json=updates)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_provider_list(self, client):
        """GET /api/settings/providers returns a list."""
        resp = client.get("/api/settings/providers")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_provider_add(self, client):
        """POST /api/settings/providers adds a provider."""
        resp = client.post("/api/settings/providers", json={
            "name": "test-api",
            "base_url": "https://test.example.com/v1",
            "api_key": "test-key",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "test-api"

    def test_provider_delete(self, client):
        """DELETE /api/settings/providers/{name} removes a provider."""
        client.post("/api/settings/providers", json={
            "name": "to-delete",
            "base_url": "https://x.com/v1",
        })
        resp = client.delete("/api/settings/providers/to-delete")
        assert resp.status_code == 200

    def test_provider_toggle(self, client):
        """PUT /api/settings/providers/{name}/toggle toggles a provider."""
        client.post("/api/settings/providers", json={
            "name": "toggle-test",
            "base_url": "https://x.com/v1",
        })
        resp = client.put("/api/settings/providers/toggle-test/toggle", json={"enabled": False})
        assert resp.status_code == 200

    def test_model_defaults_endpoint(self, client):
        """GET /api/settings/models/defaults returns defaults."""
        resp = client.get("/api/settings/models/defaults")
        assert resp.status_code == 200
        data = resp.json()
        assert "chat" in data

    def test_model_options_endpoint(self, client):
        """GET /api/settings/models/options returns unified options."""
        client.post("/api/settings/providers", json={
            "name": "settings-provider",
            "models": ["settings-model"],
            "base_url": "https://provider.example/v1",
        })
        client.put("/api/settings/single", json={
            "key": "llm.model",
            "value": "settings-runtime-model",
            "category": "llm",
        })

        resp = client.get("/api/settings/models/options")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "providers" in data
        assert "settings-provider" in data["providers"]
        assert "settings-model" in data["models"]
        assert "settings-runtime-model" in data["models"]

    def test_set_model_default(self, client):
        """PUT /api/settings/models/defaults sets a default."""
        resp = client.put("/api/settings/models/defaults", json={
            "task_type": "chat",
            "model_name": "gpt-4o",
        })
        assert resp.status_code == 200

    def test_agent_config_update(self, client):
        """PUT /api/settings/agent updates agent config."""
        resp = client.put("/api/settings/agent", json={
            "name": "updated-agent",
            "language": "en",
        })
        assert resp.status_code == 200

    def test_personalities_endpoint(self, client):
        """GET /api/settings/agent/personalities returns a list."""
        resp = client.get("/api/settings/agent/personalities")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_safety_save(self, client):
        """PUT /api/settings/agent/safety saves safety settings."""
        resp = client.put("/api/settings/agent/safety", json={
            "hitl_enabled": True,
            "approval_timeout": 90,
        })
        assert resp.status_code == 200

    def test_connectors_get(self, client):
        """GET /api/settings/connectors returns connectors."""
        resp = client.get("/api/settings/connectors")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_connectors_save(self, client):
        """PUT /api/settings/connectors saves connector config."""
        resp = client.put("/api/settings/connectors", json={
            "platform": "telegram",
            "settings": {"token": "test-token"},
        })
        assert resp.status_code == 200

    def test_appearance_get(self, client):
        """GET /api/settings/appearance returns appearance."""
        resp = client.get("/api/settings/appearance")
        assert resp.status_code == 200
        data = resp.json()
        assert "theme" in data

    def test_appearance_save(self, client):
        """PUT /api/settings/appearance saves appearance."""
        resp = client.put("/api/settings/appearance", json={
            "theme": "light",
            "font_size": 16,
        })
        assert resp.status_code == 200

    def test_shortcuts_get(self, client):
        """GET /api/settings/shortcuts returns shortcuts."""
        resp = client.get("/api/settings/shortcuts")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_shortcuts_save(self, client):
        """PUT /api/settings/shortcuts saves a shortcut."""
        resp = client.put("/api/settings/shortcuts", json={
            "action": "test_action",
            "keys": "Ctrl+T",
        })
        assert resp.status_code == 200

    def test_tools_get(self, client):
        """GET /api/settings/tools returns a list."""
        resp = client.get("/api/settings/tools")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_mcp_get(self, client):
        """GET /api/settings/mcp returns a list."""
        resp = client.get("/api/settings/mcp")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_mcp_add(self, client):
        """POST /api/settings/mcp adds a server."""
        resp = client.post("/api/settings/mcp", json={
            "name": "test-mcp",
            "transport": "stdio",
            "command": ["echo", "hello"],
        })
        assert resp.status_code == 200

    def test_system_diagnostics(self, client):
        """GET /api/settings/system/diagnostics returns diagnostics."""
        resp = client.get("/api/settings/system/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
        assert "uptime" in data

    def test_system_logs(self, client):
        """GET /api/settings/system/logs returns logs."""
        resp = client.get("/api/settings/system/logs?lines=10")
        assert resp.status_code == 200
        assert "lines" in resp.json()

    def test_export_endpoint(self, client):
        """GET /api/settings/export returns file download."""
        resp = client.get("/api/settings/export?format=yaml")
        assert resp.status_code == 200
        assert "text/yaml" in resp.headers.get("content-type", "")

    def test_export_json_endpoint(self, client):
        """GET /api/settings/export?format=json returns JSON."""
        resp = client.get("/api/settings/export?format=json")
        assert resp.status_code == 200
        assert "application/json" in resp.headers.get("content-type", "")

    def test_import_endpoint(self, client):
        """POST /api/settings/import imports config."""
        resp = client.post("/api/settings/import", json={
            "data": "agent:\n  name: imported\n",
            "format": "yaml",
        })
        assert resp.status_code == 200
        assert int(resp.json()["imported"]) > 0

    def test_import_selective(self, client):
        """POST /api/settings/import with selective=True imports selected sections."""
        resp = client.post("/api/settings/import", json={
            "data": "agent:\n  name: selective\nmodel:\n  model: gpt-4\n",
            "format": "yaml",
            "selective": True,
            "sections": ["agent"],
        })
        assert resp.status_code == 200

    def test_reset_endpoint(self, client):
        """POST /api/settings/reset resets all settings."""
        # Add some settings first
        client.put("/api/settings", json=[
            {"key": "test.reset", "value": "yes", "category": "test"}
        ])
        resp = client.post("/api/settings/reset")
        assert resp.status_code == 200

    def test_account_sessions(self, client):
        """GET /api/settings/account/sessions returns a list."""
        resp = client.get("/api/settings/account/sessions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_account_tokens(self, client):
        """GET /api/settings/account/tokens returns a list."""
        resp = client.get("/api/settings/account/tokens")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_create_token(self, client):
        """POST /api/settings/account/tokens creates a token."""
        resp = client.post("/api/settings/account/tokens", json={"name": "test"})
        assert resp.status_code == 200
        assert "token" in resp.json()

    def test_voice_settings_get_and_put(self, client):
        """GET and PUT /api/settings/voice fetches and saves voice settings."""
        # 1. GET defaults
        resp = client.get("/api/settings/voice")
        assert resp.status_code == 200
        data = resp.json()
        assert "enabled" in data
        assert "stt_provider" in data
        assert "tts_provider" in data

        # 2. PUT updates
        payload = {
            "enabled": True,
            "stt_provider": "groq",
            "tts_provider": "kokoro",
            "tts_voice": "en-US-1",
            "stt_language": "en",
            "tts_output_format": "opus"
        }
        resp = client.put("/api/settings/voice", json=payload)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # 3. Verify GET returns updated values
        resp = client.get("/api/settings/voice")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["stt_provider"] == "groq"
        assert data["tts_provider"] == "kokoro"
        assert data["tts_voice"] == "en-US-1"
        assert data["stt_language"] == "en"
        assert data["tts_output_format"] == "opus"

    def test_voice_providers_get(self, client):
        """GET /api/voice/providers returns available STT and TTS providers."""
        resp = client.get("/api/voice/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "stt" in data
        assert "tts" in data
        assert isinstance(data["stt"], list)
        assert isinstance(data["tts"], list)

    def test_voice_voices_get(self, client):
        """GET /api/voice/voices returns voice list for a provider."""
        # 1. Test OpenAI
        resp = client.get("/api/voice/voices?provider=openai")
        assert resp.status_code == 200
        data = resp.json()
        assert "alloy" in data
        assert "shimmer" in data

        # 2. Test Nvidia
        resp = client.get("/api/voice/voices?provider=nvidia")
        assert resp.status_code == 200
        data = resp.json()
        assert "Magpie-Multilingual.EN-US.Aria" in data

        # 3. Test EdgeTTS
        resp = client.get("/api/voice/voices?provider=edgetts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        assert "default" in data




# ══════════════════════════════════════════════════════════════════════
# TestUnifiedProvidersRouterAPI — New unified providers & connectors hub
# ══════════════════════════════════════════════════════════════════════


class TestUnifiedProvidersRouterAPI:
    """Integration tests for the new /api/providers, /api/connectors, and
    /api/models/profiles endpoints in kazma_ui/providers.py.
    """

    @pytest.fixture
    def client(self, tmp_path):
        """Create a FastAPI TestClient with the unified providers router."""
        from fastapi import FastAPI
        from kazma_core.config_store import ConfigStore
        from kazma_core.model_registry import (
            initialize_model_registry,
            reset_model_registry,
        )
        from kazma_ui.providers import create_providers_router

        db_path = str(tmp_path / "providers_test.db")
        cs = ConfigStore(db_path=db_path)
        initialize_model_registry(cs)

        app = FastAPI()
        router = create_providers_router(cs)
        app.include_router(router)

        from fastapi.testclient import TestClient
        client = TestClient(app)
        yield client
        reset_model_registry()

    def test_providers_list_empty(self, client):
        """GET /api/providers returns a list of providers with masked keys."""
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # Preset providers should have empty/masked keys
        for provider in data:
            assert "api_key" in provider
            assert not provider["api_key"].startswith("sk-") or provider["api_key"] == ""

    def test_providers_add_and_mask(self, client):
        """POST /api/providers adds a provider and GET masks the API key."""
        resp = client.post("/api/providers", json={
            "name": "unified-test",
            "display_name": "Unified Test",
            "base_url": "https://test.example.com/v1",
            "api_key": "test-api-key-1234",
            "models": ["model-a"],
            "enabled": True,
        })
        assert resp.status_code == 200
        result = resp.json()
        assert result["name"] == "unified-test"
        assert result["api_key"] == "****1234"  # masked last 4 of test-api-key-1234

        # Verify GET returns masked key
        resp = client.get("/api/providers")
        data = resp.json()
        found = [p for p in data if p["name"] == "unified-test"]
        assert len(found) == 1
        assert found[0]["api_key"] == "****1234"

    def test_providers_update_preserves_secret_with_placeholder(self, client):
        """POST /api/providers preserves the existing secret when the UI sends a masked placeholder."""
        client.post("/api/providers", json={
            "name": "preserve-secret",
            "base_url": "https://preserve.example.com/v1",
            "api_key": "sk-original-secret",
        })
        # Update with masked placeholder
        resp = client.post("/api/providers", json={
            "name": "preserve-secret",
            "base_url": "https://preserve.example.com/v1",
            "api_key": "****cret",
        })
        assert resp.status_code == 200
        # GET should still show the original masked secret (last 4 of original)
        resp = client.get("/api/providers")
        found = [p for p in resp.json() if p["name"] == "preserve-secret"]
        assert len(found) == 1
        assert found[0]["api_key"] == "****cret"

    def test_providers_delete(self, client):
        """DELETE /api/providers/{name} removes a provider."""
        client.post("/api/providers", json={
            "name": "to-delete-unified",
            "base_url": "https://x.com/v1",
        })
        resp = client.delete("/api/providers/to-delete-unified")
        assert resp.status_code == 200
        resp = client.get("/api/providers")
        found = [p for p in resp.json() if p["name"] == "to-delete-unified"]
        assert len(found) == 0

    def test_providers_toggle(self, client):
        """POST /api/providers/{name}/toggle toggles a provider."""
        client.post("/api/providers", json={
            "name": "toggle-unified",
            "base_url": "https://x.com/v1",
            "enabled": True,
        })
        resp = client.post("/api/providers/toggle-unified/toggle", json={"enabled": False})
        assert resp.status_code == 200
        resp = client.get("/api/providers")
        found = [p for p in resp.json() if p["name"] == "toggle-unified"]
        assert found[0]["enabled"] is False

    def test_providers_discover(self, client):
        """POST /api/providers/{name}/discover returns model discovery results."""
        # Use a local-only provider with a dummy URL so the discovery fails gracefully
        client.post("/api/providers", json={
            "name": "discover-unified",
            "base_url": "http://localhost:99999/v1",
        })
        resp = client.post("/api/providers/discover-unified/discover")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "count" in data

    def test_model_profiles_crud(self, client):
        """GET/POST/DELETE /api/models/profiles handles saved profiles."""
        resp = client.post("/api/models/profiles", json={
            "name": "my-profile",
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-profile-secret",
            "model": "gpt-4o-mini",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "my-profile"

        resp = client.get("/api/models/profiles")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(p["name"] == "my-profile" for p in data)
        profile = [p for p in data if p["name"] == "my-profile"][0]
        assert profile["api_key"] == "****cret" or profile["api_key"] == "***"

        resp = client.delete("/api/models/profiles/my-profile")
        assert resp.status_code == 200
        resp = client.get("/api/models/profiles")
        assert not any(p["name"] == "my-profile" for p in resp.json())

    def test_connectors_list(self, client):
        """GET /api/connectors returns all connector entries with masked tokens."""
        resp = client.get("/api/connectors")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(c["name"] == "telegram" for c in data)
        for connector in data:
            assert "token" in connector
            assert not connector["token"].startswith("123") or connector["token"] == ""

    def test_connectors_upsert_and_mask(self, client):
        """POST /api/connectors saves a token and GET returns it masked."""
        resp = client.post("/api/connectors", json={
            "name": "telegram",
            "token": "123456789:ABCDEFGHIJKLMNOP",
            "enabled": True,
            "extras": {"allowed_users": "12345"},
        })
        assert resp.status_code == 200
        result = resp.json()
        assert result["token"] == "****MNOP"  # last 4
        assert result["extras"]["allowed_users"] == "12345"

        resp = client.get("/api/connectors")
        telegram = [c for c in resp.json() if c["name"] == "telegram"][0]
        assert telegram["token"] == "****MNOP"

    def test_connectors_delete(self, client):
        """DELETE /api/connectors/{name} removes a connector config."""
        client.post("/api/connectors", json={
            "name": "discord",
            "token": "discord-token",
        })
        resp = client.delete("/api/connectors/discord")
        assert resp.status_code == 200
        resp = client.get("/api/connectors")
        discord = [c for c in resp.json() if c["name"] == "discord"][0]
        assert discord["token"] == ""

    def test_connectors_toggle(self, client):
        """POST /api/connectors/{name}/toggle toggles a connector."""
        resp = client.post("/api/connectors/telegram/toggle", json={"enabled": False})
        assert resp.status_code == 200
        resp = client.get("/api/connectors")
        telegram = [c for c in resp.json() if c["name"] == "telegram"][0]
        assert telegram["enabled"] is False

    @pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
    def test_connectors_test_missing_token(self, client, platform):
        """POST /api/connectors/{name}/test fails gracefully when no token is configured."""
        # Ensure no token is stored
        client.delete(f"/api/connectors/{platform}")
        resp = client.post(f"/api/connectors/{platform}/test")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "No token" in data["error"] or "token" in data["error"].lower()
