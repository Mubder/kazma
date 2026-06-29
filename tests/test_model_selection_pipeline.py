"""Tests for fix-002-model-selection-pipeline.

Validates:
  VAL-UI-002 — Chat page has a model selector dropdown
  VAL-UI-003 — Model selection persists (localStorage key present)
  VAL-UI-004 — Saving model config calls /api/provider/switch + reconfigure
  VAL-UI-005 — Chat sends the selected model in SSE request body
  VAL-UI-006 — Missing API key produces a visible error message
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_ui.sse_chat import _is_cloud_url, create_sse_chat_router

_UI = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
_CHAT_JS = _UI / "static" / "js" / "chat.js"
_SETTINGS_JS = _UI / "static" / "js" / "settings.js"
_CHAT_HTML = _UI / "templates" / "chat.html"


@pytest.fixture(autouse=True)
def _reset_shared_session_store():
    """Reset the shared SessionManager singleton before each test."""
    from kazma_ui.session_manager import reset_session_manager

    reset_session_manager()


# ═══════════════════════════════════════════════════════════════════
# Helper
# ═══════════════════════════════════════════════════════════════════


def _make_app(
    *,
    provider_profile: dict[str, Any] | None = None,
    llm_provider: Any = None,
) -> tuple[FastAPI, Any]:
    """Create a test app with a mock graph and optional llm_provider."""
    graph = MagicMock()
    router = create_sse_chat_router(
        graph=graph,
        checkpointer=None,
        provider_profile=provider_profile,
        llm_provider=llm_provider,
    )
    app = FastAPI()
    app.include_router(router)
    return app, graph


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-005: Chat sends the selected model in SSE request body
#              Server reads and applies the model
# ═══════════════════════════════════════════════════════════════════


class TestChatStreamReadsModel:
    """VAL-UI-005: chat_stream reads body.get('model') and calls reconfigure."""

    def test_model_in_body_triggers_reconfigure(self):
        """When 'model' is in the request body, llm_provider.reconfigure() is called."""
        mock_provider = MagicMock()
        mock_provider.reconfigure = MagicMock()
        # Provide a non-cloud profile so we don't hit the API-key gate
        mock_provider.config.api_key = "test-key-not-real"
        mock_provider.config.base_url = "http://localhost:1234/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        client.post(
            "/api/chat/stream",
            json={"message": "hello", "model": "gpt-4o-mini"},
        )

        mock_provider.reconfigure.assert_called_once_with(model="gpt-4o-mini")

    def test_no_model_in_body_does_not_reconfigure(self):
        """When 'model' is absent from the body, reconfigure is NOT called."""
        mock_provider = MagicMock()
        mock_provider.reconfigure = MagicMock()
        mock_provider.config.api_key = "test-key-not-real"
        mock_provider.config.base_url = "http://localhost:1234/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        client.post("/api/chat/stream", json={"message": "hello"})

        mock_provider.reconfigure.assert_not_called()

    def test_empty_model_string_does_not_reconfigure(self):
        """An empty 'model' value should not trigger reconfigure."""
        mock_provider = MagicMock()
        mock_provider.config.api_key = "test-key-not-real"
        mock_provider.config.base_url = "http://localhost:1234/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        client.post("/api/chat/stream", json={"message": "hello", "model": ""})

        mock_provider.reconfigure.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-004: /api/provider/switch reconfigures the live LLM
# ═══════════════════════════════════════════════════════════════════


class TestProviderSwitchReconfigure:
    """VAL-UI-004: POST /api/provider/switch calls llm_provider.reconfigure()."""

    def test_switch_calls_reconfigure(self):
        mock_provider = MagicMock()
        mock_provider.reconfigure = MagicMock()

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/provider/switch", json={
            "provider": "custom",
            "base_url": "http://localhost:1234/v1",
            "model": "local-model",
            "api_key": "test-key-123",
        })

        assert resp.status_code == 200
        mock_provider.reconfigure.assert_called_once()
        call_kwargs = mock_provider.reconfigure.call_args.kwargs
        assert "test-key-123" in call_kwargs.get("api_key", "")
        assert "local-model" in call_kwargs.get("model", "") or call_kwargs.get("model") == ""

    def test_switch_returns_status_ok(self):
        mock_provider = MagicMock()
        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/provider/switch", json={
            "provider": "ollama",
            "model": "llama3.2",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "ok"

    def test_switch_invalid_json_returns_error(self):
        mock_provider = MagicMock()
        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post(
            "/api/provider/switch",
            content="not json",
            headers={"content-type": "application/json"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"error": "Invalid JSON"}


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-006: Missing API key produces a visible error message
# ═══════════════════════════════════════════════════════════════════


class TestMissingApiKeyValidation:
    """VAL-UI-006: pre-stream validation catches missing API keys on cloud APIs."""

    def test_not_needed_key_on_cloud_returns_helpful_error(self):
        """A 'not-needed' key on a cloud URL returns an immediate error frame."""
        mock_provider = MagicMock()
        mock_provider.config.api_key = "not-needed"
        mock_provider.config.base_url = "https://api.openai.com/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": "hello"})

        assert resp.status_code == 200
        body = resp.text
        assert "event: error" in body
        assert "No API key" in body
        assert "Settings" in body

    def test_empty_key_on_cloud_returns_error(self):
        """An empty key on a cloud URL also returns the error frame."""
        mock_provider = MagicMock()
        mock_provider.config.api_key = ""
        mock_provider.config.base_url = "https://api.deepseek.com/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": "hello"})

        assert resp.status_code == 200
        body = resp.text
        assert "event: error" in body

    def test_real_key_on_cloud_does_not_block(self):
        """A real API key on a cloud URL should NOT trigger the gate."""
        mock_provider = MagicMock()
        mock_provider.config.api_key = "test-key-123-123"
        mock_provider.config.base_url = "https://api.openai.com/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": "hello"})

        body = resp.text
        # Should NOT contain the missing-API-key error message
        assert "No API key" not in body

    def test_not_needed_key_on_local_does_not_block(self):
        """A 'not-needed' key on a local URL should NOT trigger the gate."""
        mock_provider = MagicMock()
        mock_provider.config.api_key = "not-needed"
        mock_provider.config.base_url = "http://localhost:1234/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": "hello"})

        body = resp.text
        assert "No API key" not in body

    def test_not_needed_key_on_ollama_does_not_block(self):
        """Ollama (port 11434) never needs a key and should not be blocked."""
        mock_provider = MagicMock()
        mock_provider.config.api_key = "not-needed"
        mock_provider.config.base_url = "http://localhost:11434/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": "hello"})

        body = resp.text
        assert "No API key" not in body

    def test_error_directs_to_settings_models(self):
        """The error message must mention Settings > Models."""
        mock_provider = MagicMock()
        mock_provider.config.api_key = "not-needed"
        mock_provider.config.base_url = "https://api.openai.com/v1"

        app, _ = _make_app(llm_provider=mock_provider)
        client = TestClient(app)

        resp = client.post("/api/chat/stream", json={"message": "hello"})

        body = resp.text
        assert "Settings" in body
        assert "Models" in body


# ═══════════════════════════════════════════════════════════════════
# _is_cloud_url helper tests
# ═══════════════════════════════════════════════════════════════════


class TestIsCloudUrl:
    """Tests for the _is_cloud_url helper."""

    def test_openai_is_cloud(self):
        assert _is_cloud_url("https://api.openai.com/v1") is True

    def test_deepseek_is_cloud(self):
        assert _is_cloud_url("https://api.deepseek.com/v1") is True

    def test_localhost_is_not_cloud(self):
        assert _is_cloud_url("http://localhost:1234/v1") is False

    def test_loopback_ip_is_not_cloud(self):
        assert _is_cloud_url("http://127.0.0.1:8080/v1") is False

    def test_ollama_port_is_not_cloud(self):
        # Ollama runs on port 11434 and never needs a key
        assert _is_cloud_url("http://localhost:11434/v1") is False

    def test_litellm_port_is_not_cloud(self):
        assert _is_cloud_url("http://localhost:4000/v1") is False

    def test_empty_url_is_not_cloud(self):
        assert _is_cloud_url("") is False

    def test_remote_custom_host_is_cloud(self):
        assert _is_cloud_url("https://my-llm-proxy.example.com/v1") is True


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-002: Chat page has a model selector dropdown
# ═══════════════════════════════════════════════════════════════════


class TestChatHtmlModelSelector:
    """VAL-UI-002: chat.html must have a model selector dropdown."""

    def test_chat_html_has_model_selector(self):
        html = _CHAT_HTML.read_text(encoding="utf-8")
        assert 'id="model-selector"' in html, (
            "chat.html must have an element with id='model-selector'"
        )

    def test_chat_html_model_selector_is_select(self):
        html = _CHAT_HTML.read_text(encoding="utf-8")
        pattern = re.compile(r'<select[^>]*id="model-selector"', re.IGNORECASE)
        assert pattern.search(html), "model-selector must be a <select> element"

    def test_chat_html_has_model_bar(self):
        html = _CHAT_HTML.read_text(encoding="utf-8")
        assert "chat-model-bar" in html, (
            "chat.html should have a model selector bar container"
        )


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-003: Model selection persists in localStorage
# ═══════════════════════════════════════════════════════════════════


class TestChatJsModelPersistence:
    """VAL-UI-003: chat.js stores model selection in localStorage."""

    def test_chat_js_has_model_selector_ref(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "model-selector" in js, "chat.js must reference the model-selector element"

    def test_chat_js_has_local_storage_key(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "kazma.selectedModel" in js, (
            "chat.js must store the selected model in localStorage under "
            "'kazma.selectedModel'"
        )

    def test_chat_js_has_load_models_function(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "function loadModels" in js, "chat.js must have a loadModels function"

    def test_chat_js_calls_load_models_on_init(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        # loadModels() must be called during init
        assert "loadModels()" in js

    def test_chat_js_includes_model_in_sse_body(self):
        """VAL-UI-005: the SSE POST body must include the selected model."""
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "model:" in js, "chat.js sendMessage must include model in the SSE body"

    def test_chat_js_has_on_model_change_handler(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "onModelChange" in js, "chat.js must have an onModelChange handler"


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-004: settings.js saveModel calls /api/provider/switch
# ═══════════════════════════════════════════════════════════════════


class TestSettingsJsProviderSwitch:
    """VAL-UI-004: settings.js saveModel must call POST /api/provider/switch."""

    def test_settings_js_save_model_calls_provider_switch(self):
        js = _SETTINGS_JS.read_text(encoding="utf-8")
        assert "/api/provider/switch" in js, (
            "settings.js saveModel() must call POST /api/provider/switch"
        )

    def test_settings_js_save_model_sends_model_and_key(self):
        js = _SETTINGS_JS.read_text(encoding="utf-8")
        # The switch body must include model, base_url, and api_key
        assert "api_key" in js
        assert "base_url" in js


# ═══════════════════════════════════════════════════════════════════
# VAL-UI-006 (frontend): error frames display without token events
# ═══════════════════════════════════════════════════════════════════


class TestChatJsErrorDisplay:
    """VAL-UI-006: error frames must display even when no tokens preceded."""

    def test_on_error_creates_assistant_message(self):
        """onError must call createAssistantMessage when currentMsgEl is null."""
        js = _CHAT_JS.read_text(encoding="utf-8")
        # The onError callback should contain the pattern that creates
        # an assistant message if none exists
        assert "createAssistantMessage" in js, (
            "chat.js onError must create an assistant message to display errors"
        )

    def test_on_error_renders_error_message_class(self):
        js = _CHAT_JS.read_text(encoding="utf-8")
        assert "error-message" in js, (
            "chat.js must render errors with an 'error-message' CSS class"
        )
