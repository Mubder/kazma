"""Unified Providers & Connectors management router.

Provides a single hub for LLM providers, saved model profiles, and platform
connector tokens (Telegram, Discord, Slack, etc.) with consistent masking,
CRUD, and test-before-save semantics.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from kazma_core.config_store import ConfigStore
from kazma_core.model_registry import get_model_registry

from kazma_ui.models import (
    ConnectorTestResponse,
    ConnectorUpdateRequest,
    ModelProfileUpdateRequest,
    ProviderTestResponse,
    ProviderToggleRequest,
    ProviderUpdateRequest,
)

logger = logging.getLogger(__name__)

# Connectors whose primary secret is stored under ``connectors.{name}.token``.
_CONNECTOR_PLATFORMS = ("telegram", "discord", "slack", "email", "webhook")

# Keys that are considered secrets and must be masked before leaving the backend.
_SECRET_KEY_HINTS = ("token", "secret", "password", "key", "api_key")


def _mask_secret(value: str) -> str:
    """Mask a secret for safe UI display.

    Returns ``****XXXX`` where ``XXXX`` is the last 4 characters, or ``***``
    if the value is shorter than 4 characters.
    """
    if not value:
        return ""
    if len(value) < 4:
        return "***"
    return f"****{value[-4:]}"


def _is_masked_placeholder(value: str) -> bool:
    """Return True if *value* is a masked placeholder from the UI."""
    if not value:
        return False
    # Common placeholders: "***", "****", "sk-****1234", "xoxb-****abcd"
    if value == "***" or value == "****" or "****" in value:
        return True
    return False


def _is_secret_key(key: str) -> bool:
    """Return True if a config key looks like it holds a secret."""
    lower = key.lower()
    return any(hint in lower for hint in _SECRET_KEY_HINTS)


def _mask_provider_entry(provider: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a provider entry with the API key masked."""
    safe = dict(provider)
    if safe.get("api_key"):
        safe["api_key"] = _mask_secret(str(safe["api_key"]))
    return safe


def _mask_connector_entry(name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Build a connector entry with all secret fields masked."""
    token = str(config.get("token", ""))
    extras: dict[str, str] = {}
    endpoint = ""
    for key, value in config.items():
        if key == "token":
            continue
        if key == "enabled":
            continue
        str_value = str(value)
        if _is_secret_key(key):
            str_value = _mask_secret(str_value)
        if key in ("incoming_url", "outgoing_url", "webhook_url", "base_url"):
            endpoint = str_value
        extras[key] = str_value

    return {
        "name": name,
        "platform": name,
        "type": "connector",
        "enabled": bool(config.get("enabled", True)),
        "token": _mask_secret(token),
        "endpoint": endpoint,
        "extras": extras,
        "status": "configured" if token else "missing_token",
    }


def _load_connector_config(config_store: ConfigStore, name: str) -> dict[str, Any]:
    """Load all config keys under ``connectors.{name}.*``."""
    prefix = f"connectors.{name}."
    all_settings = config_store.get_all()
    config: dict[str, Any] = {}
    # Flatten DB settings grouped by category
    for category_values in all_settings.values():
        for key, value in category_values.items():
            if key.startswith(prefix):
                config[key[len(prefix):]] = value
    return config


def create_providers_router(config_store: ConfigStore) -> APIRouter:
    """Create the unified providers & connectors router."""
    router = APIRouter(tags=["providers"])

    # ── LLM Providers ──────────────────────────────────────────────────

    @router.get("/api/providers")
    async def list_providers() -> list[dict[str, Any]]:
        """List all LLM providers with masked API keys."""
        registry = get_model_registry()
        return [_mask_provider_entry(p) for p in registry.list_providers()]

    @router.post("/api/providers")
    async def upsert_provider(req: ProviderUpdateRequest) -> dict[str, Any]:
        """Add or update an LLM provider.

        If the request contains a masked API key placeholder, the existing key
        is preserved instead of overwriting it with the placeholder.
        """
        registry = get_model_registry()
        data = req.model_dump()

        if _is_masked_placeholder(data.get("api_key", "")):
            existing = registry.get_provider(data["name"])
            if existing and existing.get("api_key"):
                data["api_key"] = existing["api_key"]

        result = registry.upsert_provider(data)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return _mask_provider_entry(result)

    @router.delete("/api/providers/{name}")
    async def delete_provider(name: str) -> dict[str, str]:
        """Delete an LLM provider by name."""
        registry = get_model_registry()
        registry.delete_provider(name)
        return {"status": "ok"}

    @router.post("/api/providers/{name}/toggle")
    async def toggle_provider(name: str, req: ProviderToggleRequest) -> dict[str, str]:
        """Enable or disable an LLM provider."""
        registry = get_model_registry()
        registry.toggle_provider(name, req.enabled)
        return {"status": "ok"}

    @router.post("/api/providers/{name}/test", response_model=ProviderTestResponse)
    async def test_provider(name: str) -> dict[str, Any]:
        """Run a non-destructive health check against the provider's /models endpoint."""
        registry = get_model_registry()
        provider = registry.get_provider(name)
        if not provider:
            return {"success": False, "error": f"Provider '{name}' not found"}

        base_url = str(provider.get("base_url", "")).rstrip("/")
        api_key = str(provider.get("api_key", ""))
        if not base_url:
            return {"success": False, "error": "No base URL configured"}

        start = time.monotonic()
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            from kazma_core.security.ssrf import validate_url
            validate_url(base_url)
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
                latency = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    registry.set_provider_health(name, "healthy")
                    return {"success": True, "latency_ms": latency}
                registry.set_provider_health(name, "degraded")
                return {
                    "success": False,
                    "latency_ms": latency,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except httpx.ConnectError:
            registry.set_provider_health(name, "down")
            return {"success": False, "error": f"Cannot connect to {base_url}"}
        except Exception as exc:  # pragma: no cover - defensive
            registry.set_provider_health(name, "down")
            logger.error("Provider test failed for %r: %s", name, exc)
            return {"success": False, "error": str(exc)}

    @router.post("/api/providers/{name}/discover")
    async def discover_provider_models(name: str) -> dict[str, Any]:
        """Discover models available for a provider."""
        registry = get_model_registry()
        models = await registry.discover_models(name)
        return {"name": name, "models": models, "count": len(models)}

    # ── Saved Model Profiles ────────────────────────────────────────────

    @router.get("/api/models/profiles")
    async def list_model_profiles() -> list[dict[str, Any]]:
        """List all saved model profiles with masked API keys."""
        registry = get_model_registry()
        profiles: list[dict[str, Any]] = registry.list_model_profiles(mask_api_key=True)
        return profiles

    @router.post("/api/models/profiles")
    async def save_model_profile(req: ModelProfileUpdateRequest) -> dict[str, Any]:
        """Save a named model profile.

        Stores under ``models.saved.{name}`` with the provider, base URL, API key,
        and model name.
        """
        registry = get_model_registry()
        data = req.model_dump()
        profile_name = data.get("name", "").strip()
        if not profile_name:
            raise HTTPException(status_code=400, detail="Profile name is required")

        existing = registry.get_model_profile(profile_name)
        if _is_masked_placeholder(data.get("api_key", "")) and existing:
            data["api_key"] = existing.get("api_key", "")

        profile: dict[str, Any] = {
            "provider": data.get("provider", "custom"),
            "base_url": data.get("base_url", ""),
            "api_key": data.get("api_key", ""),
            "model": data.get("model", ""),
        }
        result: dict[str, Any] = registry.save_model_profile(profile_name, profile)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @router.delete("/api/models/profiles/{name}")
    async def delete_model_profile(name: str) -> dict[str, str]:
        """Delete a saved model profile by name."""
        registry = get_model_registry()
        if not registry.delete_model_profile(name):
            raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
        return {"status": "ok"}

    # ── Platform Connectors ───────────────────────────────────────────

    @router.get("/api/connectors")
    async def list_connectors() -> list[dict[str, Any]]:
        """List all platform connectors with masked tokens."""
        entries: list[dict[str, Any]] = []
        for platform in _CONNECTOR_PLATFORMS:
            config = _load_connector_config(config_store, platform)
            entries.append(_mask_connector_entry(platform, config))
        return entries

    @router.post("/api/connectors")
    async def upsert_connector(req: ConnectorUpdateRequest) -> dict[str, Any]:
        """Add or update a platform connector token.

        If the request contains a masked token placeholder, the existing token is
        preserved instead of overwriting it with the placeholder.
        """
        name = req.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Connector name is required")

        existing_config = _load_connector_config(config_store, name)
        token = req.token
        if _is_masked_placeholder(token) and existing_config.get("token"):
            token = str(existing_config["token"])

        if token:
            config_store.set(f"connectors.{name}.token", token, category="connectors")
        config_store.set(f"connectors.{name}.enabled", req.enabled, category="connectors")
        for key, value in req.extras.items():
            config_store.set(f"connectors.{name}.{key}", value, category="connectors")

        updated = _load_connector_config(config_store, name)
        return _mask_connector_entry(name, updated)

    @router.delete("/api/connectors/{name}")
    async def delete_connector(name: str) -> dict[str, str]:
        """Remove a connector from ConfigStore."""
        # Delete all keys under connectors.{name}.* that exist in the DB.
        all_settings = config_store.get_all()
        prefix = f"connectors.{name}."
        deleted = False
        for category_values in all_settings.values():
            for key in list(category_values.keys()):
                if key.startswith(prefix):
                    config_store.delete(key)
                    deleted = True
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Connector '{name}' not found")
        return {"status": "ok"}

    @router.post("/api/connectors/{name}/test", response_model=ConnectorTestResponse)
    async def test_connector(name: str) -> dict[str, Any]:
        """Run a platform-specific health check for a connector."""
        name = name.strip()
        config = _load_connector_config(config_store, name)
        token = str(config.get("token", ""))
        if not token:
            return {"success": False, "error": f"No token configured for {name}"}

        if name == "telegram":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                    if resp.status_code == 200:
                        data = resp.json()
                        return {"success": True, "bot_name": data.get("result", {}).get("username", "")}
                    return {"success": False, "error": f"HTTP {resp.status_code}"}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        if name == "discord":
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        "https://discord.com/api/v10/users/@me",
                        headers={"Authorization": f"Bot {token}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return {"success": True, "bot_name": data.get("username", "")}
                    return {"success": False, "error": f"HTTP {resp.status_code}"}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        if name == "slack":
            app_token = str(config.get("app_token", ""))
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        "https://slack.com/api/auth.test",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("ok"):
                            return {"success": True, "bot_name": data.get("user", "")}
                        return {"success": False, "error": data.get("error", "Slack auth.test failed")}
                    return {"success": False, "error": f"HTTP {resp.status_code}"}
            except Exception as exc:
                return {"success": False, "error": str(exc)}

        # Generic connectors cannot be tested remotely; report token presence.
        return {"success": True, "message": f"Token configured for {name}"}

    @router.post("/api/connectors/{name}/toggle")
    async def toggle_connector(name: str, req: ProviderToggleRequest) -> dict[str, str]:
        """Enable or disable a connector."""
        name = name.strip()
        config_store.set(f"connectors.{name}.enabled", req.enabled, category="connectors")
        return {"status": "ok"}

    return router
