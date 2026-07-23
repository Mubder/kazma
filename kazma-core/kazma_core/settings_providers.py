"""Provider settings service — extracted from settings_manager (S5)."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

__all__ = ["ProviderSettingsService"]

logger = logging.getLogger(__name__)

class ProviderSettingsService:
    """Service handling provider-specific settings, connections, and health checks."""

    def __init__(self, config_store: Any, registry: Any = None) -> None:
        self._cs = config_store
        if registry is None:
            from kazma_core.model_registry import ModelRegistry
            self._registry = ModelRegistry(config_store)
        else:
            self._registry = registry

    def get_all_providers(self) -> list[dict[str, Any]]:
        """List all configured providers."""
        return self._registry.list_providers()

    def add_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new provider."""
        return self._registry.upsert_provider(data)

    def delete_provider(self, name: str) -> None:
        """Delete a provider by name."""
        self._registry.delete_provider(name)

    def toggle_provider(self, name: str, enabled: bool) -> None:
        """Enable/disable a provider."""
        self._registry.toggle_provider(name, enabled)

    async def test_provider(self, name: str) -> dict[str, Any]:
        """Test a provider connection with a real HTTP call."""
        providers = self.get_all_providers()
        provider = None
        for p in providers:
            if p.get("name") == name:
                provider = p
                break
        if not provider:
            return {"success": False, "error": f"Provider '{name}' not found"}

        import httpx

        if name.lower() == "google":
            # Test Google Provider (either AI Studio or Vertex AI)
            try:
                client = self._registry.get_client_by_provider(name)
                if not client:
                    return {"success": False, "error": "Failed to construct Google Gemini provider client"}
                
                # Retrieve the authenticated HTTP client (resolves ADC or API Key)
                http_client = await client._get_client()
                
                # Perform a lightweight ping to the base URL
                start = time.monotonic()
                if getattr(client, "_use_ai_studio", False):
                    resp = await http_client.get("/models")
                    latency = int((time.monotonic() - start) * 1000)
                    if resp.status_code == 200:
                        self._update_provider_health(name, "healthy")
                        return {"success": True, "latency_ms": latency, "status_code": resp.status_code}
                    else:
                        self._update_provider_health(name, "degraded")
                        return {"success": False, "error": f"AI Studio returned HTTP {resp.status_code}", "latency_ms": latency}
                else:
                    # Vertex AI: just check if get_client succeeded and do a lightweight head/get to base_url to check network.
                    try:
                        resp = await http_client.get("")
                        latency = int((time.monotonic() - start) * 1000)
                        self._update_provider_health(name, "healthy")
                        return {"success": True, "latency_ms": latency, "status_code": resp.status_code}
                    except httpx.ConnectError:
                        self._update_provider_health(name, "down")
                        return {"success": False, "error": f"Cannot connect to Vertex AI at {client.config.base_url}"}
            except Exception as e:
                self._update_provider_health(name, "down")
                return {"success": False, "error": f"Google Provider test failed: {e}"}

        base_url = provider.get("base_url", "")
        api_key = provider.get("api_key", "")
        if not base_url:
            return {"success": False, "error": "No base URL configured"}

        start = time.monotonic()
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
                latency = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    # Update health
                    self._update_provider_health(name, "healthy")
                    return {"success": True, "latency_ms": latency, "status_code": resp.status_code}
                else:
                    self._update_provider_health(name, "degraded")
                    return {"success": False, "error": f"HTTP {resp.status_code}", "latency_ms": latency}
        except httpx.ConnectError:
            self._update_provider_health(name, "down")
            return {"success": False, "error": f"Cannot connect to {base_url}"}
        except Exception as e:
            self._update_provider_health(name, "down")
            return {"success": False, "error": str(e)}

    def get_provider_health(self, name: str) -> dict[str, Any]:
        """Get health status for a provider."""
        health = self._cs.get(f"providers.health.{name}", "unknown")
        last_check = self._cs.get(f"providers.health.{name}.last_check", "")
        return {"name": name, "health": health, "last_check": last_check}

    def _update_provider_health(self, name: str, status: str) -> None:
        """Update provider health status in store."""
        self._cs.set(f"providers.health.{name}", status, category="providers")
        self._cs.set(f"providers.health.{name}.last_check", datetime.now(UTC).isoformat(), category="providers")
        self._registry.set_provider_health(name, status)
