"""What Kazma knows about one model (providers stage 3).

Every model got a 1M-token context window, a 16,384-token output limit and a
guessed price; the context sizes and prices the providers' own model lists
carry were thrown away. A model's details now come from, the first that says:

1. **your override** (Settings -> Providers & Connectors -> Models -> Model details:
   ``models.details``, under ``provider:model`` or the bare model name);
2. **the provider's own model list** -- OpenRouter's context length, output
   limit, input kinds, tool support and per-token price, Groq's and
   Mistral's context windows, Gemini's token limits -- saved when the list
   is fetched (``registry.model_details.<name>``: found again, never in a
   settings backup);
3. **Kazma's catalog** (:data:`CATALOG`): vision, tool use and the vendor's
   own list price, for exact model ids on the vendor's own API, dated.

The catalog never holds a context window or output limit: a table of sizes
went stale and capped models below what the owner had set, and was removed
on 2026-10-18 (the owner's decision). A size is the provider's own answer,
or yours.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CATALOG",
    "CATALOG_AS_OF",
    "OVERRIDES_KEY",
    "ModelDetails",
    "details_for",
    "from_listing",
    "parse_overrides",
    "reported_key",
]

#: The setting holding the owner's details: ``{"provider:model" or "model":
#: {"context_window", "max_output", "vision", "tools"}}``.
OVERRIDES_KEY = "models.details"

#: When the catalog's prices were read from the vendors' own pages.
CATALOG_AS_OF = "2026-11"

_FIELDS = ("context_window", "max_output", "vision", "tools", "input_per_1m", "output_per_1m")


@dataclass
class ModelDetails:
    """One model's details; ``None`` is "not known". ``sources`` names
    where each known field came from: ``owner``, ``provider`` or
    ``catalog``."""

    context_window: int | None = None
    max_output: int | None = None
    vision: bool | None = None
    tools: bool | None = None
    input_per_1m: float | None = None
    output_per_1m: float | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in _FIELDS} | {"sources": dict(self.sources)}

    def fill(self, values: dict[str, Any], source: str) -> None:
        """Take each field *values* knows that is not known yet."""
        for name in _FIELDS:
            if getattr(self, name) is None and values.get(name) is not None:
                setattr(self, name, values[name])
                self.sources[name] = source


def reported_key(provider: str) -> str:
    """The settings row holding what *provider*'s model list said: Kazma's
    own state (``settings_restore``: runtime), found again by a discovery."""
    return f"registry.model_details.{provider}"


# ── The catalog ─────────────────────────────────────────────────────────
#
# Exact model ids, by the preset whose own API serves them at that price.
# Prices: US dollars per million tokens, standard (uncached, under any long
# context tier) input / output, from the vendor's pricing page as of
# CATALOG_AS_OF. A model served elsewhere (OpenRouter, a router) takes that
# provider's price, never this one.

def _m(vision: bool, tools: bool = True, price: tuple[float, float] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"vision": vision, "tools": tools}
    if price is not None:
        out["input_per_1m"], out["output_per_1m"] = price
    return out


CATALOG: dict[str, dict[str, dict[str, Any]]] = {
    "openai": {
        "gpt-5": _m(True, price=(1.25, 10.0)),
        "gpt-5-mini": _m(True, price=(0.25, 2.0)),
        "gpt-5-nano": _m(True, price=(0.05, 0.40)),
        "gpt-4.1": _m(True, price=(2.0, 8.0)),
        "gpt-4.1-mini": _m(True, price=(0.40, 1.60)),
        "gpt-4.1-nano": _m(True, price=(0.10, 0.40)),
        "gpt-4o": _m(True, price=(2.50, 10.0)),
        "gpt-4o-mini": _m(True, price=(0.15, 0.60)),
        "o3": _m(True, price=(2.0, 8.0)),
        "o4-mini": _m(True, price=(1.10, 4.40)),
        "o3-mini": _m(False, price=(1.10, 4.40)),
    },
    "anthropic": {
        "claude-opus-4-1": _m(True, price=(15.0, 75.0)),
        "claude-sonnet-4-5": _m(True, price=(3.0, 15.0)),
        "claude-haiku-4-5": _m(True, price=(1.0, 5.0)),
    },
    "google": {
        "gemini-2.5-pro": _m(True, price=(1.25, 10.0)),
        "gemini-2.5-flash": _m(True, price=(0.30, 2.50)),
        "gemini-2.5-flash-lite": _m(True, price=(0.10, 0.40)),
    },
    "deepseek": {
        "deepseek-chat": _m(False),
        "deepseek-reasoner": _m(False),
    },
    "xai": {
        "grok-4": _m(True, price=(3.0, 15.0)),
    },
    "groq": {
        "llama-3.3-70b-versatile": _m(False, price=(0.59, 0.79)),
    },
}


def _catalog(preset: str, model: str) -> dict[str, Any]:
    return dict((CATALOG.get(str(preset or "").lower()) or {}).get(str(model or "").strip().lower()) or {})


# ── What a provider's model list says ───────────────────────────────────


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _price_per_token(value: Any) -> float | None:
    """A price per token (OpenRouter's, as a string) in dollars per million;
    a negative price ("varies") is not a price."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number * 1_000_000, 6)


def from_listing(item: Any) -> dict[str, Any]:
    """The details one entry of a ``/models`` answer carries, by field --
    only those it states. OpenRouter: ``context_length``,
    ``top_provider.max_completion_tokens``, ``architecture.input_modalities``,
    ``supported_parameters``, ``pricing`` (per token). Groq:
    ``context_window``, ``max_completion_tokens``. Mistral:
    ``max_context_length``, ``capabilities``. vLLM: ``max_model_len``.
    Gemini: ``inputTokenLimit``, ``outputTokenLimit``."""
    if not isinstance(item, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("context_length", "context_window", "max_context_length", "max_model_len", "inputTokenLimit"):
        size = _int(item.get(key))
        if size:
            out["context_window"] = size
            break
    top = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    for value in (top.get("max_completion_tokens"), item.get("max_completion_tokens"), item.get("outputTokenLimit")):
        size = _int(value)
        if size:
            out["max_output"] = size
            break
    arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    modalities = arch.get("input_modalities")
    if isinstance(modalities, list):
        out["vision"] = "image" in [str(m).lower() for m in modalities]
    caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    if isinstance(caps.get("vision"), bool):
        out["vision"] = caps["vision"]
    if isinstance(caps.get("function_calling"), bool):
        out["tools"] = caps["function_calling"]
    params = item.get("supported_parameters")
    if isinstance(params, list):
        out["tools"] = "tools" in [str(p).lower() for p in params]
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    inp, outp = _price_per_token(pricing.get("prompt")), _price_per_token(pricing.get("completion"))
    if inp is not None and outp is not None:
        out["input_per_1m"], out["output_per_1m"] = inp, outp
    return out


# ── The owner's overrides ───────────────────────────────────────────────


def parse_overrides(raw: Any) -> dict[str, dict[str, Any]]:
    """The owner's details from the setting's stored value: each entry's
    known fields, typed; anything else is left out (the route refuses it)."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for name, entry in raw.items():
        key = str(name or "").strip().lower()
        if not key or not isinstance(entry, dict):
            continue
        values: dict[str, Any] = {}
        for f in ("context_window", "max_output"):
            size = _int(entry.get(f))
            if size and 1024 <= size <= 100_000_000:
                values[f] = size
        for f in ("vision", "tools"):
            if isinstance(entry.get(f), bool):
                values[f] = entry[f]
        if values:
            out[key] = values
    return out


def _store_get(store: Any, key: str) -> Any:
    from kazma_core.db.pg_helpers import store_errors

    try:
        if store is None:
            from kazma_core.config_store import get_config_store

            store = get_config_store()
        return store.get(key)
    except store_errors():  # an unreadable store knows nothing
        logger.debug("[model-details] %s unreadable", key, exc_info=True)
        return None


def details_for(provider: str, model: str, *, preset: str = "", store: Any = None) -> ModelDetails:
    """What is known about *model* served by *provider* (a row name; its
    *preset* when the row was made from one under another name). Reads the
    settings store: off the event loop."""
    name = str(model or "").strip()
    prov = str(provider or "").strip()
    out = ModelDetails()
    if not name:
        return out
    overrides = parse_overrides(_store_get(store, OVERRIDES_KEY))
    for key in (f"{prov.lower()}:{name.lower()}" if prov else "", name.lower()):
        if key and key in overrides:
            out.fill(overrides[key], "owner")
            break
    if prov:
        reported = _store_get(store, reported_key(prov))
        if isinstance(reported, dict) and isinstance(reported.get(name), dict):
            out.fill(reported[name], "provider")
    out.fill(_catalog(preset or prov, name), "catalog")
    return out
