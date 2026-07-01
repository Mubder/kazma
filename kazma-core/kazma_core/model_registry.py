"""Singleton ModelRegistry — single source of truth for providers, models, and LLM clients.

This module replaces the former ``UnifiedModelRegistry`` with a true singleton
that owns provider configuration, API keys, and LLM client creation.

Singleton lifecycle::

    initialize_model_registry(config_store)  # create + deserialize
    get_model_registry()                     # retrieve
    reset_model_registry()                   # teardown (tests)

Backward-compatible storage keys:
- ``providers.list``
- ``providers.health.*``
- ``models.saved.*``
- ``models.defaults.*``
- ``llm.model``
- ``registry.active_provider``
- ``registry.active_model``
- ``registry.discovered_models``
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.providers import PROVIDER_PRESETS

logger = logging.getLogger(__name__)

_DEFAULT_TASKS = ("chat", "code", "summarize", "translate")
_PROFILE_FIELDS = ("base_url", "api_key", "model", "provider")

# ── Singleton lifecycle ──────────────────────────────────────────────

_registry: ModelRegistry | None = None


def initialize_model_registry(config_store: Any) -> ModelRegistry:
    """Create and return the module-level singleton ``ModelRegistry``.

    Calling this again replaces any existing instance.
    """
    global _registry
    _registry = ModelRegistry(config_store)
    _registry._deserialize()
    return _registry


def get_model_registry() -> ModelRegistry:
    """Return the singleton ``ModelRegistry``.

    Raises ``RuntimeError`` if ``initialize_model_registry`` has not been called.
    """
    if _registry is None:
        raise RuntimeError(
            "ModelRegistry not initialized. Call initialize_model_registry() first."
        )
    return _registry


def reset_model_registry() -> None:
    """Tear down the singleton (primarily for tests)."""
    global _registry
    _registry = None


# ── ModelRegistry ────────────────────────────────────────────────────


class ModelRegistry:
    """Singleton registry — the ONLY owner of provider config and LLM clients.

    All existing ``UnifiedModelRegistry`` methods are preserved for backward
    compatibility.  New methods add active-profile management, LLM client
    caching, and model discovery.
    """

    def __init__(self, config_store: Any) -> None:
        self._cs = config_store
        self._clients: dict[str, LLMProvider] = {}
        self._active_provider: str = ""
        self._active_model: str = ""
        self._discovered_models: dict[str, list[str]] = {}

    # ── Active profile management ──────────────────────────────────

    def get_active_profile(self) -> dict[str, str]:
        """Return the active provider profile.

        Returns a dict with keys ``provider``, ``base_url``, ``model``,
        ``api_key`` (masked).  Falls back to legacy ``llm.*`` keys when no
        active profile has been explicitly set.
        """
        provider_name = self._active_provider
        model = self._active_model

        # Try to resolve from stored providers first
        provider_entry = self.get_provider(provider_name) if provider_name else None

        base_url = ""
        api_key = ""

        if provider_entry:
            base_url = str(provider_entry.get("base_url", ""))
            api_key = str(provider_entry.get("api_key", ""))
        elif not provider_name or not provider_entry:
            # Fallback: legacy llm.* keys
            base_url = str(self._cs.get("llm.base_url", "") or "")
            api_key = str(self._cs.get("llm.api_key", "") or "")
            if not model:
                model = str(self._cs.get("llm.model", "") or "")
            if not provider_name:
                provider_name = "custom"

        masked_key = "***" if api_key else ""
        return {
            "provider": provider_name,
            "base_url": base_url,
            "model": model,
            "api_key": masked_key,
        }

    def set_active_provider(
        self,
        provider: str,
        base_url: str = "",
        model: str = "",
        api_key: str = "",
    ) -> dict[str, str]:
        """Switch the active provider.

        Persists to ConfigStore and invalidates any cached client for this
        provider.  Returns the normalized profile (api_key masked).
        """
        clean_provider = (provider or "").strip()
        if not clean_provider:
            return {"error": "Provider name is required"}

        self._active_provider = clean_provider

        # If explicit base_url/api_key provided, store them in the provider list
        if base_url or api_key:
            existing = self.get_provider(clean_provider)
            if existing:
                upsert_data: dict[str, Any] = {"name": clean_provider}
                if base_url:
                    upsert_data["base_url"] = base_url
                if api_key:
                    upsert_data["api_key"] = api_key
                self.upsert_provider(upsert_data)
            else:
                self.upsert_provider({
                    "name": clean_provider,
                    "base_url": base_url,
                    "api_key": api_key,
                })

        if model:
            self._active_model = model

        # Persist
        self._cs.set("registry.active_provider", clean_provider, category="registry")
        if self._active_model:
            self._cs.set("registry.active_model", self._active_model, category="registry")

        # Invalidate cached client
        self._clients.pop(clean_provider, None)

        return self.get_active_profile()

    def set_active_model(self, model: str) -> None:
        """Change just the model within the active provider."""
        self._active_model = (model or "").strip()
        self._cs.set("registry.active_model", self._active_model, category="registry")
        # Invalidate cached client so it picks up the new model
        if self._active_provider:
            self._clients.pop(self._active_provider, None)

    # ── LLM client management ──────────────────────────────────────

    def get_client(self, model: str | None = None) -> LLMProvider:
        """Return a pre-configured ``LLMProvider`` for the active profile.

        Clients are cached by provider name.  Pass *model* to override the
        model within the active provider (does not change the cached entry).
        """
        provider_name = self._active_provider or "custom"
        effective_model = model or self._active_model

        if model is None and provider_name in self._clients:
            return self._clients[provider_name]

        provider_entry = self.get_provider(provider_name)
        base_url = ""
        api_key = ""
        if provider_entry:
            base_url = str(provider_entry.get("base_url", ""))
            api_key = str(provider_entry.get("api_key", ""))

        if not base_url:
            # Fallback to legacy keys
            base_url = str(self._cs.get("llm.base_url", "") or "")
        if not api_key:
            api_key = str(self._cs.get("llm.api_key", "") or "")
        if not effective_model:
            effective_model = str(self._cs.get("llm.model", "") or "gpt-4o-mini")

        config = LLMConfig.from_dict({
            "base_url": base_url,
            "api_key": api_key,
            "model": effective_model,
        })
        client = LLMProvider(config)

        if model is None:
            self._clients[provider_name] = client

        return client

    def get_model(self, model_id: str) -> LLMProvider:
        """Return a pre-configured ``LLMProvider`` for a specific model ID.

        Looks up which provider owns *model_id* from the provider list, then
        builds a client.
        """
        clean_id = (model_id or "").strip()
        if not clean_id:
            raise ValueError("model_id is required")

        # Search providers for one that lists this model
        for provider in self.list_providers():
            models = self._normalize_models(provider.get("models", []))
            if clean_id in models:
                config = LLMConfig.from_dict({
                    "base_url": str(provider.get("base_url", "")),
                    "api_key": str(provider.get("api_key", "")),
                    "model": clean_id,
                })
                return LLMProvider(config)

        # Fallback: use active profile with overridden model
        return self.get_client(model=clean_id)

    def find_provider_for_model(self, model_id: str) -> dict[str, Any] | None:
        """Return the provider entry that owns *model_id*.

        Searches both manually-configured ``models`` and cached
        ``_discovered_models`` so that discovered models route correctly.
        Returns ``None`` if no provider claims the model.
        """
        clean_id = (model_id or "").strip()
        if not clean_id:
            return None
        for provider in self.list_providers():
            name = provider.get("name", "")
            manual = set(self._normalize_models(provider.get("models", [])))
            discovered = set(self._discovered_models.get(name, []))
            if clean_id in manual or clean_id in discovered:
                return dict(provider)
        return None

    # ── Model discovery ────────────────────────────────────────────

    async def discover_models(self, provider_name: str) -> list[str]:
        """Hit the provider's ``/models`` endpoint and return model IDs.

        Results are cached in ``self._discovered_models``.
        """
        clean_name = (provider_name or "").strip()
        if not clean_name:
            return []

        provider_entry = self.get_provider(clean_name)
        if not provider_entry:
            logger.warning("discover_models: provider %r not found", clean_name)
            return []

        base_url = str(provider_entry.get("base_url", ""))
        api_key = str(provider_entry.get("api_key", ""))

        if not base_url:
            logger.warning("discover_models: no base_url for %r", clean_name)
            return []

        # Resolve the models endpoint
        preset = PROVIDER_PRESETS.get(clean_name, {})
        models_path = preset.get("models_endpoint", "/models")

        # Build the full URL — strip /v1 if present, then append models path
        url = base_url.rstrip("/")
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        url = f"{url}{models_path}"

        # Build auth header
        auth_header_type = preset.get("auth_header", "Bearer")
        headers: dict[str, str] = {}
        if api_key and auth_header_type:
            headers["Authorization"] = f"{auth_header_type} {api_key}"
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            model_ids: list[str] = []
            for item in data.get("data", []):
                model_id = item.get("id", "")
                if model_id:
                    model_ids.append(str(model_id))

            model_ids.sort()
            self._discovered_models[clean_name] = model_ids
            return model_ids

        except Exception as exc:
            logger.error("discover_models failed for %r: %s", clean_name, exc)
            return self._discovered_models.get(clean_name, [])

    async def discover_all(self) -> dict[str, list[str]]:
        """Discover models for all enabled providers."""
        results: dict[str, list[str]] = {}
        for provider in self.list_providers():
            name = str(provider.get("name", "")).strip()
            if not name or not provider.get("enabled", True):
                continue
            results[name] = await self.discover_models(name)
        return results

    def get_discovered_models(self, provider_name: str | None = None) -> list[str]:
        """Return cached discovered models.

        If *provider_name* is given, returns models for that provider only.
        Otherwise returns all discovered models across all providers.
        """
        if provider_name:
            return list(self._discovered_models.get(provider_name.strip(), []))
        all_models: list[str] = []
        for models in self._discovered_models.values():
            all_models.extend(models)
        return sorted(set(all_models))

    # ── Persistence ────────────────────────────────────────────────

    def _deserialize(self) -> None:
        """Load active profile and discovered models from ConfigStore."""
        self._active_provider = str(self._cs.get("registry.active_provider", "") or "")
        self._active_model = str(self._cs.get("registry.active_model", "") or "")

        raw = self._cs.get("registry.discovered_models", {})
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = {}
        if isinstance(raw, dict):
            self._discovered_models = {
                str(k): [str(m) for m in v]
                for k, v in raw.items()
                if isinstance(v, list)
            }

        # Legacy fallback: if no active provider, check llm.* keys
        if not self._active_provider:
            legacy_url = str(self._cs.get("llm.base_url", "") or "")
            if legacy_url:
                self._active_provider = "custom"
                if not self._active_model:
                    self._active_model = str(self._cs.get("llm.model", "") or "")

    def serialize(self) -> None:
        """Save current state to ConfigStore."""
        self._cs.set("registry.active_provider", self._active_provider, category="registry")
        self._cs.set("registry.active_model", self._active_model, category="registry")
        self._cs.set(
            "registry.discovered_models",
            json.dumps(self._discovered_models),
            category="registry",
        )

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
                # Invalidate cached client for this provider
                self._clients.pop(name, None)
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
        self._clients.pop(clean_name, None)

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


# Backward-compatible alias for existing consumers.
UnifiedModelRegistry = ModelRegistry
