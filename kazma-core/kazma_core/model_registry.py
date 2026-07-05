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
from typing import Any, TYPE_CHECKING

import httpx

from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.providers import PROVIDER_PRESETS

if TYPE_CHECKING:
    from kazma_core.config_store import ConfigStore

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

    def __init__(self, config_store: ConfigStore | Any) -> None:
        self._config_store = config_store
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
            base_url = str(self._config_store.get("llm.base_url", "") or "")
            api_key = str(self._config_store.get("llm.api_key", "") or "")
            if not model:
                model = str(self._config_store.get("llm.model", "") or "")
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
        self._config_store.set("registry.active_provider", clean_provider, category="registry")
        if self._active_model:
            self._config_store.set("registry.active_model", self._active_model, category="registry")

        # Invalidate cached client
        self._clients.pop(clean_provider, None)

        return self.get_active_profile()

    def set_active_model(self, model: str) -> None:
        """Change the active model, auto-switching provider if needed.

        If the model belongs to a different provider than the currently
        active one, the active provider is updated so the LLM client
        points to the correct API endpoint.
        """
        clean_model = (model or "").strip()
        self._active_model = clean_model
        self._config_store.set("registry.active_model", self._active_model, category="registry")

        # Auto-switch provider if this model belongs to a different one
        owner = self.find_provider_for_model(clean_model)
        if owner:
            owner_name = owner.get("name", "")
            if owner_name and owner_name != self._active_provider:
                logger.info(
                    "Auto-switching active provider '%s' -> '%s' (model=%s)",
                    self._active_provider,
                    owner_name,
                    clean_model,
                )
                self._active_provider = owner_name
                self._config_store.set("registry.active_provider", owner_name, category="registry")

        # Invalidate ALL cached clients so they rebuild with correct URL+model
        self._clients.clear()

    # ── LLM client management ──────────────────────────────────────

    def get_client(self, model: str | None = None) -> LLMProvider:
        """Return a pre-configured ``LLMProvider`` for the active profile.

        Clients are cached by provider name.  Pass *model* to override the
        model within the active provider (does not change the cached entry).

        Safety: if the model belongs to a DIFFERENT provider than the
        active one, the provider is auto-corrected so that (for example)
        NVIDIA model names are never sent to DeepSeek's API — or
        ``mimo-v2.5-pro`` to DeepSeek.  This guard fires both when
        *model* is None (using the active model) and when it is passed
        as an override.
        """
        provider_name = self._active_provider or "custom"
        effective_model = model or self._active_model

        # Safety: if the model belongs to a DIFFERENT provider,
        # auto-correct the provider so we don't send cross-provider
        # model names (e.g. NVIDIA model to DeepSeek's API).
        # This runs regardless of whether *model* was passed — the
        # previous `not model` guard defeated this safety net exactly
        # when the swarm path (which always passes model=) needed it.
        if effective_model:
            owner = self.find_provider_for_model(effective_model)
            if owner:
                owner_name = owner.get("name", "")
                if owner_name and owner_name.lower() != provider_name.lower():
                    logger.warning(
                        "Model '%s' belongs to provider '%s' but active provider is '%s'. "
                        "Auto-correcting to the owning provider.",
                        effective_model,
                        owner_name,
                        provider_name,
                    )
                    provider_name = owner_name
                    # Only persist the active-provider change when the
                    # caller did not explicitly pass a model override —
                    # otherwise we'd hijack the global active provider
                    # for every per-call model request.
                    if model is None:
                        self._active_provider = owner_name
                        self._config_store.set("registry.active_provider", owner_name, category="registry")
            else:
                # Model not found in any provider — warn so misconfigs
                # are visible instead of silently routing to a random
                # active provider's endpoint.
                logger.warning(
                    "Model '%s' not found in any configured provider. "
                    "Falling back to active provider '%s'.",
                    effective_model, provider_name,
                )

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
            base_url = str(self._config_store.get("llm.base_url", "") or "")
        if not api_key:
            api_key = str(self._config_store.get("llm.api_key", "") or "")
        if not effective_model:
            effective_model = str(self._config_store.get("llm.model", "") or "gpt-4o-mini")

        config = LLMConfig.from_dict({
            "base_url": base_url,
            "api_key": api_key,
            "model": effective_model,
        })

        # ── Google Vertex AI → use GeminiProvider with ADC auth ──
        if provider_name.lower() == "google":
            from kazma_core.google_llm import GeminiProvider

            location = str(
                provider_entry.get("location", "")
                if provider_entry else ""
            ) or "us-central1"
            project_id = str(
                provider_entry.get("project_id", "")
                if provider_entry else ""
            )
            client = GeminiProvider(config, project_id=project_id, location=location)
        else:
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

        # Search providers for one that lists this model (manual + discovered)
        owner = self.find_provider_for_model(clean_id)
        if owner:
            owner_name = str(owner.get("name", "")).lower()
            config = LLMConfig.from_dict({
                "base_url": str(owner.get("base_url", "")),
                "api_key": str(owner.get("api_key", "")),
                "model": clean_id,
            })
            if owner_name == "google":
                from kazma_core.google_llm import GeminiProvider
                return GeminiProvider(
                    config,
                    project_id=str(owner.get("project_id", "")),
                    location=str(owner.get("location", "")) or "us-central1",
                )
            return LLMProvider(config)

        # Fallback: use active profile with overridden model
        return self.get_client(model=clean_id)

    def get_client_by_provider(
        self, provider_name: str, model: str | None = None
    ) -> LLMProvider | None:
        """Return an ``LLMProvider`` client for a SPECIFIC provider.

        Unlike :meth:`get_client` (which uses the globally-active
        provider), this pins to *provider_name* regardless of the
        active setting.  Returns ``None`` if the named provider is not
        found or misconfigured.
        """
        entry = self.get_provider(provider_name)
        if not entry:
            return None
        effective_model = model or str(entry.get("model", "") or "")
        if not effective_model:
            # Try the active model as a fallback
            effective_model = self._active_model or "gpt-4o-mini"
        config = LLMConfig.from_dict({
            "base_url": str(entry.get("base_url", "")),
            "api_key": str(entry.get("api_key", "")),
            "model": effective_model,
        })
        if provider_name.lower() == "google":
            from kazma_core.google_llm import GeminiProvider
            return GeminiProvider(
                config,
                project_id=str(entry.get("project_id", "")),
                location=str(entry.get("location", "")) or "us-central1",
            )
        return LLMProvider(config)

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
            # Google Vertex AI has no static /models endpoint — models are
            # hardcoded because the base URL is computed per project/location.
            if clean_name == "google":
                from kazma_core.providers import GEMINI_MODELS
                self._discovered_models[clean_name] = list(GEMINI_MODELS)
                return self._discovered_models[clean_name]
            logger.warning("discover_models: no base_url for %r", clean_name)
            return []

        # Resolve the models endpoint
        preset = PROVIDER_PRESETS.get(clean_name, {})
        models_path = preset.get("models_endpoint", "/models")

        # Build the full URL — base_url + models_path.
        # Most OpenAI-compatible APIs expect {base_url}/models where base_url
        # already includes the /v1 suffix (e.g. https://api.openai.com/v1/models).
        url = f"{base_url.rstrip('/')}{models_path}"

        # SSRF guard: prevent the server from fetching internal/private URLs
        # (e.g. cloud metadata 169.254.169.254, localhost services).
        try:
            from kazma_core.security.ssrf import SSRFError, validate_url
            validate_url(url, block_unresolved=True, allow_private=True)
        except SSRFError as exc:
            logger.warning("discover_models: SSRF blocked %r for %r: %s", url, clean_name, exc)
            return []
        except Exception:
            logger.warning("discover_models: SSRF validation unavailable — blocking request for safety")
            return []

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
        self._active_provider = str(self._config_store.get("registry.active_provider", "") or "")
        self._active_model = str(self._config_store.get("registry.active_model", "") or "")

        raw = self._config_store.get("registry.discovered_models", {})
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
            legacy_url = str(self._config_store.get("llm.base_url", "") or "")
            if legacy_url:
                self._active_provider = "custom"
                if not self._active_model:
                    self._active_model = str(self._config_store.get("llm.model", "") or "")

    def serialize(self) -> None:
        """Save current state to ConfigStore."""
        self._config_store.set("registry.active_provider", self._active_provider, category="registry")
        self._config_store.set("registry.active_model", self._active_model, category="registry")
        self._config_store.set(
            "registry.discovered_models",
            json.dumps(self._discovered_models),
            category="registry",
        )

    # ── Providers ──────────────────────────────────────────────────

    def list_providers(self) -> list[dict[str, Any]]:
        """Return all providers, merging stored entries with preset defaults.

        Preset providers that don't exist in the stored list are included
        from their default entries.  Stored values always take priority
        (preserving user customisations like api_key, enabled status).
        """
        stored = self._load_providers()
        defaults = self._default_provider_entries()
        stored_by_name = {p.get("name", ""): p for p in stored}
        defaults_by_name = {p.get("name", ""): p for p in defaults}

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Start with stored providers (user customisations win)
        for p in stored:
            name = p.get("name", "")
            if name and name not in seen:
                merged.append(p)
                seen.add(name)

        # Fill in any preset defaults not in the stored list
        for p in defaults:
            name = p.get("name", "")
            if name and name not in seen:
                merged.append(p)
                seen.add(name)

        return merged

    def get_provider(self, name: str) -> dict[str, Any] | None:
        """Return provider entry by name (fuzzy, case-insensitive matching).

        Resolution order:
            1. Exact case-insensitive match on ``name``
            2. Exact case-insensitive match on ``display_name``
            3. Substring match: ``name`` contains the query (e.g.
               ``"Xiaomi MiMo"`` matches query ``"xiaomi"``)

        This tolerates mismatches like a YAML worker declaring
        ``provider: xiaomi`` while the DB stores the provider as
        ``"Xiaomi MiMo"``.
        """
        clean_name = (name or "").strip().lower()
        if not clean_name:
            return None
        # Pass 1: exact case-insensitive match on name
        for provider in self.list_providers():
            p_name = str(provider.get("name", "")).strip().lower()
            if p_name == clean_name:
                return dict(provider)
        # Pass 2: exact match on display_name
        for provider in self.list_providers():
            p_display = str(provider.get("display_name", "")).strip().lower()
            if p_display == clean_name:
                return dict(provider)
        # Pass 3: substring match (query is contained in provider name)
        for provider in self.list_providers():
            p_name = str(provider.get("name", "")).strip().lower()
            if clean_name in p_name:
                return dict(provider)
        return None

    def upsert_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        """Insert or update a provider entry.

        If the incoming ``api_key`` is a masked placeholder (e.g.
        ``****1234``), the existing key is preserved. If there is no
        existing key, the placeholder is rejected (the caller must
        provide a real key).
        """
        name = str(data.get("name", "")).strip()
        if not name:
            return {"error": "Provider name is required"}

        # Defense-in-depth: if the API key looks masked, try to preserve
        # the existing one. If there's no existing key, reject the masked
        # value rather than storing garbage.
        incoming_key = str(data.get("api_key", "") or "")
        if incoming_key and ("****" in incoming_key or incoming_key == "***"):
            existing_provider = self.get_provider(name)
            if existing_provider and existing_provider.get("api_key"):
                data["api_key"] = existing_provider["api_key"]
            else:
                data["api_key"] = ""  # Clear the masked placeholder

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

    # ── Selected models (user-curated subset of discovered) ─────────

    def get_selected_models(self, provider_name: str) -> list[str]:
        """Return the user-selected models for a provider.

        Stored under ``providers.{name}.selected_models`` in ConfigStore.
        Returns an empty list when nothing has been explicitly selected.
        """
        clean = (provider_name or "").strip()
        if not clean:
            return []
        raw = self._config_store.get(f"providers.{clean}.selected_models", [])
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                raw = []
        if isinstance(raw, list):
            return [str(m) for m in raw]
        return []

    def set_selected_models(self, provider_name: str, models: list[str]) -> None:
        """Set the user-selected models for a provider."""
        clean = (provider_name or "").strip()
        if not clean:
            return
        self._config_store.set(
            f"providers.{clean}.selected_models",
            [str(m) for m in models],
            category="providers",
        )

    def get_visible_models(self, provider_name: str) -> list[str]:
        """Return models that should appear in dropdowns.

        If the user has explicitly selected models, returns only those.
        Otherwise returns all discovered + manual models (backward-compatible).
        """
        selected = self.get_selected_models(provider_name)
        if selected:
            return selected
        discovered = self.get_discovered_models(provider_name)
        provider = self.get_provider(provider_name)
        manual = self._normalize_models(provider.get("models", [])) if provider else []
        return sorted(set(discovered) | set(manual))

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

        self._config_store.set(f"models.saved.{clean_name}", profile, category="models")
        return self._mask_profile(profile)

    def list_model_profiles(self, *, mask_api_key: bool = True) -> list[dict[str, Any]]:
        """Return all saved model profiles."""
        category = self._config_store.get_category("models")
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
        profile = self._config_store.get(f"models.saved.{clean_name}", None)
        return profile if isinstance(profile, dict) else None

    def delete_model_profile(self, name: str) -> bool:
        """Delete ``models.saved.{name}`` entry."""
        clean_name = (name or "").strip()
        if not clean_name:
            return False
        return bool(self._config_store.delete(f"models.saved.{clean_name}"))

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

        llm_model = str(self._config_store.get("llm.model", "") or "").strip()
        if llm_model:
            models.add(llm_model)

        yaml_default_model = str(self._config_store.get("models.default", "") or "").strip()
        if yaml_default_model:
            models.add(yaml_default_model)

        task_defaults = self._collect_task_defaults()
        models.update(task_defaults.values())

        for model_name in self._extract_registry_models(self._config_store.get("models.registry", [])):
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
        """Load the provider list from ConfigStore.

        Handles both the current format (a list stored directly) and a
        legacy double-encoded JSON string (from the previous _save_providers
        that pre-encoded with json.dumps).

        **This is a pure read operation — it NEVER writes.** The previous
        version had a migration step that called ``_save_providers()``
        during load; if parsing failed, it wrote an empty list over the
        real data, destroying it permanently. That will never happen again.
        """
        raw = self._config_store.get("providers.list", [])
        # Legacy: ConfigStore.get returns a JSON-parsed value, but the
        # old _save_providers stored json.dumps(list) — so after one
        # json.loads inside ConfigStore.get we may still have a string.
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "[ModelRegistry] providers.list is a string but could "
                    "not be parsed — returning empty list (data NOT overwritten)"
                )
                return []
            if not isinstance(raw, list):
                return []
            # NOTE: We do NOT migrate (write) here. The data is parsed
            # correctly in-memory on every load. Migration happens only
            # on the next explicit _save_providers() call (upsert, etc.).
            logger.debug(
                "[ModelRegistry] Loaded providers from legacy double-encoded "
                "format (in-memory only; will migrate on next save)"
            )
        if not isinstance(raw, list):
            return []
        return [self._normalize_provider_entry(item) for item in raw if isinstance(item, dict)]

    def _save_providers(self, providers: list[dict[str, Any]]) -> None:
        # ConfigStore.set() already JSON-serializes the value — do not
        # pre-encode, or the stored string gets double-JSON-encoded
        # (str of a str).  The previous json.dumps here caused every
        # read to need two json.loads calls to recover the list.
        self._config_store.set("providers.list", providers, category="providers")

    def _default_provider_entries(self) -> list[dict[str, Any]]:
        providers: list[dict[str, Any]] = []
        for key, preset in PROVIDER_PRESETS.items():
            if key == "custom":
                continue
            enabled = key == "google"  # Google auto-enabled (ADC, no key needed)
            models: list[str] = []
            project_id = ""
            location = "us-central1"
            if key == "google":
                from kazma_core.providers import GEMINI_MODELS
                models = list(GEMINI_MODELS)
                # Try to auto-detect project from ADC credentials or gcloud config
                try:
                    import json, os
                    from pathlib import Path
                    # Check ADC credentials file
                    adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
                    if not adc_path.exists():
                        adc_path = Path(os.environ.get("APPDATA", "")) / "gcloud" / "application_default_credentials.json"
                    if adc_path.exists():
                        data = json.loads(adc_path.read_text(encoding="utf-8"))
                        project_id = data.get("quota_project_id") or data.get("project_id") or ""
                    # Check gcloud config_default file
                    if not project_id:
                        config_path = Path.home() / ".config" / "gcloud" / "configurations" / "config_default"
                        if not config_path.exists():
                            config_path = Path(os.environ.get("APPDATA", "")) / "gcloud" / "configurations" / "config_default"
                        if config_path.exists():
                            for line in config_path.read_text(encoding="utf-8").splitlines():
                                if line.strip().startswith("project"):
                                    project_id = line.partition("=")[2].strip()
                                    break
                except Exception as exc:
                    logger.debug("gcloud project auto-detection failed: %s", exc)
            providers.append(
                {
                    "name": key,
                    "display_name": preset.get("name", key),
                    "base_url": preset.get("base_url", ""),
                    "api_key": "",
                    "models": models,
                    "enabled": enabled,
                    "health": "unknown",
                    "project_id": project_id,
                    "location": location,
                }
            )
        return providers

    def _seed_missing_presets(self) -> None:
        """Ensure every preset provider exists in the stored list.

        New providers (e.g. Google Gemini) added in a code update will
        automatically appear in the UI without requiring manual setup.
        Existing customisations (api_key, enabled status) are preserved.
        """
        stored = self._load_providers()
        stored_by_name = {p.get("name", ""): p for p in stored} if stored else {}
        changed = False

        for key, preset in PROVIDER_PRESETS.items():
            if key == "custom":
                continue
            if key in stored_by_name:
                continue  # already exists — don't overwrite user changes

            # Build a new entry from the preset
            entry: dict[str, Any] = {
                "name": key,
                "display_name": preset.get("name", key),
                "base_url": preset.get("base_url", ""),
                "api_key": "",
                "models": [],
                "enabled": key == "google",  # auto-enable Google (ADC, no key needed)
                "health": "unknown",
            }

            # Pre-populate models for providers that hardcode them
            if key == "google":
                from kazma_core.providers import GEMINI_MODELS
                entry["models"] = list(GEMINI_MODELS)
                # Auto-detect project from ADC credentials or gcloud config
                entry["project_id"] = ""
                entry["location"] = "us-central1"
                try:
                    import json, os
                    from pathlib import Path
                    # Check ADC credentials file
                    adc_path = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
                    if not adc_path.exists():
                        adc_path = Path(os.environ.get("APPDATA", "")) / "gcloud" / "application_default_credentials.json"
                    if adc_path.exists():
                        data = json.loads(adc_path.read_text(encoding="utf-8"))
                        entry["project_id"] = data.get("quota_project_id") or data.get("project_id") or ""
                    # Check gcloud config_default file
                    if not entry["project_id"]:
                        config_path = Path.home() / ".config" / "gcloud" / "configurations" / "config_default"
                        if not config_path.exists():
                            config_path = Path(os.environ.get("APPDATA", "")) / "gcloud" / "configurations" / "config_default"
                        if config_path.exists():
                            for line in config_path.read_text(encoding="utf-8").splitlines():
                                if line.strip().startswith("project"):
                                    entry["project_id"] = line.partition("=")[2].strip()
                                    break
                except Exception as exc:
                    logger.debug("gcloud project auto-detection failed: %s", exc)

            stored.append(entry)
            changed = True
            logger.info("[ModelRegistry] Seeded new preset provider: %s", key)

        if changed:
            self._save_providers(stored)

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
            "project_id": str(provider.get("project_id") or ""),
            "location": str(provider.get("location") or "us-central1"),
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
        category = self._config_store.get_category("models")
        for key, value in category.items():
            if key.startswith("models.defaults.") and isinstance(value, str) and value.strip():
                defaults[key[len("models.defaults."):]] = value.strip()

        for task in _DEFAULT_TASKS:
            if task not in defaults:
                value = self._config_store.get(f"models.defaults.{task}", "")
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
