"""Universal Model Registry — single source of truth for model selection.

Provides a canonical model list consumed by Web UI, TUI, and Telegram.
Auto-refreshes when providers are updated. Validates model selection
against the current ConfigStore.
"""

from __future__ import annotations

import logging
from typing import Any

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

        Merges saved model profiles and discovered provider models
        into a single list with keys: id, name, provider, base_url.
        """
        models: list[dict[str, Any]] = []
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()

            # Saved model profiles
            for profile in registry.list_model_profiles(mask_api_key=True):
                models.append({
                    "id": f"profile:{profile['name']}",
                    "name": profile.get("model", profile["name"]),
                    "provider": profile.get("provider", ""),
                    "base_url": profile.get("base_url", ""),
                    "source": "saved",
                })

            # Provider-discovered models
            options = registry.list_unified_options()
            for provider_name, model_list in options.get("provider_models", {}).items():
                for model_name in model_list:
                    models.append({
                        "id": f"discovered:{provider_name}:{model_name}",
                        "name": model_name,
                        "provider": provider_name,
                        "base_url": "",
                        "source": "discovered",
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
        # tui / plain text
        if not models:
            return "No models available."
        return "\n".join(f"  {m['name']:30s} ({m['provider']})" for m in models)


# Module-level convenience
def get_universal_models() -> list[dict[str, Any]]:
    """Return all available models across saved profiles and discovered providers."""
    return UniversalModelRegistry.get_models()


def get_model_list_text(interface: str = "telegram") -> str:
    """Return a formatted model list for the given interface."""
    return UniversalModelRegistry.format_for_interface(
        UniversalModelRegistry.get_models(), interface
    )
