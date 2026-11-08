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

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from kazma_core.http_tls import shared_ssl_context
from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.model_details import from_listing
from kazma_core.provider_adapters import build_client
from kazma_core.providers import PROVIDER_PRESETS
from kazma_core.runtime.live_llm import coerce_api_key

__all__ = ["ModelRegistry", "get_model_registry", "initialize_model_registry", "lookup_context_window", "peek_model_registry", "reset_model_registry"]

if TYPE_CHECKING:
    from kazma_core.config_store import ConfigStore

logger = logging.getLogger(__name__)

# Recognized task kinds for per-task default-model overrides. Extended beyond
# the legacy 4 to cover the swarm's TaskProfile kinds (research/vision/general/fast).
_DEFAULT_TASKS = ("chat", "code", "summarize", "translate", "research", "vision", "general", "fast")
_PROFILE_FIELDS = ("base_url", "api_key", "model", "provider")

def lookup_context_window(model: str | None) -> int | None:
    """The owner's context window for *model* (ConfigStore
    ``models.context_window.<model>``), or ``None``. Never raises.

    There is no built-in table any more (2026-10-18): it guessed sizes by
    model-name prefix (deepseek 64,000, gpt-4o 128,000...), went stale, and
    capped models below the window the owner had set. A model that really is
    smaller gets its own entry here.
    """
    if not model:
        return None
    model_id = str(model).strip()
    if not model_id:
        return None
    override = _owner_setting(f"models.context_window.{model_id}")
    if override is not None:
        try:
            return max(1024, int(override))
        except (TypeError, ValueError):
            pass
    return None


def _owner_setting(key: str) -> Any:
    """A per-model setting the owner set (``models.<what>.<model>``), or None,
    also while the settings store cannot answer. Never raises."""
    try:
        from kazma_core.config_store import get_config_store

        return get_config_store().get(key)
    except Exception:  # noqa: BLE001 -- the store away: the default applies
        logger.debug("[model_registry] %s unreadable", key, exc_info=True)
        return None

#: The output limit of a step that may call tools, when the model has none of
#: its own (``models.max_output_tokens.<model>``).
TOOL_STEP_OUTPUT_TOKENS = 8192


def tool_step_output_limit(model: str | None) -> int:
    """The output limit of a supervisor step that offers tools: the owner's
    word for *model* (``models.max_output_tokens.<model>``, 1,024 to
    200,000), else :data:`TOOL_STEP_OUTPUT_TOKENS`. Never raises.

    8,192 for every model cut eight file writes off mid-argument in the
    functional audit (resilience plan D2): a model that writes more in one
    answer gets its own limit here. A settings read: callers on the loop use
    ``asyncio.to_thread``.
    """
    model_id = str(model or "").strip()
    override = _owner_setting(f"models.max_output_tokens.{model_id}") if model_id else None
    if override is not None:
        try:
            return max(1024, min(200_000, int(override)))
        except (TypeError, ValueError):
            pass
    return TOOL_STEP_OUTPUT_TOKENS


# ── Singleton lifecycle ──────────────────────────────────────────────

_registry: ModelRegistry | None = None


def initialize_model_registry(config_store: Any) -> ModelRegistry:
    """Create and return the module-level singleton ``ModelRegistry``.

    Calling this again replaces any existing instance.
    """
    global _registry
    _registry = ModelRegistry(config_store)
    _registry._deserialize()
    _registry._seed_missing_presets()
    return _registry


def get_model_registry() -> ModelRegistry:
    """Return the singleton ``ModelRegistry``.

    Fallback: auto-initializes via ``get_config_store()`` if not yet initialized.
    """
    global _registry
    if _registry is None:
        try:
            from kazma_core.config_store import get_config_store

            _registry = initialize_model_registry(get_config_store())
        except Exception as exc:
            raise RuntimeError(
                "ModelRegistry not initialized. Call initialize_model_registry() first."
            ) from exc
    return _registry


def peek_model_registry() -> ModelRegistry | None:
    """The registry if this process made one; never makes one. Shutdown only
    closes what exists (tests/test_shutdown_builds_nothing.py)."""
    return _registry


def reset_model_registry() -> None:
    """Tear down the singleton (primarily for tests)."""
    global _registry
    _registry = None


# ── Model discovery's answer ─────────────────────────────────────────


@dataclass
class DiscoveryResult:
    """What asking a provider for its models found (FA-107).

    ``ok`` False: the provider could not be asked or did not answer with a
    list; ``error`` is a stable code (``not_found``, ``no_base_url``,
    ``blocked_url``, ``unreachable``, ``http_<status>``, ``bad_answer``) the
    page words in its language, ``detail`` an English sentence, and
    ``cached`` the list an earlier discovery found -- never presented as
    found now. ``source`` is ``provider``, or ``built_in`` where the provider
    has no models endpoint (Vertex AI).
    """

    ok: bool
    models: list[str] = field(default_factory=list)
    error: str = ""
    detail: str = ""
    cached: list[str] = field(default_factory=list)
    source: str = "provider"

    @classmethod
    def failed(cls, error: str, detail: str, *, cached: list[str] | None = None) -> DiscoveryResult:
        return cls(False, [], error, detail, list(cached or []))


# ── ModelRegistry ────────────────────────────────────────────────────


class ModelRegistry:
    """Singleton registry — the ONLY owner of provider config and LLM clients.

    All existing ``UnifiedModelRegistry`` methods are preserved for backward
    compatibility.  New methods add active-profile management, LLM client
    caching, and model discovery.
    """

    #: Details a discovery found, until it saves them (providers stage 3).
    _pending_details: dict[str, dict[str, dict[str, Any]]]

    def __init__(self, config_store: ConfigStore | Any) -> None:
        self._config_store = config_store
        self._clients: dict[str, LLMProvider] = {}
        # Every client this registry has ever built (superset of _clients'
        # values). get_model / get_client_by_provider build one-off clients for
        # the swarm path that are NOT entered into _clients, so close() iterating
        # only _clients leaked their httpx connection pools (audit finding).
        self._all_clients: set[LLMProvider] = set()
        self._client_providers: dict[LLMProvider, str] = {}
        self._active_provider: str = ""
        self._active_model: str = ""
        self._discovered_models: dict[str, list[str]] = {}
        self._pending_details = {}
        # Reentrant lock guards read-modify-write of the active
        # provider/model and the client cache. This registry is a
        # process-wide singleton consumed concurrently by the swarm engine
        # and gateway; without a lock, concurrent get_client() calls can
        # interleave the provider auto-correction path. RLock because some
        # methods (e.g. set_active_* -> get_active_profile) re-enter.
        self._lock = threading.RLock()
        # Bumped by every change to the providers or the active profile. A
        # cache of clients built here (the failover caches) is good only for
        # the generation it was built at (FA-106: resilient_chat kept a
        # provider's old key and base URL until a restart).
        self._generation = 0
        # The owner's model prices are in every client built (model_prices):
        # a write to them, by any path (Settings, a restore), rebuilds them.
        listen = getattr(self._config_store, "add_change_listener", None)
        if callable(listen):
            listen(self._on_settings_written)

    def _on_settings_written(self, keys: Any) -> None:
        from kazma_core.model_prices import OWNER_PRICES_KEY

        if keys is None or OWNER_PRICES_KEY in keys:
            self.prices_changed()

    @property
    def generation(self) -> int:
        """Counts the changes to the providers and the active profile."""
        return self._generation

    def _config_changed(self) -> None:
        with self._lock:
            self._generation += 1

    def prices_changed(self) -> None:
        """The owner's model prices changed: every client is built again with
        them (a cached client keeps the price it was built with)."""
        with self._lock:
            self._clients.clear()
            self._config_changed()

    def _track(self, client: LLMProvider, provider: str = "") -> LLMProvider:
        """Register a built client for shutdown cleanup, returning it unchanged."""
        with self._lock:
            self._all_clients.add(client)
            self._client_providers[client] = provider
        return client

    def provider_for_client(self, client: LLMProvider) -> str:
        """The provider actually used to build this client, including fallback."""
        with self._lock:
            return self._client_providers.get(client, "")

    async def release_client(self, client: LLMProvider) -> None:
        """Close a completed one-off client; cached global clients stay owned."""
        with self._lock:
            if any(client is cached for cached in self._clients.values()):
                return
        await client.close()
        with self._lock:
            self._all_clients.discard(client)
            self._client_providers.pop(client, None)

    async def close(self) -> None:
        """Close all cached LLM clients (call on app shutdown)."""
        with self._lock:
            # Iterate the superset (_all_clients) so one-off clients built by
            # get_model / get_client_by_provider are also closed (audit finding).
            for client in list(self._all_clients):
                try:
                    await client.close()
                except Exception:
                    pass
            self._clients.clear()
            self._all_clients.clear()
            self._client_providers.clear()

    # ── Active profile management ──────────────────────────────────

    @staticmethod
    def _env_locked() -> bool:
        """Whether the active profile is pinned by env vars (KAZMA_MODEL /
        KAZMA_PROVIDER).

        When locked, ``set_active_model`` / ``set_active_provider`` are
        no-ops — the env always wins. Use this to pin a single model on a
        shared public demo so visitors can't change it for everyone.
        """
        return bool(os.getenv("KAZMA_MODEL", "").strip() or os.getenv("KAZMA_PROVIDER", "").strip())

    def _resolve_provider_config(
        self, provider_name: str, model: str = "",
    ) -> tuple[str, str, str, str]:
        """Resolve provider config from stored providers or legacy llm.* keys.

        Returns (provider_name, base_url, api_key, model).
        Falls back to legacy ``llm.*`` keys when no stored provider entry
        exists.
        """
        provider_entry = self.get_provider(provider_name) if provider_name else None
        base_url = ""
        api_key = ""
        effective_model = model

        if provider_entry:
            base_url = str(provider_entry.get("base_url", "") or "")
            api_key = coerce_api_key(provider_entry.get("api_key", ""))
            if provider_entry.get("api_keys"):
                # Several keys (providers stage 4): the first not resting.
                from kazma_core.provider_keys import current_key

                api_key = current_key(self._config_store, provider_name, api_key, provider_entry)
        else:
            # Fallback: legacy llm.* keys
            base_url = str(self._config_store.get("llm.base_url", "") or "")
            api_key = coerce_api_key(self._config_store.get("llm.api_key", "") or "")
            if not effective_model:
                effective_model = str(self._config_store.get("llm.model", "") or "")
            # ALWAYS pin to "custom": a named provider with no provider entry
            # would hit get_client's cross-provider auto-correction, which
            # silently REPLACES the legacy base_url/model with the active
            # provider's — the exact model/provider desync the legacy path
            # must avoid (audit finding). The env fallback below then reads
            # CUSTOM_API_KEY / KAZMA_API_KEY, which is the correct generic
            # behavior for a custom endpoint.
            provider_name = "custom"

        # Env-var fallback: when no key was resolved from ConfigStore, try the
        # conventional <PROVIDER>_API_KEY env var (e.g. GROQ_API_KEY). This
        # lets deployments (incl. the public demo) configure keys via env
        # without saving them in the UI.
        if not api_key and provider_name:
            # env_key_for, not a local spelling: replacing only hyphens left a
            # dot in place and produced `Z.AI_API_KEY`, a name no shell can
            # export, so this fallback was unreachable for that provider in
            # both chat and the Test button.
            from kazma_core.providers import env_key_for

            api_key = os.getenv(env_key_for(provider_name), "")
            # KAZMA_API_KEY is a generic operator key — only reuse it for
            # Bearer/OpenAI-compatible providers. Sending it to a native
            # provider (Anthropic x-api-key, Azure api-key) sends one
            # vendor's credential to another vendor's endpoint (audit H-10).
            if not api_key:
                _preset_auth = PROVIDER_PRESETS.get(
                    (provider_name or "").strip().lower(), {}
                ).get("auth_header", "Bearer")
                if _preset_auth == "Bearer":
                    api_key = os.getenv("KAZMA_API_KEY", "")

        return provider_name, base_url, api_key, effective_model

    @staticmethod
    def _key_is_usable(key: str) -> bool:
        from kazma_core.runtime.live_llm import key_is_usable

        return key_is_usable(key)

    @staticmethod
    def _url_is_local(url: str) -> bool:
        from kazma_core.runtime.live_llm import url_is_local

        return url_is_local(url)

    def _first_ready_provider(
        self, *, exclude: str = "",
    ) -> tuple[str, str, str, str] | None:
        """Pick an enabled provider that actually has an API key.

        Prefer ``health=healthy`` (just Tested) so a working DeepSeek key
        wins over the empty/stale OpenAI default that chat otherwise pins.
        """
        exclude_l = (exclude or "").strip().lower()
        scored: list[tuple[int, str, dict[str, Any]]] = []
        from kazma_core.models.modality import row_is_speech

        for p in self.list_providers():
            if p.get("enabled") is False:
                continue
            name = str(p.get("name") or "")
            if not name or name.lower() == exclude_l:
                continue
            if row_is_speech(p, self._discovered_models.get(name, [])):
                continue  # a speech service never answers a chat (stage 2)
            _, _url, api_key, _ = self._resolve_provider_config(name, "")
            if not self._key_is_usable(api_key):
                continue
            rank = 0 if str(p.get("health") or "") == "healthy" else 1
            scored.append((rank, name.lower(), p))
        if not scored:
            return None
        scored.sort()
        chosen = scored[0][2]
        name = str(chosen.get("name") or "")
        _, base_url, api_key, _ = self._resolve_provider_config(name, "")
        vis = self.get_visible_models(name)
        models = vis or list(chosen.get("models") or [])
        model = str(models[0]) if models else ""
        if not model:
            # One map, in providers.py. This copy had drifted: it was missing
            # xai, openrouter and mistral, which model_switch's copy had.
            from kazma_core.providers import default_model_for

            model = default_model_for(name)
        return name, base_url, api_key, model

    def get_active_profile(self) -> dict[str, str]:
        """Return the active provider profile.

        Returns a dict with keys ``provider``, ``base_url``, ``model``,
        ``api_key`` (masked).  Falls back to legacy ``llm.*`` keys when no
        active profile has been explicitly set.
        """
        provider_name, base_url, api_key, model = self._resolve_provider_config(
            self._active_provider, self._active_model,
        )

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

        If *model* is omitted, the active model is re-validated against the
        new provider: when the current model is NOT owned by the new
        provider it is cleared (rather than silently left pointing at a
        model the new provider cannot serve). This closes the
        "change-one-without-the-other" desync where the persisted profile
        recorded a provider/model mismatch.
        """
        if self._env_locked():
            logger.info("[registry] provider change to %r ignored — profile locked by KAZMA_MODEL/KAZMA_PROVIDER env", provider)
            return {"error": "Profile is locked by KAZMA_MODEL/KAZMA_PROVIDER environment variables"}
        clean_provider = (provider or "").strip()
        if not clean_provider:
            return {"error": "Provider name is required"}

        with self._lock:
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
            else:
                # Re-validate the active model against the new provider so
                # we don't leave a model the new provider doesn't serve.
                owner = self.find_provider_for_model(self._active_model)
                if owner:
                    owner_name = owner.get("name", "")
                    if owner_name and owner_name.lower() != clean_provider.lower():
                        logger.info(
                            "Active model '%s' not served by new provider '%s' "
                            "(owned by '%s') — clearing active model.",
                            self._active_model, clean_provider, owner_name,
                        )
                        self._active_model = ""

            # Persist
            self._config_store.set("registry.active_provider", clean_provider, category="registry")
            if self._active_model:
                self._config_store.set("registry.active_model", self._active_model, category="registry")

            # Invalidate cached client
            self._clients.pop(clean_provider, None)
            self._config_changed()

            return self.get_active_profile()

    def set_active_model(self, model: str) -> None:
        """Change the active model, auto-switching provider if needed.

        If the model belongs to a different provider than the currently
        active one, the active provider is updated so the LLM client
        points to the correct API endpoint.
        """
        if self._env_locked():
            logger.info("[registry] model change to %r ignored — profile locked by KAZMA_MODEL/KAZMA_PROVIDER env", model)
            return
        clean_model = (model or "").strip()
        from kazma_core.models.modality import is_speech_model

        if is_speech_model(clean_model):
            raise ValueError(
                f"{clean_model} is a speech model (STT/TTS), not a chat model. "
                "Pick STT/TTS under Settings → Voice; API keys stay on the "
                "matching provider card."
            )
        with self._lock:
            # ``provider:model`` names its provider (providers stage 3): the
            # model is stored bare, beside the provider it was named with.
            named, bare = self.split_model_ref(clean_model)
            if named:
                self._active_model = bare
                self._config_store.set("registry.active_model", bare, category="registry")
                if named != self._active_provider:
                    self._active_provider = named
                    self._config_store.set("registry.active_provider", named, category="registry")
                self._clients.clear()
                self._config_changed()
                return
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
            self._config_changed()

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

        When the configured provider has no usable key another one is
        substituted, and the operator is told -- in chat and on the web banner
        (:mod:`kazma_core.observability.model_fallback`), after the registry
        lock is released. It used to be one WARNING line per build.
        """
        # Per-turn pin (SSE/WS body.model) is an implicit override — same
        # as passing model= — so we never persist it onto the active profile.
        if model is None:
            try:
                from kazma_core.runtime.turn_model import current_turn_model

                pinned = current_turn_model()
                if pinned:
                    model = pinned
            except Exception:
                pass

        client, outcome = self._build_client(model)
        from kazma_core.observability import model_fallback

        if outcome[0] == "substituted":
            model_fallback.report_substitution(
                provider=outcome[1], model=outcome[2],
                used_provider=outcome[3], used_model=outcome[4],
            )
        elif outcome[0] == "usable":
            model_fallback.report_provider_usable(outcome[1])
        return client

    def _build_client(self, model: str | None) -> tuple[LLMProvider, tuple[str, ...]]:
        """:meth:`get_client`'s body, under the registry lock.

        Returns the client and what became of the provider asked for:
        ``("usable", provider)``, ``("substituted", provider, model,
        used_provider, used_model)``, or ``("unusable",)`` when there was no
        key and nothing to substitute (that call fails loudly on its own).
        """
        with self._lock:
            provider_name = self._active_provider or "custom"
            effective_model = model or self._active_model
            named = ""
            if effective_model:
                named, effective_model = self.split_model_ref(effective_model)
                if named:
                    provider_name = named

            # Safety: if the model belongs to a DIFFERENT provider,
            # auto-correct the provider so we don't send cross-provider
            # model names (e.g. NVIDIA model to DeepSeek's API).
            # This runs regardless of whether *model* was passed — the
            # previous `not model` guard defeated this safety net exactly
            # when the swarm path (which always passes model=) needed it.
            if effective_model and not named:
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
                cached = self._clients[provider_name]
                cached_key = coerce_api_key(
                    getattr(getattr(cached, "config", None), "api_key", "")
                )
                cached_url = str(
                    getattr(getattr(cached, "config", None), "base_url", "") or ""
                )
                # A boot-time client can carry the right URL and an empty key.
                # Returning it forever is the self-improvement 401 after a
                # working chat hop (2026-09-11). Rebuild from the store.
                if self._key_is_usable(cached_key) or self._url_is_local(cached_url):
                    return cached, ("usable", provider_name)
                logger.warning(
                    "Dropping cached client for %s — unusable key against %s",
                    provider_name,
                    cached_url,
                )
                self._clients.pop(provider_name, None)

            # Resolve provider config (shared with get_active_profile)
            _, base_url, api_key, effective_model = self._resolve_provider_config(
                provider_name, effective_model,
            )

            # Chat pins a model from localStorage (often gpt-4o-mini / OpenAI)
            # even after the operator only configured DeepSeek. Sending that
            # to a keyless cloud URL is a 401. If this profile has no key and
            # is not local, use a provider that actually has one.
            outcome: tuple[str, ...] = ("usable", provider_name)
            if not self._key_is_usable(api_key) and not self._url_is_local(base_url):
                outcome = ("unusable",)
                ready = self._first_ready_provider(exclude=provider_name)
                if ready is not None:
                    ready_name, ready_url, ready_key, ready_model = ready
                    logger.warning(
                        "Profile provider=%s model=%s has no usable API key; "
                        "using %s/%s which has a configured key",
                        provider_name,
                        effective_model,
                        ready_name,
                        ready_model,
                    )
                    outcome = (
                        "substituted", provider_name, str(effective_model or ""),
                        ready_name, str(ready_model or effective_model or ""),
                    )
                    provider_name, base_url, api_key = ready_name, ready_url, ready_key
                    effective_model = ready_model or effective_model

            # Fallback model if still empty.
            #
            # This used to end `or "gpt-4o-mini"`, which is the stale-pin
            # defect one branch over: with nothing configured it invented an
            # OpenAI model and dialled a provider that may hold no key. Ask
            # the provider we actually resolved above.
            # ``llm.model`` is the legacy "custom" profile's model (an install
            # set up by environment): never another provider's. Read for any
            # provider, it sent kazma.yaml's gpt-4o-mini to DeepSeek
            # (providers stage 1).
            if not effective_model and provider_name == "custom":
                effective_model = str(self._config_store.get("llm.model", "") or "")
            if not effective_model:
                from kazma_core.providers import default_model_for

                effective_model = default_model_for(provider_name)

            entry = self.get_provider(provider_name)
            config = self._client_config(provider_name, base_url, api_key, effective_model, entry)

            # The client class comes from the provider's declared `api_style`
            # (see kazma_core.provider_adapters). This used to be a four-way
            # branch on the vendor's name, written out again in get_model and
            # get_client_by_provider.
            client = build_client(provider_name, config, entry)

            if model is None and (
                self._key_is_usable(api_key) or self._url_is_local(base_url)
            ):
                self._clients[provider_name] = client

            self._track(client, provider_name)
            return client, outcome

    def get_model(self, model_id: str) -> LLMProvider:
        """Return a pre-configured ``LLMProvider`` for a specific model ID.

        Looks up which provider owns *model_id* from the provider list, then
        builds a client.
        """
        clean_id = (model_id or "").strip()
        if not clean_id:
            raise ValueError("model_id is required")
        named, bare = self.split_model_ref(clean_id)
        if named:
            pinned = self.get_client_by_provider(named, bare)
            if pinned is not None:
                return pinned
            clean_id = bare

        # Search providers for one that lists this model (manual + discovered)
        owner = self.find_provider_for_model(clean_id)
        if owner:
            owner_name = str(owner.get("name", "")).lower()
            config = self._client_config(
                owner_name, str(owner.get("base_url", "")),
                coerce_api_key(owner.get("api_key", "")), clean_id, owner,
            )
            return self._track(build_client(owner_name, config, owner), owner_name)

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
        if not entry or not entry.get("enabled", True):
            return None
        effective_model = model or str(entry.get("model", "") or "")
        if not effective_model:
            # A model of THIS provider -- never another vendor's. The active
            # model only when it is this provider's (§1: model and provider
            # move together), else the provider's own first visible model,
            # else its default. Provider rows keep `models`, never `model`,
            # so the old `self._active_model or ...` sent whatever model was
            # active -- deepseek-chat to Anthropic for an Anthropic-pinned
            # swarm worker, a 404 classed permanent (audit 2026-10-26 AUD-015).
            from kazma_core.providers import default_model_for

            effective_model = (
                self._active_model_if_owned_by(provider_name)
                or self.probe_model_for(provider_name)
                or default_model_for(provider_name)
            )
        base_url, api_key = self.resolve_provider_credentials(provider_name)
        config = self._client_config(provider_name, base_url, api_key, effective_model, entry)
        return self._track(build_client(provider_name, config, entry), provider_name)

    def _client_config(
        self, provider_name: str, base_url: str, api_key: str, model: str,
        entry: dict[str, Any] | None = None,
    ) -> LLMConfig:
        """The config a client is built from, with the model's price when one
        is known (kazma_core.model_prices: the owner's, or free for a server
        on this network). Without it every call was priced as gpt-4o-mini.

        The provider row's own settings ride along (providers stage 2): its
        headers, and Azure's API version."""
        from kazma_core.model_prices import price_for

        fields: dict[str, Any] = {"base_url": base_url, "api_key": api_key, "model": model}
        row = entry if entry is not None else (self.get_provider(provider_name) or {})
        from kazma_core.model_details import details_for

        if row.get("api_keys"):
            # A key that fails for its key gives way to the next (stage 4).
            fields["key_switch"] = lambda failed, exc, _p=provider_name: self.switch_provider_key(_p, failed, exc)

        preset_id = str(row.get("preset") or "").strip().lower()
        known = details_for(provider_name, str(model or ""), preset=preset_id, store=self._config_store)
        # A model that writes less than the default limit is asked for no
        # more (a provider refuses a request over its model's limit).
        if known.max_output and known.max_output < LLMConfig.max_tokens:
            fields["max_tokens"] = known.max_output
        if isinstance(row.get("secret_headers"), dict) and row["secret_headers"]:
            fields["extra_headers"] = dict(row["secret_headers"])
        if row.get("api_version"):
            fields["api_version"] = str(row["api_version"])
        price = price_for(provider_name, base_url, str(model or ""), store=self._config_store, details=known)
        if price is not None:
            fields["input_cost_per_1m"] = price.input_per_1m
            fields["output_cost_per_1m"] = price.output_per_1m
        return LLMConfig.from_dict(fields)

    def _active_model_if_owned_by(self, provider_name: str) -> str:
        """The active model when it belongs to *provider_name*, else "".

        Owned means the provider list names the active model under this
        provider, or -- for a model typed by hand and listed nowhere -- that
        this provider is the active one (``set_active_model`` moves both).
        """
        active = (self._active_model or "").strip()
        if not active:
            return ""
        entry = self.get_provider(provider_name) or {}
        own_name = str(entry.get("name") or provider_name).strip().lower()
        owner = self.find_provider_for_model(active)
        if owner is not None:
            owner_name = str(owner.get("name") or "").strip().lower()
            return active if owner_name == own_name else ""
        active_provider = self.get_provider(self._active_provider or "") or {}
        active_name = str(
            active_provider.get("name") or self._active_provider or ""
        ).strip().lower()
        return active if active_name and active_name == own_name else ""

    def find_provider_for_model(self, model_id: str) -> dict[str, Any] | None:
        """Return the provider entry that owns *model_id*.

        Searches both manually-configured ``models`` and cached
        ``_discovered_models`` so that discovered models route correctly.
        Returns ``None`` if no provider claims the model.
        
        Only searches ENABLED providers — disabled providers are ignored
        to prevent auto-switching to providers the user has turned off.
        """
        clean_id = (model_id or "").strip()
        if not clean_id:
            return None
        from kazma_core.models.modality import row_is_speech

        rows = self.list_providers()
        named, bare = self.split_model_ref(clean_id, rows)
        if named:
            # ``provider:model`` (providers stage 3): that provider, listed
            # or not -- the owner named it.
            row = next((r for r in rows if str(r.get("name") or "") == named), None)
            return dict(row) if row is not None and row.get("enabled", True) else None
        owners: list[dict[str, Any]] = []
        for provider in rows:
            # Skip disabled providers — they should not be considered
            # for model resolution (fixes auto-switch to disabled providers)
            if not provider.get("enabled", True):
                continue
            name = provider.get("name", "")
            discovered_ids = self._discovered_models.get(name, [])
            # A speech service is never a chat model's owner (stage 2).
            if row_is_speech(provider, discovered_ids):
                continue
            manual = set(self._normalize_models(provider.get("models", [])))
            if clean_id in manual or clean_id in set(discovered_ids):
                owners.append(provider)
        if not owners:
            return None
        # One name under two providers: the provider in use, else the first
        # (a reference says which; the pickers send one).
        active = (self._active_provider or "").strip().lower()
        for row in owners:
            if str(row.get("name") or "").strip().lower() == active:
                return dict(row)
        return dict(owners[0])

    def split_model_ref(
        self, ref: str, rows: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str]:
        """``(provider, model)`` for a ``provider:model`` reference, else
        ``("", ref)`` (kazma_core.model_refs)."""
        from kazma_core.model_refs import split_ref

        if ":" not in str(ref or ""):
            return "", str(ref or "").strip()
        names = [str(r.get("name") or "") for r in (rows if rows is not None else self.list_providers())]
        return split_ref(ref, names)

    # ── Model discovery ────────────────────────────────────────────

    async def discover_models(self, provider_name: str) -> list[str]:
        """The provider's models from its ``/models`` endpoint, or ``[]`` when
        it could not be asked (:meth:`discover` says why)."""
        return (await self.discover(provider_name)).models

    async def discover(self, provider_name: str, candidate: dict[str, Any] | None = None) -> DiscoveryResult:
        """Hit the provider's ``/models`` endpoint: its model IDs, or why not.

        *candidate*: a provider entry to ask instead of the stored one -- what
        a Settings dialog or the setup guide typed, not saved yet
        (kazma_core.provider_check). Its answer is neither cached nor used
        to prune the stored selection.

        A failure is a failure (FA-107): it returned the list cached from an
        earlier discovery, and Settings said "Found 2 models" over a provider
        that could not be reached. The earlier list is still there, as
        ``cached``, for the page to show labelled as such.

        Results are cached in ``self._discovered_models``.
        """
        clean_name = (provider_name or "").strip()
        if not clean_name:
            return DiscoveryResult.failed("not_found", "No provider was named.")
        cached = list(self._discovered_models.get(clean_name, []))

        def failed(code: str, detail: str) -> DiscoveryResult:
            return DiscoveryResult.failed(code, detail, cached=cached)

        def keep(ids: list[str], details: dict[str, dict[str, Any]] | None = None) -> None:
            if candidate is None:
                self._discovered_models[clean_name] = ids
                if details:
                    # What the provider's own list says about each model
                    # (providers stage 3, kazma_core.model_details).
                    self._pending_details[clean_name] = details

        # The provider list is settings and vault reads (audit 2026-10-26 AUD-111).
        provider_entry = candidate if candidate is not None else await asyncio.to_thread(
            self.get_provider, clean_name)
        if not provider_entry:
            logger.warning("discover_models: provider %r not found", clean_name)
            return failed("not_found", f"There is no provider named {clean_name!r}.")

        base_url = str(provider_entry.get("base_url", ""))
        api_key = str(provider_entry.get("api_key", ""))

        if not base_url:
            # Google Vertex AI has no static /models endpoint — models are
            # hardcoded because the base URL is computed per project/location.
            if clean_name == "google":
                if api_key:
                    try:
                        async with httpx.AsyncClient(timeout=10.0, verify=shared_ssl_context()) as client:
                            # Pass the key via header, not the URL query string —
                            # ?key= leaks the API key into proxy/access logs and
                            # httpx error tracebacks (audit finding).
                            resp = await client.get(
                                "https://generativelanguage.googleapis.com/v1beta/models",
                                headers={"x-goog-api-key": api_key},
                            )
                            if resp.status_code == 200:
                                data = resp.json()
                                model_ids = []
                                google_details: dict[str, dict[str, Any]] = {}
                                for m_entry in data.get("models", []):
                                    full_name = m_entry.get("name", "")
                                    short = full_name[7:] if full_name.startswith("models/") else full_name
                                    if short:
                                        model_ids.append(short)
                                        listed = from_listing(m_entry)
                                        if listed:
                                            google_details[short] = listed
                                if model_ids:
                                    found = sorted(set(model_ids))
                                    keep(found, google_details)
                                    await self._save_reported_details(clean_name)
                                    if candidate is None:
                                        await asyncio.to_thread(
                                            self._prune_stale_selected_models, clean_name, found
                                        )
                                    return DiscoveryResult(True, list(found))
                            problem = f"Google answered HTTP {resp.status_code}."
                    except Exception as exc:
                        logger.warning("discover_models: Google AI Studio dynamic discovery failed: %s", exc)
                        problem = f"Google could not be reached ({type(exc).__name__})."
                    # A key that was refused, or no answer: never the built-in
                    # list in its place, named as found (FA-107).
                    from kazma_core.providers import GEMINI_MODELS

                    return DiscoveryResult.failed(
                        "unreachable", problem, cached=cached or list(GEMINI_MODELS))

                # Vertex AI has no /models endpoint: the built-in list is the answer.
                from kazma_core.providers import GEMINI_MODELS
                keep(list(GEMINI_MODELS))
                return DiscoveryResult(True, list(GEMINI_MODELS), source="built_in")
            logger.warning("discover_models: no base_url for %r", clean_name)
            return failed("no_base_url", "The provider has no base URL.")

        # Resolve the models endpoint: the row's preset when it was made
        # from one under another name (providers stage 2).
        preset_id = str(provider_entry.get("preset") or "").strip().lower() or clean_name
        preset = PROVIDER_PRESETS.get(preset_id, {})
        models_path = preset.get("models_endpoint", "/models")
        if preset and not models_path:
            # Azure deployments and Bedrock model ids are the owner's own
            # list: there is no endpoint that lists them for a key.
            own = self._normalize_models(provider_entry.get("models", []))
            return DiscoveryResult(True, own, source="manual") if own else failed(
                "no_models_endpoint", "This provider has no model list: type its models.")

        # Build the full URL — base_url + models_path.
        # Most OpenAI-compatible APIs expect {base_url}/models where base_url
        # already includes the /v1 suffix (e.g. https://api.openai.com/v1/models).
        url = f"{base_url.rstrip('/')}{models_path}"

        # SSRF guard: prevent the server from fetching internal/private URLs
        # (e.g. cloud metadata 169.254.169.254, localhost services). Private
        # ranges are allowed ONLY for loopback hosts (Ollama / LM Studio) —
        # previously allow_private=True unconditionally, so an admin-pasted
        # provider URL could probe any private/internal host with the API key
        # attached (audit finding).
        # Imported before the try: imported inside it after urlparse, a URL
        # urlparse refuses ("http://[::1") left SSRFError unbound when the
        # except clause named it, and the guard raised UnboundLocalError
        # instead of answering (2026-10-02).
        from urllib.parse import urlparse

        from kazma_core.security.ssrf import SSRFError, resolves_to_own_network, validate_url

        try:
            _host = (urlparse(url).hostname or "").lower()
            _loopback = _host in ("localhost", "127.0.0.1", "::1")
            # A server on the owner's own network (providers stage 1): every
            # address its host resolves to is loopback, private or Tailscale's
            # -- never link-local (cloud metadata). Anything else is checked as
            # a public address, as before.
            _own = _loopback or await asyncio.to_thread(resolves_to_own_network, url)
            if not _own:
                await asyncio.to_thread(validate_url, url, block_unresolved=True)
        except SSRFError as exc:
            logger.warning("discover_models: SSRF blocked %r for %r: %s", url, clean_name, exc)
            return failed("blocked_url", "Kazma does not send requests to that address.")
        except Exception as _e:
            logger.debug("discover_models SSRF guard issue: %s", _e)
            logger.warning("discover_models: SSRF validation unavailable — blocking request for safety")
            return failed("blocked_url", "The address could not be checked, so it was not asked.")

        # Build auth headers per the provider's auth style. Non-Bearer
        # vendors (Anthropic ``x-api-key``, Azure ``api-key``) use their own
        # header names — stuffing the scheme into ``Authorization`` produced
        # ``Authorization: x-api-key <key>``, a guaranteed 401 (audit M-6).
        auth_header_type = preset.get("auth_header", "Bearer")
        headers: dict[str, str] = {}
        if isinstance(provider_entry.get("secret_headers"), dict):
            headers.update({str(k): str(v) for k, v in provider_entry["secret_headers"].items()})
        if api_key:
            if auth_header_type == "Bearer" or not auth_header_type:
                headers["Authorization"] = f"Bearer {api_key}"
            elif auth_header_type == "x-api-key":
                headers["x-api-key"] = api_key
                headers.setdefault("anthropic-version", "2023-06-01")
            else:
                headers[auth_header_type] = api_key

        try:
            async with httpx.AsyncClient(timeout=15.0, verify=shared_ssl_context()) as client:
                resp = await client.get(url, headers=headers)
                try:
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    logger.error("discover_models failed for %r: HTTP %s", clean_name, status)
                    return failed(f"http_{status}", f"The provider answered HTTP {status}.")
                data = resp.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                return failed("bad_answer", "The provider's answer was not a list of models.")

            model_ids: list[str] = []
            listed_details: dict[str, dict[str, Any]] = {}
            for item in data.get("data", []):
                if not isinstance(item, dict):
                    continue
                model_id = item.get("id", "")
                if model_id:
                    # Strip :latest suffix for Ollama to prevent duplicate/stale tags
                    if clean_name == "ollama" and str(model_id).endswith(":latest"):
                        model_id = str(model_id)[:-7]
                    model_ids.append(str(model_id))
                    listed = from_listing(item)
                    if listed:
                        listed_details[str(model_id)] = listed

            model_ids = sorted(set(model_ids))
            keep(model_ids, listed_details)
            if candidate is None:
                await self._save_reported_details(clean_name)
            if candidate is None:
                await asyncio.to_thread(self._prune_stale_selected_models, clean_name, model_ids)  # audit 2026-10-26 AUD-111
            return DiscoveryResult(True, list(model_ids))

        except ValueError as exc:  # a body that is not JSON
            logger.error("discover_models failed for %r: %s", clean_name, type(exc).__name__)
            return failed("bad_answer", "The provider's answer was not JSON.")
        except Exception as exc:  # no answer: network, TLS, a client that failed
            logger.error("discover_models failed for %r: %s", clean_name, exc)
            return failed("unreachable", f"The provider could not be reached ({type(exc).__name__}).")

    async def _save_reported_details(self, provider_name: str) -> None:
        """Keep what the provider's list said about its models (providers
        stage 3): a settings write, off the loop. A failure loses only these
        details, said at WARNING."""
        from kazma_core.model_details import reported_key

        details = self._pending_details.pop(provider_name, None)
        if not details:
            return
        from kazma_core.db.pg_helpers import store_errors

        try:
            await asyncio.to_thread(
                self._config_store.set, reported_key(provider_name), details, category="providers")
        except store_errors() as exc:  # the list itself was found
            logger.warning("[providers] could not keep %s's model details: %s", provider_name, type(exc).__name__)

    def switch_provider_key(self, provider_name: str, failed_key: str, exc: BaseException) -> str | None:
        """The next key of *provider_name* after *failed_key* failed for
        its key (providers stage 4, kazma_core.provider_keys), or None. The
        failed key rests, the cached client is dropped, and the switch is
        said once: an ops alert and the web banner. Blocking: off the loop."""
        from kazma_core.provider_keys import key_failure, next_key

        why, retry_after = key_failure(exc)
        if not why:
            return None
        row = self.get_provider(provider_name) or {}
        primary = coerce_api_key(row.get("api_key", ""))
        found = next_key(self._config_store, provider_name, primary, row, failed_key, why, retry_after)
        if found is None:
            return None
        label, key = found
        with self._lock:
            self._clients.pop(provider_name, None)
        words = {"refused": "was refused", "no_credit": "has no credit left", "rate_limited": "is out of requests"}
        message = (f"{provider_name}: a key {words.get(why, 'failed')}; Kazma switched to "
                   f"{('the key ' + repr(label)) if label else 'its next key'} and sent the call again.")
        logger.warning("[providers] %s", message)
        from kazma_core.observability.ops_alerts import alert

        alert(f"llm.key_switched.{provider_name}", "Provider key switched", message, severity="warn")  # never raises
        return key

    def model_details(self, model: str) -> Any:
        """What is known about *model* (a bare name or ``provider:model``):
        its provider's row decides whose list and catalog apply
        (kazma_core.model_details). Blocking: off the loop."""
        from kazma_core.model_details import ModelDetails, details_for

        named, bare = self.split_model_ref(model)
        row = self.get_provider(named) if named else self.find_provider_for_model(bare)
        if row is None and not named:
            row = self.get_provider(self._active_provider or "")
        if not bare:
            return ModelDetails()
        name = str((row or {}).get("name") or named or "")
        preset = str((row or {}).get("preset") or "").strip().lower()
        return details_for(name, bare, preset=preset, store=self._config_store)

    def _prune_stale_selected_models(self, provider_name: str, discovered_ids: list[str]) -> None:
        """Prune any models in selected_models that are no longer discovered or manual."""
        clean = (provider_name or "").strip()
        if not clean:
            return
        raw = self._config_store.get(f"providers.{clean}.selected_models", None)
        if raw is None:
            return
        current_selected = self.get_selected_models(clean)
        if not current_selected:
            return

        provider = self.get_provider(clean)
        manual_set = set(self._normalize_models(provider.get("models", []))) if provider else set()
        valid_set = set(discovered_ids) | manual_set

        # Build lookup set supporting prefix matching and :latest variations
        valid_lookups = set()
        for m in valid_set:
            valid_lookups.add(m)
            valid_lookups.add(f"{clean}/{m}")
            if m.startswith(f"{clean}/"):
                valid_lookups.add(m[len(clean)+1:])
            if m.endswith(":latest"):
                valid_lookups.add(m[:-7])
                valid_lookups.add(f"{clean}/{m[:-7]}")
            else:
                valid_lookups.add(f"{m}:latest")
                valid_lookups.add(f"{clean}/{m}:latest")

        pruned = [
            m for m in current_selected
            if m in valid_lookups or (m.startswith(f"{clean}/") and m[len(clean)+1:] in valid_lookups)
        ]
        if len(pruned) != len(current_selected):
            logger.info(
                "Pruned %d stale models from providers.%s.selected_models",
                len(current_selected) - len(pruned),
                clean,
            )
            self.set_selected_models(clean, pruned)

    async def discover_all(self) -> dict[str, list[str]]:
        """Discover models for all enabled providers."""
        results: dict[str, list[str]] = {}
        for provider in await asyncio.to_thread(self.list_providers):
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

    def remove_provider_model(self, provider_name: str, model_id: str) -> bool:
        """Remove a specific model from a provider's discovered, selected, and manual lists.

        Returns True if the model was found and removed from at least one list.
        """
        clean_name = (provider_name or "").strip()
        clean_model = (model_id or "").strip()
        if not clean_name or not clean_model:
            return False

        targets = {clean_model}
        targets.add(f"{clean_name}/{clean_model}")
        if clean_model.startswith(f"{clean_name}/"):
            targets.add(clean_model[len(clean_name)+1:])
        if clean_model.endswith(":latest"):
            targets.add(clean_model[:-7])
            targets.add(f"{clean_name}/{clean_model[:-7]}")
        else:
            targets.add(f"{clean_model}:latest")
            targets.add(f"{clean_name}/{clean_model}:latest")

        changed = False

        # 1. Remove from _discovered_models
        if clean_name in self._discovered_models:
            orig = self._discovered_models[clean_name]
            filtered = [m for m in orig if m not in targets]
            if len(filtered) != len(orig):
                self._discovered_models[clean_name] = filtered
                changed = True

        # 2. Remove from selected_models
        selected = self.get_selected_models(clean_name)
        if selected:
            filtered_selected = [m for m in selected if m not in targets]
            if len(filtered_selected) != len(selected):
                self.set_selected_models(clean_name, filtered_selected)
                changed = True

        # 3. Remove from provider entry in providers.list (manual models)
        providers = self.list_providers()
        for provider in providers:
            if provider.get("name") == clean_name:
                manual_models = provider.get("models", [])
                if isinstance(manual_models, list):
                    filtered_manual = [m for m in manual_models if str(m) not in targets]
                    if len(filtered_manual) != len(manual_models):
                        provider["models"] = filtered_manual
                        self._save_providers(providers)
                        changed = True
                break

        if changed:
            self.serialize()
        return changed

    def clear_discovered_models(self, provider_name: str | None = None) -> None:
        """Clear cached discovered models and selection for a specific provider or all providers."""
        if provider_name:
            clean_name = provider_name.strip()
            if clean_name in self._discovered_models:
                del self._discovered_models[clean_name]
            self._config_store.delete(f"providers.{clean_name}.selected_models")
        else:
            self._discovered_models.clear()
            for provider in self.list_providers():
                p_name = provider.get("name", "")
                if p_name:
                    self._config_store.delete(f"providers.{p_name}.selected_models")
        self.serialize()

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

        # Env-var override: KAZMA_PROVIDER / KAZMA_MODEL take precedence over
        # stored config so deployments (incl. the public demo) can pin the
        # active profile via environment without UI interaction. This is the
        # documented fly.toml contract.
        env_provider = os.getenv("KAZMA_PROVIDER", "").strip()
        env_model = os.getenv("KAZMA_MODEL", "").strip()
        if env_provider:
            self._active_provider = env_provider
        if env_model:
            self._active_model = env_model

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

    def probe_model_for(self, name: str) -> str:
        """A model to send when checking that *name* works, or "".

        The provider row's ``model`` field is only one of the places a model
        can live, and for a provider configured by discovering and ticking
        models it is empty. Reading only that field made the health probe send
        no model at all and then report the provider as "chat failing" -- a
        verdict about the provider, caused entirely by the check.

        Order: the row's pinned model, then what the operator ticked, then
        what is visible in the dropdowns, then anything discovered or listed
        manually.
        """
        entry = self.get_provider(name) or {}
        from kazma_core.models.modality import is_speech_model

        pinned = str(entry.get("model") or "").strip()
        if pinned and not is_speech_model(pinned):
            return pinned
        for candidates in (
            self.get_selected_models(name),
            self.get_visible_models(name),
            self.get_discovered_models(name),
            entry.get("models") or [],
        ):
            for candidate in candidates or []:
                text = str(candidate).strip()
                if text and not is_speech_model(text):
                    return text
        return ""

    def stored_key_is_undecryptable(self, name: str) -> bool:
        """True when a key IS saved for *name* but the vault cannot open it.

        "No key" and "a key you cannot read" are different problems with
        different fixes, and they look identical from the resolved view: both
        arrive as an empty string. Telling an operator who saved a key that no
        key is stored sends them to paste it again, which is the one action
        that cannot help -- the value on disk is fine; ``KAZMA_VAULT_KEY`` is
        missing or no longer the one it was encrypted with.
        """
        from kazma_core.config_store import is_vault_ref
        from kazma_core.model_registry_store import load_providers_unresolved

        clean = (name or "").strip().lower()
        for entry in load_providers_unresolved(self._config_store):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("name", "")).strip().lower() != clean:
                continue
            if not is_vault_ref(entry.get("api_key")):
                return False
            _, resolved = self.resolve_provider_credentials(name)
            return not resolved
        return False

    def resolve_provider_credentials(self, name: str) -> tuple[str, str]:
        """The base URL and API key the runtime would actually send for *name*.

        Reading ``get_provider(name)["api_key"]`` is NOT the same question. A
        key can also come from the legacy ``llm.*`` settings or from
        ``<PROVIDER>_API_KEY`` in the environment, and chat resolves all of
        them. The Settings "Test" button read only the stored entry, so a
        provider configured entirely through a `.env` file worked for every
        message and reported *"No API key stored for this provider"* when
        tested -- the check contradicting the thing it was checking.

        Use this anywhere the question is "would this work", rather than
        "what is written in the provider row".
        """
        _, base_url, api_key, _ = self._resolve_provider_config(name)
        return base_url, api_key

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
        # Pass 3: substring match — only when EXACTLY one provider name contains
        # the query. Previously first-match-wins, so `"openai"` could resolve to
        # a stored `"my-openai-mirror"` instead of the real `"openai"` (misrouting
        # traffic + the API key to the wrong endpoint). Requiring a unique
        # substring match preserves the documented fuzzy tolerance (e.g.
        # `xiaomi` → `"Xiaomi MiMo"`) while eliminating ambiguity.
        matches = [
            provider for provider in self.list_providers()
            if clean_name in str(provider.get("name", "")).strip().lower()
        ]
        if len(matches) == 1:
            return dict(matches[0])
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
                    elif key == "api_key":
                        provider[key] = coerce_api_key(value)
                    else:
                        provider[key] = value
                if not provider.get("display_name"):
                    provider["display_name"] = name
                self._save_providers(providers)
                # Invalidate cached client for this provider
                self._clients.pop(name, None)
                try:
                    from kazma_core.runtime.model_switch import notify_credentials_changed

                    notify_credentials_changed(name)
                except Exception:
                    pass
                return dict(provider)

        provider = {
            "name": name,
            "display_name": str(data.get("display_name") or name),
            "base_url": str(data.get("base_url") or ""),
            "api_key": coerce_api_key(data.get("api_key") or ""),
            "models": self._normalize_models(data.get("models", [])),
            "enabled": bool(data.get("enabled", True)),
            "health": str(data.get("health") or "unknown"),
        }
        # Safely preserve any other extra configuration keys (e.g. google_mode, project_id, location)
        for key, val in data.items():
            if key not in provider and key != "name":
                provider[key] = val
        providers.append(provider)
        self._save_providers(providers)
        try:
            from kazma_core.runtime.model_switch import notify_credentials_changed

            notify_credentials_changed(name)
        except Exception:
            pass
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
        from kazma_core.models.modality import chat_models

        self._config_store.set(
            f"providers.{clean}.selected_models",
            chat_models(models),
            category="providers",
        )

    def get_visible_models(self, provider_name: str) -> list[str]:
        """Return models that should appear in dropdowns.

        If the user has explicitly selected models, returns only those
        (chat models only). Speech/STT/TTS ids never appear as chat models.
        Otherwise returns discovered + manual chat models.
        """
        from kazma_core.models.modality import chat_models

        clean = (provider_name or "").strip()
        if clean:
            raw = self._config_store.get(f"providers.{clean}.selected_models", None)
            if raw is not None:
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        raw = []
                if isinstance(raw, list):
                    selected = chat_models(str(m) for m in raw)
                    if selected:
                        return selected

        discovered = self.get_discovered_models(provider_name)
        provider = self.get_provider(provider_name)
        manual = self._normalize_models(provider.get("models", [])) if provider else []
        return chat_models(sorted(set(discovered) | set(manual)))

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

        refused = self._restore_masked_profile_secrets(clean_name, profile)
        if refused:
            return {"error": refused}

        self._config_store.set(f"models.saved.{clean_name}", profile, category="models")
        return self._mask_profile(profile)

    def _restore_masked_profile_secrets(self, name: str, profile: dict[str, Any]) -> str:
        """Put back the stored secrets a form posted masked; the refusal, or "".

        AUD-105: every profile list masks the key (``***``) and a URL's
        password, and a form posts the profile back. ``POST
        /api/models/profiles`` restored them in the route, but ``POST
        /api/models/saved`` -- the same store -- wrote the stars verbatim
        over the real key and URL password. The rule now lives here, where
        every profile write passes (§26D): a masked key keeps the stored one;
        a masked URL keeps the stored URL only when posted back unchanged
        (``restore_masked_url``), and stars are never stored as a secret.
        """
        from kazma_core.config_store import is_masked_secret_placeholder
        from kazma_core.security.url_credentials import (
            restore_masked_url,
            url_password_is_masked,
        )

        existing = self.get_model_profile(name) or {}
        if is_masked_secret_placeholder(profile.get("api_key")):
            if not existing.get("api_key"):
                return "api_key: the key is masked. Enter it again."
            profile["api_key"] = existing["api_key"]
        base_url = restore_masked_url(profile.get("base_url"), existing.get("base_url"))
        if url_password_is_masked(base_url):
            return (
                "base_url: the password in this URL is masked. Enter it again -- "
                "the saved one is kept only when the URL itself is unchanged."
            )
        profile["base_url"] = base_url
        return ""

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
            if not provider.get("enabled", True):
                continue
            name = str(provider.get("name") or "").strip()
            if name:
                provider_names.add(name)
            p_models = self.get_visible_models(name)
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

        from kazma_core.models.modality import chat_models, is_speech_model

        llm_model = str(self._config_store.get("llm.model", "") or "").strip()
        if llm_model and not is_speech_model(llm_model):
            models.add(llm_model)

        task_defaults = self._collect_task_defaults()
        models.update(task_defaults.values())

        for model_name in self._extract_registry_models(self._config_store.get("models.registry", [])):
            models.add(model_name)

        models = set(chat_models(models))
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
        """Load the provider list from ConfigStore (read-only; never writes)."""
        from kazma_core.model_registry_store import load_providers
        from kazma_core.url_utils import set_as_typed_urls

        rows = load_providers(self._config_store)
        # One set every normalizer asks (url_utils.is_as_typed).
        set_as_typed_urls(r.get("base_url") for r in rows if r.get("url_as_typed"))
        return rows

    def _save_providers(self, providers: list[dict[str, Any]]) -> None:
        from kazma_core.model_registry_store import save_providers

        save_providers(self._config_store, providers)
        self._config_changed()

    def _default_provider_entries(self) -> list[dict[str, Any]]:
        from kazma_core.model_registry_store import default_provider_entries

        return default_provider_entries()

    def _seed_missing_presets(self) -> None:
        """Ensure every preset provider exists in the stored list."""
        from kazma_core.model_registry_store import seed_missing_presets

        stored = self._load_providers()
        stored, changed = seed_missing_presets(stored)
        # Bookkeeping, so a diagnostic (kazma doctor, /health/*) that happens
        # to build the registry first skips it rather than being refused:
        # list_providers() merges the presets in memory either way, and the
        # next non-diagnostic initialisation seeds them.
        from kazma_core.diagnostic_scope import writes_suppressed

        if changed and not writes_suppressed():
            self._save_providers(stored)

    def _normalize_provider_entry(self, provider: dict[str, Any]) -> dict[str, Any]:
        from kazma_core.model_registry_store import normalize_provider_entry

        return normalize_provider_entry(provider)

    def _normalize_models(self, models: Any) -> list[str]:
        from kazma_core.model_registry_store import normalize_models

        return normalize_models(models)

    def _mask_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        from kazma_core.security.url_credentials import mask_url_credentials

        safe = dict(profile)
        if safe.get("api_key"):
            safe["api_key"] = "***"
        if safe.get("base_url"):
            # The profile save restores an unchanged masked URL
            # (kazma_ui.providers._restore_url).
            safe["base_url"] = mask_url_credentials(safe["base_url"])
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
