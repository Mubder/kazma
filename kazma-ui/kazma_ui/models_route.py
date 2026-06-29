"""Models & Ollama Management Routes.

Provides:
  GET  /api/models         — Unified model discovery (provider-aware)
  GET  /api/ollama/check   — Ollama health check
  POST /api/ollama/pull    — Pull a model via ollama pull (background)
  GET  /api/models/saved   — List all saved model profiles
  POST /api/models/saved   — Save a new named model profile
  DELETE /api/models/saved/{name} — Delete a saved model profile
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


class SaveModelProfileRequest(BaseModel):
    """Request body for POST /api/models/saved."""

    name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provider: str = "custom"


def create_models_router(config_store: Any = None) -> APIRouter:
    """Create the models & Ollama management router.

    Args:
        config_store: Optional :class:`ConfigStore` instance. When provided,
            the saved-model-profiles endpoints (GET/POST/DELETE
            /api/models/saved) are registered. When ``None``, those
            endpoints return a 503 indicating profiles are unavailable.

    Returns:
        APIRouter with /api/models, /api/ollama/check, /api/ollama/pull,
        and (conditionally) /api/models/saved.
    """
    r = APIRouter(tags=["models"])

    # Lazily initialize SettingsManager for profile operations
    _sm = None

    def _get_sm():
        nonlocal _sm
        if _sm is None and config_store is not None:
            from kazma_core.settings_manager import SettingsManager
            _sm = SettingsManager(config_store)
        return _sm

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

        # Use preset base_url if a known provider and none provided
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            preset = registry.get_provider(provider)
            if preset and not base_url:
                base_url = preset.get("base_url", "")
        except RuntimeError:
            from kazma_core.providers import PROVIDER_PRESETS
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

    # ── Saved Model Profiles ───────────────────────────────────────

    @r.get("/api/models/saved")
    async def list_saved_models() -> list[dict[str, Any]]:
        """List all saved model profiles.

        Returns:
            List of profile dicts with masked api_key.
        """
        sm = _get_sm()
        if sm is None:
            return []
        return sm.get_saved_model_profiles()

    @r.post("/api/models/saved", status_code=201)
    async def save_model_profile(req: SaveModelProfileRequest) -> dict[str, Any]:
        """Save a named model profile.

        Request body:
            {"name": "my-gpt4", "base_url": "...", "api_key": "...",
             "model": "gpt-4o", "provider": "openai"}
        """
        sm = _get_sm()
        if sm is None:
            return {"error": "ConfigStore not available"}
        result = sm.save_model_profile(req.name, req.model_dump())
        if "error" in result:
            from fastapi.responses import JSONResponse
            return JSONResponse(result, status_code=400)
        return result

    @r.delete("/api/models/saved/{name}")
    async def delete_model_profile(name: str) -> dict[str, str]:
        """Delete a saved model profile by name."""
        sm = _get_sm()
        if sm is None:
            return {"status": "error", "message": "ConfigStore not available"}
        deleted = sm.delete_model_profile(name)
        if not deleted:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"status": "error", "message": f"Profile '{name}' not found"},
                status_code=404,
            )
        return {"status": "ok"}

    return r
