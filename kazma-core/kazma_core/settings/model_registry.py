"""Universal Model Registry — single source of truth for model selection.

Provides a canonical model list consumed by Web UI, TUI, and Telegram.
Auto-refreshes when providers are updated. Validates model selection
against the current ConfigStore.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["UniversalModelRegistry", "get_model_list_text", "get_universal_models"]

logger = logging.getLogger(__name__)


class UniversalModelRegistry:
    """Global model registry serving all interfaces.

    Usage:
        from kazma_core.settings.model_registry import get_universal_models
        models = get_universal_models()  # → [{id, name, provider, ...}]
    """

    @staticmethod
    def get_models() -> list[dict[str, Any]]:
        """Return a canonical flat list of available models.

        Reads from the same source as the Web UI: registry.list_providers()
        with visible_models/discovered_models, plus saved profiles and config
        defaults. Returns a flat list with keys: id, name, provider, base_url.
        """
        models: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()

            # 1. Saved model profiles (highest priority)
            for profile in registry.list_model_profiles(mask_api_key=True):
                model_name = profile.get("model", profile["name"])
                if model_name and model_name not in seen:
                    seen.add(model_name)
                    models.append({
                        "id": f"profile:{profile['name']}",
                        "name": model_name,
                        "provider": profile.get("provider", ""),
                        "base_url": profile.get("base_url", ""),
                        "source": "saved",
                    })

            # 2. Models from ALL providers (same source as Web UI /api/providers)
            # The Web UI reads list_providers() and groups models by provider.
            # We do the same but flatten into our list.
            for provider in registry.list_providers():
                p_name = provider.get("name", "")
                p_base = provider.get("base_url", "")
                p_enabled = provider.get("enabled", False)

                # Collect models from this provider (visible > discovered+manual > manual)
                visible = registry.get_visible_models(p_name)
                discovered = registry.get_discovered_models(p_name)
                manual = provider.get("models", [])

                if visible:
                    p_models = visible
                elif discovered or manual:
                    p_models = list(dict.fromkeys(discovered + manual))
                else:
                    p_models = []

                for model_name in p_models:
                    if model_name and model_name not in seen:
                        seen.add(model_name)
                        models.append({
                            "id": f"provider:{p_name}:{model_name}",
                            "name": model_name,
                            "provider": p_name,
                            "base_url": p_base if p_enabled else "",
                            "source": "provider",
                        })

            # 3. Config defaults (llm.model, models.default, task defaults)
            # These may not belong to any provider entry.
            options = registry.list_unified_options()
            active_provider = getattr(registry, "_active_provider", None) or "config"
            for model_name in options.get("models", []):
                if model_name and model_name not in seen:
                    seen.add(model_name)
                    models.append({
                        "id": f"config:{model_name}",
                        "name": model_name,
                        "provider": active_provider,
                        "base_url": "",
                        "source": "config",
                    })
        except Exception as exc:
            logger.warning("[UniversalModelRegistry] get_models failed: %s", exc)
        return models

    @staticmethod
    def validate(model_id: str) -> dict[str, Any] | None:
        """Check if a model ID is available. Returns the model dict or None."""
        for model in UniversalModelRegistry.get_models():
            if model["id"] == model_id or model["name"] == model_id:
                return model
        return None

    @staticmethod
    def format_for_interface(models: list[dict[str, Any]], interface: str = "web") -> str:
        """Format model list for a specific interface.

        - web: JSON array
        - telegram: Bullet-point Markdown
        - tui: Newline-separated plain text
        """
        if interface == "web":
            import json
            return json.dumps(models)
        if interface == "telegram":
            if not models:
                return "No models available. Add a provider and run Discover."
            lines = ["*Available Models*", "━━━━━━━━━━━━━━━━━━━━━"]
            for m in models:
                lines.append(f"• `{m['name']}` ({m['provider']})")
            return "\n".join(lines)
        # tui / plain text — grouped by provider
        if not models:
            return "No models available."
        from collections import defaultdict
        by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for m in models:
            by_provider[m.get("provider", "unknown")].append(m)
        lines: list[str] = []
        for provider in sorted(by_provider):
            provider_models = by_provider[provider]
            lines.append(f"  [{provider}]")
            for m in provider_models:
                lines.append(f"    {m['name']}")
            lines.append("")
        return "\n".join(lines).rstrip()


# Module-level convenience
def get_universal_models() -> list[dict[str, Any]]:
    """Return all available models across saved profiles and discovered providers."""
    return UniversalModelRegistry.get_models()


def get_model_list_text(interface: str = "telegram") -> str:
    """Return a formatted model list for the given interface."""
    return UniversalModelRegistry.format_for_interface(
        UniversalModelRegistry.get_models(), interface
    )
