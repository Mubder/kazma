"""Kazma Provider Discovery — Auto-detect local LLM endpoints.

Queries active local provider APIs (Ollama, LM Studio, etc.) to discover
available models at runtime. Fails gracefully when a provider is offline.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Known local provider endpoints ─────────────────────────────────────

_LOCAL_PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "ollama",
        "label": "Ollama",
        "models_url": "http://localhost:11434/api/tags",
        "base_url": "http://localhost:11434/v1",
        "model_key": lambda data: [
            m["name"] for m in data.get("models", [])
        ] if isinstance(data, dict) else [],
        "timeout": 3.0,
    },
    {
        "name": "lm_studio",
        "label": "LM Studio",
        "models_url": "http://localhost:1234/v1/models",
        "base_url": "http://localhost:1234/v1",
        "model_key": lambda data: [
            m["id"] for m in data.get("data", [])
        ] if isinstance(data, dict) else [],
        "timeout": 3.0,
    },
    # Extensible — add more local providers here (e.g. vLLM, LocalAI, etc.)
]


# ── Data model ─────────────────────────────────────────────────────────


@dataclass
class ProviderInfo:
    """A discovered provider with its available models."""

    name: str
    label: str
    base_url: str
    models: list[str] = field(default_factory=list)
    online: bool = False
    error: str | None = None


# ── Discovery engine ───────────────────────────────────────────────────


async def _probe_provider(
    provider_cfg: dict[str, Any],
    client: httpx.AsyncClient,
) -> ProviderInfo:
    """Probe a single local provider and return its model list.

    Uses a short timeout so a single offline provider doesn't block
    the whole discovery.
    """
    info = ProviderInfo(
        name=provider_cfg["name"],
        label=provider_cfg["label"],
        base_url=provider_cfg["base_url"],
    )

    try:
        # Build a fresh client with a short per-request timeout
        async with httpx.AsyncClient(timeout=httpx.Timeout(provider_cfg["timeout"], connect=2.0)) as probe:
            resp = await probe.get(provider_cfg["models_url"])
            resp.raise_for_status()
            data = resp.json()

        model_key_fn = provider_cfg["model_key"]
        raw_models = model_key_fn(data)

        info.models = [
            f"{provider_cfg['name']}/{m}" if "/" not in m else m
            for m in raw_models
        ]
        info.online = True
        logger.info(
            "Discovered %s (%s): %d models",
            info.label,
            provider_cfg["models_url"],
            len(info.models),
        )

    except httpx.ConnectError:
        info.error = "Connection refused — provider may be offline"
        logger.debug("Provider %s offline: %s", info.label, info.error)

    except httpx.TimeoutException:
        info.error = "Request timed out"
        logger.debug("Provider %s timed out", info.label)

    except httpx.HTTPStatusError as e:
        info.error = f"HTTP {e.response.status_code}"
        logger.debug("Provider %s HTTP error: %s", info.label, info.error)

    except Exception as e:
        info.error = str(e)[:120]
        logger.debug("Provider %s error: %s", info.label, info.error)

    return info


async def get_active_local_models(
    custom_providers: list[dict[str, Any]] | None = None,
) -> dict[str, list[ProviderInfo]]:
    """Discover all active local LLM providers and their models.

    Probes all known provider endpoints *concurrently* so that a single
    slow/offline provider does not block the whole discovery. Returns a
    categorized dict with online and offline providers.

    Args:
        custom_providers: Optional list of additional provider dicts to
            probe alongside the built-in ones. Each dict must have at
            least: name, label, models_url, base_url, model_key (callable).

    Returns:
        A dict with two keys:
          - "online": list of ProviderInfo for reachable providers
          - "offline": list of ProviderInfo for unreachable ones
    """
    providers = list(_LOCAL_PROVIDERS)
    if custom_providers:
        providers.extend(custom_providers)

    # Probe all providers concurrently
    results = await asyncio.gather(
        *[_probe_provider(cfg, None) for cfg in providers],  # type: ignore[arg-type]
        return_exceptions=True,
    )

    online: list[ProviderInfo] = []
    offline: list[ProviderInfo] = []

    for r in results:
        if isinstance(r, ProviderInfo):
            if r.online:
                online.append(r)
            else:
                offline.append(r)
        elif isinstance(r, Exception):
            logger.warning("Provider probe raised unexpected exception: %s", r)

    return {
        "online": online,
        "offline": offline,
    }


async def get_model_base_url(model_name: str) -> str | None:
    """Resolve a model string (e.g. 'ollama/llama3.2') to its provider base URL.

    Probes all providers to find which one serves the given model prefix.

    Args:
        model_name: Model string in the form 'provider_name/model' or plain name.

    Returns:
        The base_url of the matching provider, or None if not found.
    """
    # If it has a provider prefix, match directly
    if "/" in model_name:
        prefix = model_name.split("/")[0]
        for cfg in _LOCAL_PROVIDERS:
            if cfg["name"] == prefix:
                return cfg["base_url"]

    # Otherwise probe all active providers
    discovered = await get_active_local_models()
    for provider in discovered["online"]:
        for m in provider.models:
            if m == model_name or m.endswith(f"/{model_name}"):
                return provider.base_url

    return None
