"""Unified provider and model profile registry backed by ConfigStore.

This module is the single source of truth for provider and model-profile
operations. It keeps backward compatibility with existing storage keys:

- ``providers.list``
- ``providers.health.*``
- ``models.saved.*``
- ``models.defaults.*``
- ``llm.model``
"""

from __future__ import annotations

import json
from typing import Any

from kazma_core.providers import PROVIDER_PRESETS

_DEFAULT_TASKS = ("chat", "code", "summarize", "translate")
_PROFILE_FIELDS = ("base_url", "api_key", "model", "provider")


class UnifiedModelRegistry:
    """Unified provider/model registry facade over ``ConfigStore``."""

    def __init__(self, config_store: Any) -> None:
        self._cs = config_store

    # ── Providers ──────────────────────────────────────────────────

    def list_providers(self) -> list[dict[str, Any]]:
        """Return all providers from ``providers.list`` or default presets."""
        providers = self._load_providers()
        if providers:
            return providers
        return self._default_provider_entries()

    def get_provider(self, name: str) -> dict[str, Any] | None:
        """Return provider entry by name."""
        clean_name = (name or "").strip()
        if not clean_name:
            return None
        for provider in self.list_providers():
            if provider.get("name") == clean_name:
                return dict(provider)
        return None

    def upsert_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a provider entry."""
        name = str(data.get("name", "")).strip()
        if not name:
            return {"error": "Provider name is required"}

        providers = self.list_providers()
        for provider in providers:
            if provider.get("name") == name:
                for key, value in data.items():
                    if key == "models":
                        provider["models"] = self._normalize_models(value)
                    elif key == "name":
                        continue
                    else:
                        provider[key] = value
                if not provider.get("display_name"):
                    provider["display_name"] = name
                self._save_providers(providers)
                return dict(provider)

        provider = {
            "name": name,
            "display_name": str(data.get("display_name") or name),
            "base_url": str(data.get("base_url") or ""),
            "api_key": str(data.get("api_key") or ""),
            "models": self._normalize_models(data.get("models", [])),
            "enabled": bool(data.get("enabled", True)),
            "health": str(data.get("health") or "unknown"),
        }
        providers.append(provider)
        self._save_providers(providers)
        return dict(provider)

    def delete_provider(self, name: str) -> None:
        """Delete a provider by name."""
        clean_name = (name or "").strip()
        if not clean_name:
            return
        providers = [p for p in self.list_providers() if p.get("name") != clean_name]
        self._save_providers(providers)

    def toggle_provider(self, name: str, enabled: bool) -> None:
        """Toggle provider enabled flag."""
        clean_name = (name or "").strip()
        providers = self.list_providers()
        for provider in providers:
            if provider.get("name") == clean_name:
                provider["enabled"] = bool(enabled)
                break
        self._save_providers(providers)

    def set_provider_health(self, name: str, status: str) -> None:
        """Update provider health value in ``providers.list`` if present."""
        clean_name = (name or "").strip()
        providers = self.list_providers()
        for provider in providers:
            if provider.get("name") == clean_name:
                provider["health"] = status
                self._save_providers(providers)
                return

    # ── Saved model profiles ───────────────────────────────────────

    def save_model_profile(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Save ``models.saved.{name}`` and return a masked profile."""
        clean_name = (name or "").strip()
        if not clean_name:
            return {"error": "Profile name is required"}

        profile: dict[str, Any] = {}
        for field in _PROFILE_FIELDS:
            if field in data:
                profile[field] = data[field]
            elif field != "provider":
                profile[field] = ""

        if not profile.get("provider"):
            profile["provider"] = "custom"
        profile["name"] = clean_name

        self._cs.set(f"models.saved.{clean_name}", profile, category="models")
        return self._mask_profile(profile)

    def list_model_profiles(self, *, mask_api_key: bool = True) -> list[dict[str, Any]]:
        """Return all saved model profiles."""
        category = self._cs.get_category("models")
        profiles: list[dict[str, Any]] = []
        for key, value in category.items():
            if not key.startswith("models.saved.") or not isinstance(value, dict):
                continue
            profile = dict(value)
            profile["name"] = key[len("models.saved."):]
            profiles.append(self._mask_profile(profile) if mask_api_key else profile)
        profiles.sort(key=lambda p: p.get("name", ""))
        return profiles

    def get_model_profile(self, name: str) -> dict[str, Any] | None:
        """Return one saved model profile (raw API key)."""
        clean_name = (name or "").strip()
        if not clean_name:
            return None
        profile = self._cs.get(f"models.saved.{clean_name}", None)
        return profile if isinstance(profile, dict) else None

    def delete_model_profile(self, name: str) -> bool:
        """Delete ``models.saved.{name}`` entry."""
        clean_name = (name or "").strip()
        if not clean_name:
            return False
        return bool(self._cs.delete(f"models.saved.{clean_name}"))

    # ── Unified options ────────────────────────────────────────────

    def list_unified_options(self) -> dict[str, Any]:
        """Return unified model/provider options with profile metadata."""
        provider_entries = self.list_providers()
        profiles = self.list_model_profiles(mask_api_key=True)

        models: set[str] = set()
        provider_names: set[str] = set()
        provider_models: dict[str, list[str]] = {}

        for provider in provider_entries:
            name = str(provider.get("name") or "").strip()
            if name:
                provider_names.add(name)
            p_models = self._normalize_models(provider.get("models", []))
            if p_models and name:
                provider_models[name] = p_models
            models.update(p_models)

        for profile in profiles:
            model_name = str(profile.get("model") or "").strip()
            provider_name = str(profile.get("provider") or "").strip()
            if model_name:
                models.add(model_name)
            if provider_name:
                provider_names.add(provider_name)

        llm_model = str(self._cs.get("llm.model", "") or "").strip()
        if llm_model:
            models.add(llm_model)

        yaml_default_model = str(self._cs.get("models.default", "") or "").strip()
        if yaml_default_model:
            models.add(yaml_default_model)

        task_defaults = self._collect_task_defaults()
        models.update(task_defaults.values())

        for model_name in self._extract_registry_models(self._cs.get("models.registry", [])):
            models.add(model_name)

        return {
            "models": sorted(m for m in models if m),
            "providers": sorted(p for p in provider_names if p),
            "provider_entries": provider_entries,
            "provider_models": provider_models,
            "profiles": profiles,
            "defaults": {
                "llm_model": llm_model,
                "task_models": task_defaults,
            },
        }

    # ── Internal helpers ───────────────────────────────────────────

    def _load_providers(self) -> list[dict[str, Any]]:
        raw = self._cs.get("providers.list", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = []
        if not isinstance(raw, list):
            return []
        return [self._normalize_provider_entry(item) for item in raw if isinstance(item, dict)]

    def _save_providers(self, providers: list[dict[str, Any]]) -> None:
        self._cs.set("providers.list", json.dumps(providers), category="providers")

    def _default_provider_entries(self) -> list[dict[str, Any]]:
        providers: list[dict[str, Any]] = []
        for key, preset in PROVIDER_PRESETS.items():
            if key == "custom":
                continue
            providers.append(
                {
                    "name": key,
                    "display_name": preset.get("name", key),
                    "base_url": preset.get("base_url", ""),
                    "api_key": "",
                    "models": [],
                    "enabled": False,
                    "health": "unknown",
                }
            )
        return providers

    def _normalize_provider_entry(self, provider: dict[str, Any]) -> dict[str, Any]:
        name = str(provider.get("name") or "").strip()
        return {
            "name": name,
            "display_name": str(provider.get("display_name") or name),
            "base_url": str(provider.get("base_url") or ""),
            "api_key": str(provider.get("api_key") or ""),
            "models": self._normalize_models(provider.get("models", [])),
            "enabled": bool(provider.get("enabled", True)),
            "health": str(provider.get("health") or "unknown"),
        }

    def _normalize_models(self, models: Any) -> list[str]:
        if isinstance(models, list):
            return sorted({str(model).strip() for model in models if str(model).strip()})
        return []

    def _mask_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        safe = dict(profile)
        if safe.get("api_key"):
            safe["api_key"] = "***"
        return safe

    def _collect_task_defaults(self) -> dict[str, str]:
        defaults: dict[str, str] = {}
        category = self._cs.get_category("models")
        for key, value in category.items():
            if key.startswith("models.defaults.") and isinstance(value, str) and value.strip():
                defaults[key[len("models.defaults."):]] = value.strip()

        for task in _DEFAULT_TASKS:
            if task not in defaults:
                value = self._cs.get(f"models.defaults.{task}", "")
                if isinstance(value, str) and value.strip():
                    defaults[task] = value.strip()
        return defaults

    def _extract_registry_models(self, registry: Any) -> list[str]:
        if not isinstance(registry, list):
            return []
        names: list[str] = []
        for entry in registry:
            if isinstance(entry, str) and entry.strip():
                names.append(entry.strip())
                continue
            if isinstance(entry, dict):
                for key in ("name", "model", "id"):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())
                        break
        return names
