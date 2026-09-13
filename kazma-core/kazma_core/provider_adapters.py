"""Which client class speaks to which provider — one table, not three ladders.

The same four-way vendor branch was written three times in
``model_registry.py`` (``get_client``, ``get_model``, ``get_client_by_provider``),
each one a chain of ``provider_name.lower() == "anthropic"`` tests ending in a
fallback to the OpenAI-shaped client. Adding a provider that needs its own
wire format meant finding all three and hoping there were only three.

The capability schema already declares the answer. ``api_style`` is not a
vendor name — it is the wire format, and several providers share one — so the
selection is a lookup on that field rather than a branch on who the vendor is::

    capabilities("anthropic")["api_style"]  ->  "anthropic"
    capabilities("groq")["api_style"]       ->  "openai"

Registering a new wire format is one line in ``_ADAPTERS`` plus the declaration
in ``CAPABILITY_OVERRIDES``. Registering a new *provider* that speaks an
existing format is zero lines here, which is the point.

Imports stay lazy: the Google, Azure and Bedrock adapters each pull a vendor
SDK, and a Kazma that only ever talks to Ollama should not pay for any of them.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any, Callable, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only
    from kazma_core.llm_provider import LLMConfig, LLMProvider

__all__ = ["ADAPTER_STYLES", "build_client"]

logger = logging.getLogger(__name__)

#: api_style -> "module:ClassName". Resolved on first use.
_ADAPTERS: dict[str, str] = {
    "openai": "kazma_core.llm_provider:LLMProvider",
    "anthropic": "kazma_core.anthropic_llm:AnthropicProvider",
    "azure": "kazma_core.azure_llm:AzureProvider",
    "bedrock": "kazma_core.bedrock_llm:BedrockProvider",
    "google": "kazma_core.google_llm:GeminiProvider",
}

#: The wire formats Kazma can actually speak. A declared `api_style` outside
#: this set is a typo in the capability table, not a provider Kazma supports.
ADAPTER_STYLES = frozenset(_ADAPTERS)


def _google_kwargs(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Vertex AI is addressed by project and region, not by a base URL.

    ``us-central1`` is the long-standing default and stays the default here;
    an empty stored value must not become an empty location string, which
    Vertex rejects.
    """
    return {
        "project_id": str(entry.get("project_id", "") or ""),
        "location": str(entry.get("location", "") or "") or "us-central1",
        "google_mode": str(entry.get("google_mode", "") or ""),
    }


#: Styles whose client needs more than the LLMConfig to be constructed.
_EXTRA_KWARGS: dict[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "google": _google_kwargs,
}


def _resolve(style: str) -> type:
    module_path, _, class_name = _ADAPTERS[style].partition(":")
    return getattr(importlib.import_module(module_path), class_name)


def build_client(
    provider_name: str,
    config: LLMConfig,
    entry: Mapping[str, Any] | None = None,
) -> LLMProvider:
    """Construct the client for *provider_name* from its declared ``api_style``.

    *entry* is the stored provider record, needed only by styles that take
    deployment coordinates rather than a URL (Vertex AI). Passing ``None`` is
    fine for every other style.
    """
    from kazma_core.providers import capabilities

    style = str(capabilities(provider_name).get("api_style") or "openai").lower()
    if style not in _ADAPTERS:
        # A declared style Kazma has no adapter for. Falling back to the
        # OpenAI shape is what the old ladder did for anything unrecognised,
        # and it is right more often than not — but say so, because silently
        # speaking the wrong protocol is how a provider 400s with no clue why.
        logger.warning(
            "[providers] %r declares api_style=%r, which has no adapter; "
            "falling back to the OpenAI wire format",
            provider_name, style,
        )
        style = "openai"

    extra = _EXTRA_KWARGS.get(style)
    kwargs = extra(entry or {}) if extra else {}
    return _resolve(style)(config, **kwargs)
