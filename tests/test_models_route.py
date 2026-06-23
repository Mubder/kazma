"""Tests for the multi-provider discovery engine and models route.

Covers:
  - discover_ollama_models: tag cleaning, offline handling
  - discover_lm_studio_models: URL normalization, model extraction
  - discover_custom_models: error handling
  - discover_models: routing logic
  - check_ollama_health: online/offline
  - pull_ollama_model: subprocess spawning
  - /api/models endpoint: provider query param
  - /api/ollama/check endpoint
  - /api/provider/switch endpoint
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.models.discovery import (
    ProviderInfo,
    check_ollama_health,
    discover_lm_studio_models,
    discover_models,
    discover_ollama_models,
    pull_ollama_model,
)

# ═══════════════════════════════════════════════════════════════════
# ProviderInfo
# ═══════════════════════════════════════════════════════════════════


class TestProviderInfo:
    def test_defaults(self):
        info = ProviderInfo(name="test", label="Test", base_url="http://localhost")
        assert info.models == []
        assert info.online is False
        assert info.error is None


# ═══════════════════════════════════════════════════════════════════
# discover_ollama_models
# ═══════════════════════════════════════════════════════════════════


class TestDiscoverOllama:
    @pytest.mark.asyncio
    async def test_strips_latest_suffix(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3.2:latest"},
                {"name": "qwen2.5:7b"},
                {"name": "codellama"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            info = await discover_ollama_models()

        assert info.online is True
        assert "ollama/llama3.2" in info.models  # :latest stripped
        assert "ollama/qwen2.5:7b" in info.models  # non-latest kept
        assert "ollama/codellama" in info.models  # no tag

    @pytest.mark.asyncio
    async def test_offline(self):
        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=Exception("Connection refused"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            info = await discover_ollama_models()

        assert info.online is False
        assert info.error is not None


# ═══════════════════════════════════════════════════════════════════
# discover_lm_studio_models
# ═══════════════════════════════════════════════════════════════════


class TestDiscoverLMStudio:
    @pytest.mark.asyncio
    async def test_extracts_model_ids(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"id": "local-model"}, {"id": "gpt-4o-mini"}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            info = await discover_lm_studio_models()

        assert info.online is True
        assert "openai/local-model" in info.models
        assert "openai/gpt-4o-mini" in info.models

    @pytest.mark.asyncio
    async def test_custom_url_normalized(self):
        info = await discover_lm_studio_models("localhost:9999")
        assert info.base_url == "http://localhost:9999/v1"


# ═══════════════════════════════════════════════════════════════════
# discover_models (routing)
# ═══════════════════════════════════════════════════════════════════


class TestDiscoverModels:
    @pytest.mark.asyncio
    async def test_routes_to_ollama(self):
        with patch(
            "kazma_core.models.discovery.discover_ollama_models",
            return_value=ProviderInfo("ollama", "Ollama", "", ["ollama/llama3.2"], True),
        ):
            info = await discover_models("ollama")
        assert info.name == "ollama"
        assert "ollama/llama3.2" in info.models

    @pytest.mark.asyncio
    async def test_routes_to_lm_studio(self):
        with patch(
            "kazma_core.models.discovery.discover_lm_studio_models",
            return_value=ProviderInfo("lm_studio", "LM Studio", "", ["openai/model"], True),
        ):
            info = await discover_models("lm-studio")
        assert info.name == "lm_studio"

    @pytest.mark.asyncio
    async def test_custom_requires_base_url(self):
        info = await discover_models("custom")
        assert info.error is not None
        assert "base_url" in info.error


# ═══════════════════════════════════════════════════════════════════
# check_ollama_health
# ═══════════════════════════════════════════════════════════════════


class TestOllamaHealth:
    @pytest.mark.asyncio
    async def test_online(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "a"}, {"name": "b"}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await check_ollama_health()

        assert result["online"] is True
        assert result["models"] == 2

    @pytest.mark.asyncio
    async def test_offline(self):
        with patch("httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=Exception("refused"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await check_ollama_health()

        assert result["online"] is False


# ═══════════════════════════════════════════════════════════════════
# pull_ollama_model
# ═══════════════════════════════════════════════════════════════════


class TestOllamaPull:
    @pytest.mark.asyncio
    async def test_empty_model(self):
        result = await pull_ollama_model("")
        assert result["status"] == "error"
        assert "Empty" in result["error"]

    @pytest.mark.asyncio
    async def test_ollama_not_installed(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await pull_ollama_model("llama3.2")
        assert result["status"] == "error"
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_spawn(self):
        mock_proc = AsyncMock()
        mock_proc.pid = 12345

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await pull_ollama_model("llama3.2")

        assert result["status"] == "pulling"
        assert result["pid"] == 12345


# ═══════════════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════════════


class TestModelsEndpoint:
    def test_get_models_returns_list(self):
        from kazma_ui.models_route import create_models_router

        app = FastAPI()
        app.include_router(create_models_router())
        client = TestClient(app)

        with patch(
            "kazma_core.models.discovery.discover_ollama_models",
            return_value=ProviderInfo("ollama", "Ollama", "", ["ollama/llama3.2"], True),
        ):
            resp = client.get("/api/models?provider=ollama")

        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert data["provider"] == "ollama"

    def test_ollama_check_endpoint(self):
        from kazma_ui.models_route import create_models_router

        app = FastAPI()
        app.include_router(create_models_router())
        client = TestClient(app)

        with patch(
            "kazma_core.models.discovery.check_ollama_health",
            return_value={"online": True, "models": 3, "error": None},
        ):
            resp = client.get("/api/ollama/check")

        assert resp.status_code == 200
        data = resp.json()
        assert data["online"] is True
        assert data["models"] == 3

    def test_ollama_pull_endpoint(self):
        from kazma_ui.models_route import create_models_router

        app = FastAPI()
        app.include_router(create_models_router())
        client = TestClient(app)

        mock_proc = AsyncMock()
        mock_proc.pid = 999

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            resp = client.post("/api/ollama/pull", json={"model": "llama3.2"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pulling"
        assert data["model"] == "llama3.2"


class TestProviderSwitch:
    def test_switch_to_ollama(self):
        from kazma_ui.sse_chat import create_sse_chat_router

        app = FastAPI()
        app.include_router(create_sse_chat_router(graph=MagicMock(), checkpointer=None))
        client = TestClient(app)

        resp = client.post(
            "/api/provider/switch",
            json={
                "provider": "ollama",
                "model": "llama3.2",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "ollama"
        assert data["model"] == "ollama/llama3.2"
        assert data["base_url"] == "http://127.0.0.1:11434/v1"

    def test_switch_to_lm_studio(self):
        from kazma_ui.sse_chat import create_sse_chat_router

        app = FastAPI()
        app.include_router(create_sse_chat_router(graph=MagicMock(), checkpointer=None))
        client = TestClient(app)

        resp = client.post(
            "/api/provider/switch",
            json={
                "provider": "lm-studio",
                "base_url": "localhost:1234",
                "model": "local-model",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "lm-studio"
        assert data["model"] == "openai/local-model"
        assert data["base_url"] == "http://localhost:1234/v1"

    def test_switch_to_custom(self):
        from kazma_ui.sse_chat import create_sse_chat_router

        app = FastAPI()
        app.include_router(create_sse_chat_router(graph=MagicMock(), checkpointer=None))
        client = TestClient(app)

        resp = client.post(
            "/api/provider/switch",
            json={
                "provider": "custom",
                "base_url": "my-server:8080",
                "model": "gpt-4o",
                "api_key": "sk-real-key",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "custom"
        assert data["base_url"] == "http://my-server:8080/v1"

    def test_get_active_provider(self):
        from kazma_ui.sse_chat import create_sse_chat_router

        app = FastAPI()
        app.include_router(create_sse_chat_router(graph=MagicMock(), checkpointer=None))
        client = TestClient(app)

        # Switch first
        client.post("/api/provider/switch", json={"provider": "ollama", "model": "llama3.2"})

        resp = client.get("/api/provider/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "ollama"
        assert data["model"] == "ollama/llama3.2"

    def test_real_key_masked(self):
        from kazma_ui.sse_chat import create_sse_chat_router

        app = FastAPI()
        app.include_router(create_sse_chat_router(graph=MagicMock(), checkpointer=None))
        client = TestClient(app)

        client.post(
            "/api/provider/switch",
            json={
                "provider": "custom",
                "base_url": "my-server:8080",
                "model": "gpt-4o",
                "api_key": "sk-real-secret-key",
            },
        )

        resp = client.get("/api/provider/active")
        data = resp.json()
        assert data["api_key"] == "***"
