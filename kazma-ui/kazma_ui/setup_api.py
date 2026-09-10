"""Hands 0.11 first-run wall — status + bootstrap. No secrets in responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from kazma_core.errors import safe_error
from kazma_core.providers import PROVIDER_PRESETS

logger = logging.getLogger(__name__)

__all__ = ["compute_setup_status", "create_setup_router"]

_LOCAL = frozenset({"ollama", "lm-studio", "lmstudio"})


def _has_key(value: Any) -> bool:
    # Masked "***" means a real key exists (get_active_profile never echoes it).
    return bool(str(value or "").strip())


def compute_setup_status(registry: Any | None = None) -> dict[str, Any]:
    """Booleans only — never echo api_key material."""
    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()

    profile = registry.get_active_profile() if hasattr(registry, "get_active_profile") else {}
    provider = str(profile.get("provider") or "").strip().lower()
    has_model = bool(str(profile.get("model") or "").strip())
    has_provider = _has_key(profile.get("api_key")) or provider in _LOCAL

    if not has_provider and hasattr(registry, "list_providers"):
        for entry in registry.list_providers() or []:
            name = str(entry.get("name") or "").strip().lower()
            if _has_key(entry.get("api_key")) or name in _LOCAL:
                has_provider = True
                break

    vault_configured = False
    try:
        from kazma_core.security.vault import get_vault

        vault_configured = get_vault() is not None
    except Exception:
        vault_configured = False

    presets = [
        {"id": pid, "name": str(meta.get("name") or pid)}
        for pid, meta in PROVIDER_PRESETS.items()
    ]
    return {
        "ready": bool(has_provider and has_model),
        "has_provider": bool(has_provider),
        "has_model": has_model,
        "vault_configured": vault_configured,
        "presets": presets,
    }


class BootstrapBody(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = ""
    model: str = ""


def create_setup_router() -> APIRouter:
    router = APIRouter(tags=["setup"])

    @router.get("/api/setup/status")
    async def setup_status() -> dict[str, Any]:
        return compute_setup_status()

    @router.post("/api/setup/bootstrap")
    async def setup_bootstrap(body: BootstrapBody, request: Request) -> dict[str, Any]:
        del request  # auth is middleware; CSRF is middleware
        pid = body.provider.strip().lower()
        preset = PROVIDER_PRESETS.get(pid)
        if preset is None:
            return {"error": "unknown_provider", "ready": False}

        api_key = body.api_key.strip()
        if api_key in {"***", "••••"}:
            api_key = ""
        if pid not in _LOCAL and not api_key:
            return {"error": "api_key_required", "ready": False}

        try:
            from kazma_core.model_registry import get_model_registry
            from kazma_core.runtime.model_switch import (
                switch_active_model,
                switch_active_provider,
            )

            registry = get_model_registry()
            upsert = {
                "name": pid,
                "display_name": str(preset.get("name") or pid),
                "base_url": str(preset.get("base_url") or ""),
                "api_key": api_key or ("local" if pid in _LOCAL else ""),
                "enabled": True,
            }
            if body.model.strip():
                upsert["models"] = [body.model.strip()]
            result = registry.upsert_provider(upsert)
            if isinstance(result, dict) and result.get("error"):
                return {"error": str(result["error"]), "ready": False}

            if body.model.strip():
                switched = switch_active_model(body.model.strip(), registry=registry)
            else:
                switched = switch_active_provider(pid, api_key=api_key, registry=registry)
            if not switched.ok:
                return {
                    "error": switched.error or "switch_failed",
                    "ready": False,
                }
        except Exception as exc:
            logger.warning("[setup] bootstrap failed: %s", exc)
            return {"error": str(safe_error(exc)), "ready": False}

        status = compute_setup_status()
        if "api_key" in status:
            status = {k: v for k, v in status.items() if k != "api_key"}
        return status

    return router
