"""Optional LiteLLM proxy gateway for OpenAI-compatible egress.

Not the kernel and not exclusive. Native Anthropic / Azure / Bedrock /
Gemini stay on the four-branch registry (AGENTS.md §1). The generic
``LLMProvider`` (OpenAI, DeepSeek, Groq, NIM, …) may send through a
LiteLLM proxy when a URL is configured.

Live-read (like HITL / proxy provider): Settings or env take effect on
the next ``chat()`` without a process restart.

Kill-switch: ``KAZMA_LITELLM=0``. Laptop without a URL is unchanged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from kazma_core.url_utils import normalize_provider_url

logger = logging.getLogger(__name__)

__all__ = [
    "LiteLLMGateway",
    "gateway_status",
    "get_litellm_gateway",
    "is_local_openai_compat",
    "resolve_generic_egress",
]

_LOOPBACK = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
_LOCAL_NAME_MARKERS = ("ollama", "lm-studio", "lmstudio")


@dataclass(frozen=True)
class LiteLLMGateway:
    """Resolved LiteLLM proxy settings (never raises; empty URL = off)."""

    enabled: bool
    url: str
    api_key: str
    include_local: bool
    fallback_direct: bool


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in ("1", "true", "on", "yes")


def _env_off(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("0", "false", "off", "no")


def is_local_openai_compat(base_url: str) -> bool:
    """True for loopback / Ollama / LM Studio that should stay direct by default.

    Port 4000 and hostnames containing ``litellm`` are the proxy itself.
    """
    raw = (base_url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port == 4000 or "litellm" in host:
        return False
    if host in _LOOPBACK:
        return True
    return any(m in host for m in _LOCAL_NAME_MARKERS)


def _store_get(key: str, default: Any = "") -> Any:
    try:
        from kazma_core.config_store import get_config_store

        return get_config_store().get(key, default)
    except Exception:
        return default


def _yaml_gateway() -> dict[str, Any]:
    try:
        from kazma_core.config_loader import load_merged_yaml

        raw = load_merged_yaml() or {}
        llm = raw.get("llm") if isinstance(raw, dict) else {}
        gw = (llm or {}).get("gateway") if isinstance(llm, dict) else {}
        return gw if isinstance(gw, dict) else {}
    except Exception:
        return {}


def get_litellm_gateway() -> LiteLLMGateway:
    """Resolve the optional LiteLLM proxy. Never raises."""
    if _env_off("KAZMA_LITELLM"):
        return LiteLLMGateway(
            enabled=False, url="", api_key="", include_local=False, fallback_direct=False
        )

    yaml_gw = _yaml_gateway()
    url = (os.environ.get("KAZMA_LITELLM_URL") or "").strip()
    if not url:
        url = str(_store_get("llm.gateway.url", "") or yaml_gw.get("url") or "").strip()

    key = (
        os.environ.get("LITELLM_MASTER_KEY")
        or os.environ.get("LITELLM_API_KEY")
        or os.environ.get("KAZMA_LITELLM_KEY")
        or ""
    ).strip()
    if not key:
        key = str(
            _store_get("llm.gateway.api_key", "") or yaml_gw.get("api_key") or ""
        ).strip()

    if "KAZMA_LITELLM_LOCAL" in os.environ:
        include_local = _truthy(os.environ.get("KAZMA_LITELLM_LOCAL"))
    else:
        include_local = _truthy(
            _store_get("llm.gateway.include_local", yaml_gw.get("include_local", False))
        )

    if "KAZMA_LITELLM_FALLBACK_DIRECT" in os.environ:
        fallback_direct = _truthy(os.environ.get("KAZMA_LITELLM_FALLBACK_DIRECT"))
    else:
        fallback_direct = _truthy(
            _store_get(
                "llm.gateway.fallback_direct", yaml_gw.get("fallback_direct", False)
            )
        )

    if not url:
        return LiteLLMGateway(
            enabled=False,
            url="",
            api_key=key,
            include_local=include_local,
            fallback_direct=fallback_direct,
        )
    try:
        normalized = normalize_provider_url(url)
    except Exception:
        normalized = url
    return LiteLLMGateway(
        enabled=True,
        url=normalized,
        api_key=key,
        include_local=include_local,
        fallback_direct=fallback_direct,
    )


def resolve_generic_egress(
    direct_url: str,
    direct_key: str,
) -> tuple[str, str, bool]:
    """Pick (url, api_key, via_gateway) for the generic OpenAI-compatible client.

    Native four-branch providers must not call this.
    """
    gw = get_litellm_gateway()
    direct_url = (direct_url or "").strip()
    direct_key = (direct_key or "").strip()
    if not gw.enabled or not gw.url:
        return direct_url, direct_key, False
    if is_local_openai_compat(direct_url) and not gw.include_local:
        return direct_url, direct_key, False
    key = gw.api_key or direct_key
    return gw.url, key, True


def gateway_status() -> dict[str, Any]:
    """Operator-safe status (no secrets) for ``/health/details`` and docs."""
    gw = get_litellm_gateway()
    host = ""
    if gw.url:
        try:
            parsed = urlparse(gw.url)
            host = parsed.netloc or parsed.hostname or ""
        except Exception:
            host = "(set)"
    return {
        "enabled": gw.enabled,
        "host": host,
        "include_local": gw.include_local,
        "fallback_direct": gw.fallback_direct,
        "api_key_present": bool(gw.api_key),
        "native_untouched": ("google", "anthropic", "azure", "bedrock"),
    }
