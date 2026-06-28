"""Provider Router — Routes LLM requests to appropriate providers.

Provides automatic model switching and failover capabilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider."""

    name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    priority: int = 0
    timeout: float = 30.0
    max_retries: int = 3


@dataclass
class ModelSpec:
    """Specification for a model."""

    model: str
    provider: str
    context_window: int = 128000
    max_output_tokens: int = 4096


# Default model registry
MODELS: dict[str, list[ModelSpec]] = {
    "gpt-4": [
        ModelSpec(model="gpt-4", provider="openai"),
        ModelSpec(model="gpt-4-turbo", provider="openai"),
    ],
    "gpt-4o-mini": [
        ModelSpec(model="gpt-4o-mini", provider="openai"),
        ModelSpec(model="gpt-4o", provider="openai"),
    ],
    "gpt-3.5-turbo": [
        ModelSpec(model="gpt-3.5-turbo", provider="openai"),
    ],
    "claude": [
        ModelSpec(model="claude-3-opus-20240229", provider="anthropic"),
        ModelSpec(model="claude-3-sonnet-20240229", provider="anthropic"),
    ],
    "llama": [
        ModelSpec(model="llama-3-70b", provider="together"),
        ModelSpec(model="llama-3-8b", provider="together"),
    ],
}


class Router:
    """Routes LLM requests to appropriate providers.

    Supports:
    - Model fallback chains
    - Provider priority ordering
    - Automatic retries on failure
    """

    def __init__(self) -> None:
        self._providers: dict[str, ProviderConfig] = {}
        self._model_chains: dict[str, list[ModelSpec]] = MODELS.copy()

    def register_provider(self, config: ProviderConfig) -> None:
        """Register a provider configuration."""
        self._providers[config.name] = config
        logger.info("Registered provider: %s (priority=%d)", config.name, config.priority)

    def get_provider(self, name: str) -> ProviderConfig | None:
        """Get a provider configuration by name."""
        return self._providers.get(name)

    def get_model_chain(self, model_name: str) -> list[ModelSpec]:
        """Get the fallback chain for a model."""
        return self._model_chains.get(model_name, [])

    def resolve_model(self, model_hint: str) -> ModelSpec:
        """Resolve a model name to a full spec.

        Falls back to default if not found.
        """
        for spec in self._model_chains.get(model_hint, []):
            if spec.provider in self._providers:
                return spec

        # Return first match or default
        for specs in self._model_chains.values():
            for spec in specs:
                if spec.provider in self._providers:
                    return spec

        return ModelSpec(model=model_hint, provider="openai")


def get_router() -> Router:
    """Get or create the global router instance."""
    return Router()
