"""Models & Ollama Management Routes.

Provides:
  GET  /api/models         — Unified model discovery (provider-aware)
  GET  /api/ollama/check   — Ollama health check
  POST /api/ollama/pull    — Pull a model via ollama pull (background)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class OllamaPullRequest(BaseModel):
    """Request body for POST /api/ollama/pull."""

    model: str


def create_models_router() -> APIRouter:
    """Create the models & Ollama management router.

    Returns:
        APIRouter with /api/models, /api/ollama/check, /api/ollama/pull.
    """
    r = APIRouter(tags=["models"])

    # ── Background pull tasks (tracked by PID) ────────────────────
    _pull_tasks: dict[str, dict[str, Any]] = {}

    @r.get("/api/models")
    @r.get("/v1/models")
    async def get_models(
        provider: str = Query("all", description="Provider key: openai, anthropic, deepseek, google, xai, openrouter, ollama, lm-studio, custom, all"),
        base_url: str | None = Query(None, description="Override base URL"),
        api_key: str | None = Query(None, description="API key for authenticated model discovery"),
    ) -> dict[str, Any]:
        """Unified model discovery endpoint.

        Routes to the correct provider based on the `provider` query param.
        Built-in providers (openai, anthropic, deepseek, google, xai, openrouter)
        auto-resolve their default base_url from PROVIDER_PRESETS.

        Returns:
            {"models": ["gpt-4o-mini", ...], "provider": "openai", "online": true}
        """
        from kazma_core.models.discovery import discover_models
        from kazma_core.providers import PROVIDER_PRESETS

        # Use preset base_url if a known provider and none provided
        if provider in PROVIDER_PRESETS and not base_url:
            base_url = PROVIDER_PRESETS[provider]["base_url"]

        result = await discover_models(provider, base_url=base_url, api_key=api_key)

        return {
            "models": result.models,
            "provider": result.name,
            "base_url": result.base_url,
            "online": result.online,
            "error": result.error,
        }

    @r.get("/api/ollama/check")
    async def ollama_check() -> dict[str, Any]:
        """Check if Ollama is running.

        Returns:
            {"online": true, "models": 5, "error": null}
        """
        from kazma_core.models.discovery import check_ollama_health

        return await check_ollama_health()

    @r.post("/api/ollama/pull")
    async def ollama_pull(req: OllamaPullRequest) -> dict[str, Any]:
        """Pull a model via `ollama pull` in the background.

        Does not block the event loop — spawns an async subprocess.

        Request body:
            {"model": "llama3.2"}

        Returns:
            {"status": "pulling", "model": "llama3.2", "pid": 12345}
        """
        from kazma_core.models.discovery import pull_ollama_model

        result = await pull_ollama_model(req.model)

        if result.get("status") == "pulling":
            _pull_tasks[req.model] = result
            logger.info("Ollama pull queued: %s (pid=%s)", req.model, result.get("pid"))

        return result

    @r.get("/api/ollama/pulls")
    async def ollama_pulls() -> list[dict[str, Any]]:
        """List active pull tasks.

        Returns:
            List of {"model": "...", "pid": ..., "status": "..."}
        """
        return list(_pull_tasks.values())

    return r
