"""Model vision-capability classification.

Kazma routes images to LLMs as OpenAI-style ``image_url`` content parts.
Text-only models (e.g. DeepSeek) reject that part with a ``400 unknown
variant 'image_url'``. This module answers two questions without requiring
any new per-model schema in the registry:

1. ``is_text_only(model_id)`` — should we *avoid* sending images to it?
2. ``is_vision_capable(model_id)`` — is it known to accept vision input?

Policy (fail-safe):

* Only models matching ``TEXT_ONLY_PATTERNS`` are downgraded. Unknown models
  are left on the default (inline) path so legitimate vision models keep
  working. This guarantees zero regression for anything we don't know about.
* ``is_vision_capable`` is the authoritative allow-list used to pick a model
  for the ``analyze_image`` tool. A model that is neither allow-listed nor
  deny-listed is treated as "unknown" (``is_text_only`` False,
  ``is_vision_capable`` False).

The lists can be extended at runtime via the ``KAZMA_VISION_MODELS`` env var
(extra model-id substrings treated as vision-capable) — useful for exotic or
self-hosted vision models.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kazma_core.model_registry import ModelRegistry

logger = logging.getLogger(__name__)

# ── Deny-list: models that reject `image_url` content parts ───────────
# Matched against the lowercased model id with fnmatch (wildcard glob).
# These are *definitely* text-only — downgrading them is always safe.
TEXT_ONLY_PATTERNS: tuple[str, ...] = (
    "deepseek*",          # deepseek-chat, deepseek-v3, deepseek-v4-pro, -reasoner, ...
    "deepseek-r1",        # explicit (also caught by deepseek*)
    "*-reasoner",         # o1/o3-mini, deepseek-reasoner, etc. (reasoning text models)
    "o1-mini",            # no vision
    "o1-preview",         # no vision
    "o3-mini",            # no vision
    "o3-mini-*",
    "gpt-3.5*",           # legacy, no vision
    "text-*",             # text-only family (embedding/text completion)
    "*-instruct",         # generic instruct variants (text-only by convention)
)

# ── Allow-list: models known to accept `image_url` vision input ───────
# Used by analyze_image and find_configured_vision_model to pick a model.
VISION_PATTERNS: tuple[str, ...] = (
    # OpenAI vision-capable
    "gpt-4o",
    "gpt-4o-*",
    "gpt-4.1",
    "gpt-4.1-*",
    "gpt-4-turbo",
    "gpt-4-turbo-*",
    "gpt-4-vision*",
    "gpt-4.5*",
    # Anthropic Claude (all 3.x / Sonnet / Opus / Haiku accept images)
    "claude-3*",
    "claude-sonnet*",
    "claude-opus*",
    "claude-haiku*",
    # Google Gemini
    "gemini*",
    # Mistral vision
    "pixtral*",
    # Alibaba Qwen vision
    "qwen*vl*",
    "qwen*-vl*",
    "qwen2-vl*",
    "qwen2.5-vl*",
    # Open vision models
    "llava*",
    "llama-3.2-*-vision*",
    "llama-4-*",
    "mistral-small-3.1*",
    "gemma-3*",
    # Grok vision
    "grok-vision*",
    "grok-2-vision*",
)


def _extra_env_patterns() -> tuple[str, ...]:
    """Load extra vision-capable patterns from KAZMA_VISION_MODELS."""
    raw = os.environ.get("KAZMA_VISION_MODELS", "")
    return tuple(p.strip().lower() for p in raw.split(",") if p.strip())


def _matches_any(model_id: str, patterns: tuple[str, ...]) -> bool:
    """Case-insensitive fnmatch of *model_id* against *patterns*."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return False
    for pat in patterns:
        if fnmatch.fnmatch(mid, pat.lower()):
            return True
    return False


def is_text_only(model_id: str | None) -> bool:
    """Return True if *model_id* is a known text-only model.

    Only explicit deny-list matches return True; unknown models return
    False (fail-open: do not downgrade what we don't recognize).
    """
    if not model_id:
        return False
    return _matches_any(model_id, TEXT_ONLY_PATTERNS)


def is_vision_capable(model_id: str | None) -> bool:
    """Return True if *model_id* is known to accept ``image_url`` input."""
    if not model_id:
        return False
    # A text-only model is never vision-capable, even if also allow-listed.
    if is_text_only(model_id):
        return False
    if _matches_any(model_id, VISION_PATTERNS):
        return True
    return _matches_any(model_id, _extra_env_patterns())


def find_configured_vision_model(
    registry: "ModelRegistry",
) -> str | None:
    """Find the first enabled, API-keyed, vision-capable model in *registry*.

    Walks every enabled provider that has an API key configured and returns
    the first model id that passes :func:`is_vision_capable`. Does **not**
    mutate the registry's active profile.

    Returns ``None`` if no vision-capable model is configured.
    """
    try:
        providers = registry.list_providers()
    except Exception as exc:  # noqa: BLE001 — registry is best-effort
        logger.debug("[vision_capability] list_providers failed: %s", exc)
        return None

    for provider in providers:
        if not isinstance(provider, dict):
            continue
        if not provider.get("enabled", True):
            continue
        # Skip providers with no credentials — they can't actually serve.
        if not (provider.get("api_key") or "").strip():
            continue
        name = provider.get("name") or ""
        if not name:
            continue
        try:
            models = registry.get_visible_models(name) or []
        except Exception as exc:  # noqa: BLE001 — best-effort enumeration
            logger.debug(
                "[vision_capability] get_visible_models(%s) failed: %s", name, exc
            )
            continue
        for model_id in models:
            if is_vision_capable(model_id):
                logger.info(
                    "[vision_capability] selected vision model %s (provider=%s)",
                    model_id,
                    name,
                )
                return str(model_id)
    return None


def active_model_is_vision_capable(registry: "ModelRegistry") -> bool:
    """True if the registry's *active* model is vision-capable.

    Best-effort: on any error, returns True (fail-open) so the existing
    inline-image path is preserved rather than silently dropping images.
    """
    try:
        profile = registry.get_active_profile()
        return is_vision_capable(profile.get("model"))
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug(
            "[vision_capability] could not read active profile: %s", exc
        )
        return True  # fail-open


def get_vision_client(
    registry: "ModelRegistry",
) -> tuple[Any | None, str | None, str | None]:
    """Resolve an LLM client suitable for image analysis.

    Selection order:

    1. If the *active* model is vision-capable → use it (zero surprise).
    2. Else, search configured providers for any vision-capable model and
       build a one-off client for it (does not change the active profile).
    3. Else, return ``(None, active_model, None)`` so the caller can surface
       a clear, actionable error *before* making any API call.

    Returns ``(client, chosen_model, reason)`` where ``reason`` explains the
    outcome for logging.
    """
    try:
        profile = registry.get_active_profile()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug("[vision_capability] get_active_profile failed: %s", exc)
        return None, None, "registry-unavailable"

    active_model = profile.get("model")
    if is_vision_capable(active_model):
        try:
            return registry.get_client(), active_model, "active-model"
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[vision_capability] active vision client build failed: %s", exc
            )

    vision_model = find_configured_vision_model(registry)
    if vision_model:
        try:
            return registry.get_model(vision_model), vision_model, "fallback-vision"
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[vision_capability] fallback vision client build failed: %s", exc
            )

    return None, active_model, "no-vision-model"
