"""Best-available-model selection for a given task kind.

When an auto-spawned swarm worker has no model/provider configured, this module
picks the best **available** model for the task at hand — walking the configured
providers and matching model ids against per-task-kind preference patterns.

Precedence for resolving a model for a task ``kind``:

1. **User-defined task default** — ``models.defaults.<kind>`` in ConfigStore
   (set via Settings → Models). If the user pinned e.g. ``code → deepseek-coder``
   and that model is actually available, it wins.
2. **Heuristic best-match** — for each enabled, API-keyed provider, the first
   model id matching the kind's preference patterns (e.g. CODING prefers
   ``*-coder*`` / ``deepseek*``; VISION reuses the vision allow-list).
3. **Active profile** — the registry's currently-active model (the caller's
   final fallback).

Env-lock aware: when ``KAZMA_MODEL`` / ``KAZMA_PROVIDER`` pin the active model,
selection is short-circuited to that model (the lock always wins).

This module never mutates the registry's active profile — it resolves a one-off
client via ``registry.get_model(id)`` / ``get_client_by_provider(...)``.
"""

from __future__ import annotations

import logging
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any

from kazma_core.models.router import TaskProfile, classify_prompt
from kazma_core.vision_capability import is_vision_capable

if TYPE_CHECKING:
    from kazma_core.model_registry import ModelRegistry

__all__ = [
    "find_best_model_for_task",
    "resolve_supervisor_route",
    "select_provider_for_task",
    "user_task_default",
]

logger = logging.getLogger(__name__)


# Per-kind model-id preference patterns (case-insensitive fnmatch globs).
# Order within a list = preference order. Curated from common model families.
_PREFERENCE_PATTERNS: dict[TaskProfile, tuple[str, ...]] = {
    TaskProfile.CODING: (
        "*-coder*", "*-code*", "codestral*", "deepseek*", "*-coder-*",
        "codellama*", "starcoder*", "qwq*", "qwen2.5-coder*",
    ),
    TaskProfile.REASONING: (
        "o1*", "o3*", "*-reasoner*", "claude-3*", "claude-sonnet*",
        "gpt-4o*", "gemini-2*", "deepseek-r1*", "qwq*",
    ),
    TaskProfile.FAST: (
        "*-mini*", "*-flash*", "*-nano*", "*-haiku*", "*-small*",
        "gpt-4o-mini", "gemini-1.5-flash*",
    ),
    # VISION uses the vision_capability allow-list, not patterns.
    TaskProfile.VISION: (),
    TaskProfile.GENERAL: (
        "gpt-4o*", "claude-3*", "claude-sonnet*", "gemini*", "deepseek*",
        "llama*", "qwen*",
    ),
    TaskProfile.DEFAULT: (
        "gpt-4o*", "claude-3*", "gemini*", "deepseek*", "llama*", "qwen*",
    ),
}


def _matches(model_id: str, patterns: tuple[str, ...]) -> bool:
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    return any(fnmatch(mid, pat.lower()) for pat in patterns)


def _kind_to_config_key(kind: TaskProfile) -> str:
    """Map a TaskProfile to the ConfigStore ``models.defaults.<key>`` name.

    Maps VISION→vision, GENERAL/DEFAULT→general so the user can override each
    kind from Settings → Models.
    """
    if kind == TaskProfile.CODING:
        return "code"
    if kind == TaskProfile.REASONING:
        return "research"
    if kind == TaskProfile.FAST:
        return "fast"
    if kind == TaskProfile.VISION:
        return "vision"
    return "general"


def _iter_usable_models(registry: "ModelRegistry"):
    """Yield (provider_name, model_id) for every enabled, API-keyed provider.

    Mirrors the provider walk in vision_capability.find_configured_vision_model.
    """
    try:
        providers = registry.list_providers()
    except Exception as exc:  # noqa: BLE001 — best-effort enumeration
        logger.debug("[model_selection] list_providers failed: %s", exc)
        return
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        if not provider.get("enabled", True):
            continue
        if not (provider.get("api_key") or "").strip():
            continue
        name = provider.get("name") or ""
        if not name:
            continue
        try:
            models = registry.get_visible_models(name) or []
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.debug("[model_selection] get_visible_models(%s) failed: %s", name, exc)
            continue
        for model_id in models:
            yield name, str(model_id)


def _heuristic_best(registry: "ModelRegistry", kind: TaskProfile) -> tuple[str, str] | None:
    """Find (provider_name, model_id) best-matching *kind* by id patterns.

    For VISION, reuses the vision allow-list. Returns None if nothing matches.
    """
    patterns = _PREFERENCE_PATTERNS.get(kind, ())
    for provider_name, model_id in _iter_usable_models(registry):
        if kind == TaskProfile.VISION:
            if is_vision_capable(model_id):
                return provider_name, model_id
        elif _matches(model_id, patterns):
            return provider_name, model_id
    return None


def user_task_default(
    registry: "ModelRegistry",
    kind: TaskProfile,
) -> tuple[str, str] | None:
    """Return ``(provider, model)`` for ``models.defaults.<kind>`` or env-lock.

    Does **not** apply heuristic id matching. Keyword classification is only
    a hint; an explicit user default always wins. Env-lock short-circuits
    to the active profile. Never mutates the active profile.
    """
    try:
        if registry._env_locked():
            profile = registry.get_active_profile()
            return (profile.get("provider") or "", profile.get("model") or "")
    except Exception:  # noqa: BLE001 — best-effort
        pass

    config_key = _kind_to_config_key(kind)
    try:
        default_model = registry._config_store.get(f"models.defaults.{config_key}")
    except Exception:  # noqa: BLE001 — best-effort
        default_model = None
    if isinstance(default_model, str) and default_model.strip():
        target = default_model.strip()
        try:
            provider_name = registry.find_provider_for_model(target)
            if provider_name:
                return (provider_name, target)
        except Exception:  # noqa: BLE001 — best-effort
            pass
    return None


def resolve_supervisor_route(
    prompt: str,
    model_router: Any | None = None,
    *,
    registry: "ModelRegistry | None" = None,
) -> tuple[str | None, Any | None, str]:
    """Pick a one-off model for this supervisor turn.

    Precedence: env lock / ``models.defaults.<kind>`` → YAML
    ``ModelRouter.route(classify())`` → ``(None, None, profile)`` so the
    caller keeps the active client. Keyword lists cannot override an
    explicit default. Never mutates the active profile.

    Returns ``(model_id, client_or_None, profile_value)``.
    """
    from kazma_core.models.router import ModelRouter

    profile = ModelRouter.classify(prompt or "")
    if registry is None:
        try:
            from kazma_core.model_registry import get_model_registry

            registry = get_model_registry()
        except Exception:  # noqa: BLE001 — best-effort
            registry = None

    if registry is not None:
        hit = user_task_default(registry, profile)
        if hit is not None:
            provider_name, model_id = hit
            client = None
            if provider_name and model_id:
                try:
                    client = registry.get_client_by_provider(
                        provider_name, model=model_id
                    )
                except Exception:  # noqa: BLE001 — best-effort
                    client = None
            logger.info(
                "[model_selection] supervisor defaults kind=%s → %s (provider=%s)",
                profile.value,
                model_id,
                provider_name,
            )
            return model_id or None, client, profile.value

    if model_router is not None:
        spec = model_router.route(profile)
        return spec.model, None, profile.value
    return None, None, profile.value


def find_best_model_for_task(
    registry: "ModelRegistry",
    kind: TaskProfile | str | None = None,
    prompt: str | None = None,
) -> tuple[str, str] | None:
    """Return (provider_name, model_id) best-suited to *kind*, or None.

    Args:
        registry: The ModelRegistry singleton.
        kind: The task kind. If None and *prompt* given, the prompt is classified.
        prompt: Used to classify the kind when *kind* is None.

    Precedence: user task-default (``models.defaults.<kind>``) → heuristic
    best-match → None (caller falls back to the active profile).

    Env-lock: if ``KAZMA_MODEL``/``KAZMA_PROVIDER`` pin the active model, the
    selection short-circuits to the active profile (returned as a pair) so the
    lock always wins.

    Never mutates the active profile.
    """
    if kind is None:
        kind = classify_prompt(prompt) if prompt else TaskProfile.GENERAL
    if isinstance(kind, str):
        try:
            kind = TaskProfile(kind)
        except ValueError:
            kind = TaskProfile.GENERAL

    # 0. Env-lock short-circuit — the pinned model always wins.
    try:
        if registry._env_locked():
            profile = registry.get_active_profile()
            return (profile.get("provider") or "", profile.get("model") or "")
    except Exception:  # noqa: BLE001 — best-effort
        pass

    # 1. User-defined task default (Settings → Models → defaults).
    config_key = _kind_to_config_key(kind)
    try:
        default_model = registry._config_store.get(f"models.defaults.{config_key}")
    except Exception:  # noqa: BLE001 — best-effort
        default_model = None
    if isinstance(default_model, str) and default_model.strip():
        target = default_model.strip()
        # Resolve the owning provider for this model id (don't mutate active).
        try:
            provider_name = registry.find_provider_for_model(target)
            if provider_name:
                return (provider_name, target)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    # 2. Heuristic best-match across usable providers.
    hit = _heuristic_best(registry, kind)
    if hit is not None:
        logger.info(
            "[model_selection] kind=%s → %s (provider=%s) [heuristic]",
            kind.value, hit[1], hit[0],
        )
        return hit

    logger.debug("[model_selection] no heuristic match for kind=%s; caller falls back to active", kind.value)
    return None


def select_provider_for_task(
    registry: "ModelRegistry",
    kind: TaskProfile | str | None = None,
    prompt: str | None = None,
) -> Any | None:
    """Resolve a one-off LLM client for *kind* without mutating the active profile.

    Returns the provider client (LLMProvider subclass) or None if nothing
    suitable is configured (caller should then use the active profile).
    """
    hit = find_best_model_for_task(registry, kind=kind, prompt=prompt)
    if hit is None:
        return None
    provider_name, model_id = hit
    if not model_id:
        return None
    try:
        if provider_name:
            client = registry.get_client_by_provider(provider_name, model=model_id)
            if client is not None:
                return client
        # Fall back to a one-off client by model id (finds owning provider).
        return registry.get_model(model_id)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("[model_selection] could not build client for %s: %s", model_id, exc)
        return None
