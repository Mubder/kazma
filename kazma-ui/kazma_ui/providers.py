"""Unified Providers & Connectors management router.

Provides a single hub for LLM providers, saved model profiles, and platform
connector tokens (Telegram, Discord, Slack, etc.) with consistent masking,
CRUD, and test-before-save semantics.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from kazma_core.config_store import ConfigStore, is_vault_ref
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

__all__ = ["create_providers_router"]

# Connectors whose primary secret is stored under ``connectors.{name}.token``.
_CONNECTOR_PLATFORMS = ("telegram", "discord", "slack", "email", "webhook")

# Keys that are considered secrets and must be masked before leaving the backend.
_SECRET_KEY_HINTS = ("token", "secret", "password", "key", "api_key")


def _normalize_telegram_bot_token(raw: str) -> str:
    """Light cleanup only — never invent/reject token shapes.

    Strips surrounding whitespace/quotes. If the user pasted a full API path
    fragment like ``bot123:AA…`` (docs style), drop the leading ``bot`` so
    ``/bot{token}/getMe`` is not doubled. Do **not** regex-reject tokens:
    BotFather formats vary and vault/env values must pass through as-is.
    """
    token = (raw or "").strip().strip("\"'")
    # Only strip a literal docs-style prefix: bot + digits…
    if len(token) > 4 and token[:3].lower() == "bot" and token[3].isdigit():
        token = token[3:]
    return token


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
    """Load all config keys under ``connectors.{name}.*``.

    Uses ``config_store.get()`` (vault-aware) rather than raw ``get_all()``
    values. Sensitive tokens are stored as ``vault://…`` pointers in the DB;
    ``get_all()`` returns those pointers unresolved, which broke connector
    Test (Telegram saw ``/botvault://…/getMe`` → HTTP 404, or later a false
    "format looks wrong" after a regex gate).
    """
    prefix = f"connectors.{name}."
    all_settings = config_store.get_all()
    config: dict[str, Any] = {}
    # Flatten DB settings grouped by category
    for category_values in all_settings.values():
        for key, value in category_values.items():
            if not key.startswith(prefix):
                continue
            # Always re-fetch via get() so vault secrets decrypt.
            resolved = config_store.get(key, value)
            if resolved is None or (isinstance(resolved, str) and is_vault_ref(resolved)):
                # Vault disabled / decrypt failed — treat as missing secret.
                if key.endswith(".token") or key.rsplit(".", 1)[-1] in _SECRET_KEY_HINTS:
                    resolved = ""
            config[key[len(prefix):]] = resolved
    return config


def create_providers_router(config_store: ConfigStore) -> APIRouter:
    """Create the unified providers & connectors router."""
    router = APIRouter(tags=["providers"])

    # ── LLM Providers ──────────────────────────────────────────────────

    @router.get("/api/providers")
    async def list_providers() -> list[dict[str, Any]]:
        """List all LLM providers with masked API keys and discovered models."""
        registry = get_model_registry()
        providers = []
        for p in registry.list_providers():
            entry = _mask_provider_entry(p)
            name = p.get("name", "")
            discovered = registry.get_discovered_models(name)
            selected = registry.get_selected_models(name)
            if discovered:
                manual = set(entry.get("models", []))
                entry["discovered_models"] = discovered
                entry["all_models"] = sorted(manual | set(discovered))
            else:
                entry["discovered_models"] = []
                entry["all_models"] = entry.get("models", [])
            entry["selected_models"] = selected
            entry["visible_models"] = registry.get_visible_models(name)
            providers.append(entry)
        return providers

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

        if name.lower() == "google":
            # Test Google Provider (either AI Studio or Vertex AI)
            try:
                client = registry.get_client_by_provider(name)
                if not client:
                    return {"success": False, "error": "Failed to construct Google Gemini provider client"}
                
                # Retrieve the authenticated HTTP client (resolves ADC or API Key)
                http_client = await client._get_client()
                
                # Perform a lightweight ping to the base URL
                start = time.monotonic()
                if getattr(client, "_use_ai_studio", False):
                    # Google AI Studio: query the native models endpoint which is guaranteed to be supported.
                    resp = await http_client.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={client.config.api_key}")
                    latency = int((time.monotonic() - start) * 1000)
                    if resp.status_code == 200:
                        registry.set_provider_health(name, "healthy")
                        return {"success": True, "latency_ms": latency}
                    else:
                        registry.set_provider_health(name, "degraded")
                        error_msg = f"AI Studio returned HTTP {resp.status_code}"
                        if resp.status_code == 401:
                            error_msg += " (Unauthorized: Your Google AI Studio API key is invalid or has expired. Please verify your key or generate a new one at https://aistudio.google.com/)"
                        elif resp.status_code == 403:
                            error_msg += " (Forbidden: Your key does not have permission, or your region might not be supported. See https://ai.google.dev/gemini-api/docs/available-regions)"
                        return {"success": False, "error": error_msg, "latency_ms": latency}
                else:
                    # Vertex AI: just check if get_client succeeded and do a lightweight head/get to base_url to check network.
                    try:
                        resp = await http_client.get("")
                        latency = int((time.monotonic() - start) * 1000)
                        registry.set_provider_health(name, "healthy")
                        return {"success": True, "latency_ms": latency}
                    except httpx.ConnectError:
                        registry.set_provider_health(name, "down")
                        return {"success": False, "error": f"Cannot connect to Vertex AI at {client.config.base_url}"}
            except Exception as e:
                registry.set_provider_health(name, "down")
                return {"success": False, "error": f"Google Provider test failed: {e}"}

        base_url = str(provider.get("base_url", "")).rstrip("/")
        api_key = str(provider.get("api_key", ""))
        if not base_url:
            return {"success": False, "error": "No base URL configured"}

        # Normalize to the OpenAI-compatible /v1 form. Local providers
        # (Ollama, LM Studio, …) live on localhost / private LAN addresses,
        # so private URLs must be allowed here (they are user-configured
        # endpoints, not untrusted external input).
        from kazma_core.url_utils import normalize_provider_url
        from kazma_core.security.ssrf import validate_url
        from urllib.parse import urlparse, urlunparse

        base = normalize_provider_url(base_url, ensure_v1=True)
        root = urlunparse((urlparse(base).scheme, urlparse(base).netloc, "", "", "", ""))
        # Candidate health endpoints: the provider's own /models, plus the
        # OpenAI-compatible /v1/models (covers LM Studio, Ollama, cloud).
        candidates = [f"{base}/models", f"{root}/v1/models"]

        # Strip any non-ASCII characters from the API key (e.g. an Arabic key
        # pasted by an Arabic-first operator) so the Authorization header can
        # be encoded — mirrors LLMProvider._strip_non_ascii.
        safe_key = "".join(ch for ch in api_key if ord(ch) < 128) if api_key else ""

        start = time.monotonic()
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if safe_key:
                headers["Authorization"] = f"Bearer {safe_key}"
            validate_url(base, allow_private=True)
            async with httpx.AsyncClient(timeout=10.0) as client:
                last_status = None
                last_body = ""
                for url in candidates:
                    try:
                        resp = await client.get(url, headers=headers)
                    except Exception:
                        # Try the next candidate endpoint.
                        continue
                    last_status = resp.status_code
                    last_body = resp.text[:200]
                    if resp.status_code == 200:
                        latency = int((time.monotonic() - start) * 1000)
                        registry.set_provider_health(name, "healthy")
                        return {"success": True, "latency_ms": latency}
                latency = int((time.monotonic() - start) * 1000)
                registry.set_provider_health(name, "degraded")
                return {
                    "success": False,
                    "latency_ms": latency,
                    "error": f"HTTP {last_status}: {last_body}",
                }
        except httpx.ConnectError:
            registry.set_provider_health(name, "down")
            return {"success": False, "error": f"Cannot connect to {base_url}"}
        except Exception as exc:  # pragma: no cover - defensive
            registry.set_provider_health(name, "down")
            logger.error("Provider test failed for %r: %s", name, exc)
            return {"success": False, "error": "Provider test failed unexpectedly"}

    @router.post("/api/providers/{name}/discover")
    async def discover_provider_models(name: str) -> dict[str, Any]:
        """Discover models available for a provider and persist them."""
        registry = get_model_registry()
        models = await registry.discover_models(name)
        registry.serialize()  # persist discovered models to ConfigStore
        return {"name": name, "models": models, "count": len(models)}

    @router.post("/api/providers/{name}/select-models")
    async def set_selected_models(name: str, req: dict[str, Any]) -> dict[str, Any]:
        """Set which discovered models should appear in dropdowns.

        Body: ``{"models": ["model-a", "model-b"]}``
        """
        registry = get_model_registry()
        models = req.get("models", []) if isinstance(req, dict) else []
        if not isinstance(models, list):
            models = []
        registry.set_selected_models(name, [str(m) for m in models])
        return {"status": "ok", "selected_models": registry.get_selected_models(name)}

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
        elif name == "telegram" and token and not _is_masked_placeholder(token):
            # Normalize on save so a later Test never builds /botbot…/getMe.
            token = _normalize_telegram_bot_token(token)

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
        # Vault-aware direct read (do not rely on get_all raw values).
        token = str(config_store.get(f"connectors.{name}.token", "") or "").strip()
        # Match gateway boot: fall back to env when ConfigStore has no token.
        if not token and name == "telegram":
            token = (
                os.environ.get("TELEGRAM_BOT_TOKEN", "")
                or os.environ.get("TELEGRAM_TOKEN", "")
            ).strip()
        if not token and name == "discord":
            token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        if not token and name == "slack":
            token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
        if not token:
            return {
                "success": False,
                "error": (
                    f"No token configured for {name} "
                    "(or vault could not decrypt connectors.{name}.token)."
                ),
            }

        if name == "telegram":
            token = _normalize_telegram_bot_token(token)
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                    if resp.status_code == 200:
                        data = resp.json()
                        return {
                            "success": True,
                            "bot_name": data.get("result", {}).get("username", ""),
                        }
                    detail = ""
                    try:
                        detail = str(resp.json().get("description", "") or "")
                    except Exception:
                        detail = (resp.text or "")[:120]
                    # Keep Telegram's status but include description — no local format gate.
                    return {
                        "success": False,
                        "error": f"HTTP {resp.status_code}" + (f": {detail}" if detail else ""),
                    }
            except Exception as exc:
                logger.debug("Telegram connector test failed: %s", exc)
                return {"success": False, "error": "Connection test failed"}

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
                logger.debug("Discord connector test failed: %s", exc)
                return {"success": False, "error": "Connection test failed"}

        if name == "slack":
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
                logger.debug("Slack connector test failed: %s", exc)
                return {"success": False, "error": "Connection test failed"}

        # Generic connectors cannot be tested remotely; report token presence.
        return {"success": True, "message": f"Token configured for {name}"}

    @router.post("/api/connectors/{name}/toggle")
    async def toggle_connector(name: str, req: ProviderToggleRequest) -> dict[str, str]:
        """Enable or disable a connector."""
        name = name.strip()
        config_store.set(f"connectors.{name}.enabled", req.enabled, category="connectors")
        return {"status": "ok"}

    return router
