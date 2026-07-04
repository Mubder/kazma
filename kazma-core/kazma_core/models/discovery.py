"""Kazma Provider Discovery — Multi-provider model discovery engine.

Provides strict, explicit routing for LM Studio, Ollama, and
Custom/Cloud endpoints. No generic fallback arrays.

Usage:
    models = await discover_models("ollama")
    models = await discover_models("lm-studio", base_url="http://localhost:1234/v1")
    health = await check_ollama_health()
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from kazma_core.url_utils import normalize_provider_url

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

_OLLAMA_BASE = "http://127.0.0.1:11434"
_OLLAMA_TAGS_URL = f"{_OLLAMA_BASE}/api/tags"
_LM_STUDIO_DEFAULT_URL = "http://127.0.0.1:1234/v1"
_TIMEOUT = 3.0


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


# ══════════════════════════════════════════════════════════════════════════
# Provider-specific discovery
# ══════════════════════════════════════════════════════════════════════════


async def discover_ollama_models() -> ProviderInfo:
    """Discover models from the local Ollama daemon.

    Queries http://127.0.0.1:11434/api/tags and strips :latest suffixes.

    Returns:
        ProviderInfo with model list (e.g. ["ollama/llama3.2", "ollama/qwen2.5"]).
    """
    info = ProviderInfo(
        name="ollama",
        label="Ollama",
        base_url=f"{_OLLAMA_BASE}/v1",
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT, connect=2.0)) as client:
            resp = await client.get(_OLLAMA_TAGS_URL)
            resp.raise_for_status()
            data = resp.json()

        raw_models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]

        # Clean: strip :latest suffix
        cleaned = []
        for m in raw_models:
            if ":" in m:
                tag = m.split(":")[-1]
                if tag == "latest":
                    m = m.rsplit(":", 1)[0]
            cleaned.append(f"ollama/{m}")

        info.models = cleaned
        info.online = True
        logger.info("Discovered Ollama: %d models", len(cleaned))

    except httpx.ConnectError:
        info.error = "Ollama not running (port 11434)"
        logger.debug("Ollama offline: %s", info.error)

    except httpx.TimeoutException:
        info.error = "Ollama timed out"
        logger.debug("Ollama timed out")

    except httpx.HTTPStatusError as e:
        info.error = f"Ollama HTTP {e.response.status_code}"
        logger.debug("Ollama HTTP error: %s", info.error)

    except Exception as e:
        info.error = str(e)[:120]
        logger.debug("Ollama error: %s", info.error)

    return info


async def discover_lm_studio_models(
    base_url: str | None = None,
) -> ProviderInfo:
    """Discover models from LM Studio (or any OpenAI-compatible server).

    Queries {base_url}/models and extracts model IDs.

    Args:
        base_url: The base URL (default http://localhost:1234/v1).
            Auto-normalized: scheme added, /v1 FORCED if missing.

    Returns:
        ProviderInfo with model list (e.g. ["openai/local-model"]).
    """
    url = normalize_provider_url(base_url or _LM_STUDIO_DEFAULT_URL)

    # HARD ENFORCE: /v1 suffix for OpenAI-compatible endpoints
    if url and not url.rstrip("/").endswith("/v1"):
        url = url.rstrip("/") + "/v1"

    info = ProviderInfo(
        name="lm_studio",
        label="LM Studio",
        base_url=url,
    )

    # Build the models endpoint: /v1/models
    models_url = f"{url}/models"

    # SSRF guard — allow private addresses for user-configured local providers
    try:
        from kazma_core.security.ssrf import SSRFError, validate_url
        validate_url(models_url, block_unresolved=True, allow_private=True)
    except SSRFError as exc:
        logger.warning("discover_lm_studio_models: SSRF blocked %r: %s", models_url, exc)
        return info
    except Exception:
        logger.warning("discover_lm_studio_models: SSRF validation unavailable — blocking request for safety")
        return info

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT, connect=2.0)) as client:
            resp = await client.get(models_url)
            resp.raise_for_status()
            data = resp.json()

        raw_models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]

        info.models = [f"openai/{m}" for m in raw_models]
        info.online = True
        logger.info("Discovered LM Studio (%s): %d models", url, len(info.models))

    except httpx.ConnectError:
        info.error = "LM Studio not running"
        logger.debug("LM Studio offline: %s", info.error)

    except httpx.TimeoutException:
        info.error = "LM Studio timed out"

    except httpx.HTTPStatusError as e:
        info.error = f"LM Studio HTTP {e.response.status_code}"

    except Exception as e:
        info.error = str(e)[:120]

    return info


async def discover_custom_models(base_url: str) -> ProviderInfo:
    """Discover models from a custom OpenAI-compatible endpoint.

    Args:
        base_url: The endpoint base URL (auto-normalized).

    Returns:
        ProviderInfo with model list.
    """
    url = normalize_provider_url(base_url)
    info = ProviderInfo(
        name="custom",
        label="Custom",
        base_url=url,
    )

    models_url = f"{url}/models"

    # SSRF guard — allow private addresses for user-configured providers
    try:
        from kazma_core.security.ssrf import SSRFError, validate_url
        validate_url(models_url, block_unresolved=True, allow_private=True)
    except SSRFError as exc:
        logger.warning("discover_custom_models: SSRF blocked %r: %s", models_url, exc)
        return info
    except Exception:
        logger.warning("discover_custom_models: SSRF validation unavailable — blocking request for safety")
        return info

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT, connect=2.0)) as client:
            resp = await client.get(models_url)
            resp.raise_for_status()
            data = resp.json()

        raw_models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]

        info.models = raw_models
        info.online = True
        logger.info("Discovered Custom (%s): %d models", url, len(raw_models))

    except Exception as e:
        info.error = str(e)[:120]

    return info


# ══════════════════════════════════════════════════════════════════════════
# Unified discovery entry point
# ══════════════════════════════════════════════════════════════════════════


async def discover_models(
    provider: str,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ProviderInfo:
    """Discover models from a specific provider.

    Routes to the correct discovery function based on provider name.
    Built-in providers (openai, anthropic, deepseek, google, xai, openrouter)
    call /v1/models on their base_url with the api_key in auth headers.

    Args:
        provider: Provider key (openai, deepseek, ollama, custom, etc.)
        base_url: Optional override URL.
        api_key:  Optional API key for authenticated providers.

    Returns:
        ProviderInfo with discovered models.
    """
    provider = provider.lower().strip()

    if provider == "ollama":
        return await discover_ollama_models()

    if provider in ("lm-studio", "lm_studio", "lmstudio"):
        return await discover_lm_studio_models(base_url)

    # All known providers + custom: fetch /v1/models with auth
    if base_url:
        return await _discover_openai_compatible(base_url, api_key, provider)

    return ProviderInfo(
        name=provider,
        label=provider.title(),
        base_url="",
        error=f"No base_url for provider '{provider}'",
    )


async def _discover_openai_compatible(base_url: str, api_key: str | None, provider: str) -> ProviderInfo:
    """Discover models from any OpenAI-compatible /v1/models endpoint."""
    import httpx

    url = f"{base_url.rstrip('/')}/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (200, 401):
                # 401 with valid key often means the endpoint requires /v1 prefix already
                if resp.status_code == 401 and "/v1/v1" in url:
                    url = url.replace("/v1/v1", "/v1")
                    resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # Check for API-level errors (some providers return 200 with error body)
            if "error" in data:
                error_msg = data["error"]
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))
                return ProviderInfo(
                    name=provider, label=provider.title(), base_url=base_url,
                    error=f"API error: {error_msg}",
                )

            models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
            if not models:
                return ProviderInfo(
                    name=provider, label=provider.title(), base_url=base_url,
                    error="No models returned. Check your API key.",
                )
            return ProviderInfo(
                name=provider,
                label=provider.title(),
                base_url=base_url,
                models=models,
                online=True,
            )
    except httpx.ConnectError:
        return ProviderInfo(name=provider, label=provider.title(), base_url=base_url, error="Connection refused")
    except httpx.HTTPStatusError as e:
        # Try to extract detailed error from response body
        try:
            body = e.response.json()
            if "error" in body:
                err = body["error"]
                if isinstance(err, dict):
                    err = err.get("message", str(err))
                return ProviderInfo(name=provider, label=provider.title(), base_url=base_url, error=f"HTTP {e.response.status_code}: {err}")
        except Exception as exc:
            logger.debug("Error response body parse failed: %s", exc)
        return ProviderInfo(name=provider, label=provider.title(), base_url=base_url, error=f"HTTP {e.response.status_code}")
    except Exception as e:
        return ProviderInfo(name=provider, label=provider.title(), base_url=base_url, error=str(e))

    # Unknown provider — try all concurrently
    logger.warning("Unknown provider '%s', probing all", provider)
    results = await asyncio.gather(
        discover_ollama_models(),
        discover_lm_studio_models(base_url),
        return_exceptions=True,
    )

    # Merge results
    merged = ProviderInfo(name="all", label="All Providers", base_url="")
    for r in results:
        if isinstance(r, ProviderInfo) and r.online:
            merged.models.extend(r.models)
            merged.online = True

    if not merged.models:
        merged.error = "No providers responded"

    return merged


# ══════════════════════════════════════════════════════════════════════════
# Ollama health check
# ══════════════════════════════════════════════════════════════════════════


async def check_ollama_health() -> dict[str, Any]:
    """Check if Ollama is running and responsive.

    Pings http://127.0.0.1:11434/api/tags with a short timeout.

    Returns:
        Dict with "online" (bool), "models" (int), "error" (str|None).
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0)) as client:
            resp = await client.get(_OLLAMA_TAGS_URL)
            resp.raise_for_status()
            data = resp.json()

        model_count = len(data.get("models", []))
        logger.info("Ollama health check: online, %d models", model_count)
        return {"online": True, "models": model_count, "error": None}

    except httpx.ConnectError:
        return {"online": False, "models": 0, "error": "Connection refused (port 11434)"}

    except httpx.TimeoutException:
        return {"online": False, "models": 0, "error": "Timed out"}

    except Exception as e:
        return {"online": False, "models": 0, "error": str(e)[:120]}


# ══════════════════════════════════════════════════════════════════════════
# Ollama pull (background task)
# ══════════════════════════════════════════════════════════════════════════


async def pull_ollama_model(model: str) -> dict[str, Any]:
    """Pull a model via `ollama pull` as an async subprocess.

    Runs in the background — does NOT block the event loop.

    Args:
        model: Model name (e.g. "llama3.2", "qwen2.5-coder:7b").

    Returns:
        Dict with "status", "model", "pid", and optionally "error".
    """
    if not model or not model.strip():
        return {"status": "error", "model": model, "error": "Empty model name"}

    model = model.strip()

    try:
        proc = await asyncio.create_subprocess_exec(
            "ollama",
            "pull",
            model,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info("Ollama pull started: model=%s pid=%d", model, proc.pid)
        return {
            "status": "pulling",
            "model": model,
            "pid": proc.pid,
        }

    except FileNotFoundError:
        return {
            "status": "error",
            "model": model,
            "error": "ollama command not found — is Ollama installed?",
        }

    except Exception as e:
        return {
            "status": "error",
            "model": model,
            "error": str(e)[:120],
        }


# ══════════════════════════════════════════════════════════════════════════
# Backward-compatible helpers
# ══════════════════════════════════════════════════════════════════════════


async def get_active_local_models(
    custom_providers: list[dict[str, Any]] | None = None,
) -> dict[str, list[ProviderInfo]]:
    """Discover all active local LLM providers (backward-compatible).

    Probes Ollama and LM Studio concurrently.

    Returns:
        Dict with "online" and "offline" lists of ProviderInfo.
    """
    results = await asyncio.gather(
        discover_ollama_models(),
        discover_lm_studio_models(),
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
            logger.warning("Provider probe raised: %s", r)

    return {"online": online, "offline": offline}


async def get_model_base_url(model_name: str) -> str | None:
    """Resolve a model string to its provider base URL.

    Args:
        model_name: "ollama/llama3.2" or "openai/local-model".

    Returns:
        Base URL string, or None if not found.
    """
    if "/" in model_name:
        prefix = model_name.split("/")[0]
        if prefix == "ollama":
            return f"{_OLLAMA_BASE}/v1"
        if prefix == "openai":
            return "https://api.openai.com/v1"

    # Probe all
    discovered = await get_active_local_models()
    for provider in discovered["online"]:
        for m in provider.models:
            if m == model_name or m.endswith(f"/{model_name}"):
                return provider.base_url

    return None
