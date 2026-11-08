"""What a model call costs -- or that its price is not known.

Every OpenAI-compatible call was priced at $0.15 / $0.60 per million tokens
(``LLMConfig``'s built-in default, gpt-4o-mini's price): DeepSeek, OpenRouter,
Groq and a model on the owner's own computer alike, and Anthropic's and
Bedrock's tables answered $3 / $15 for any model they did not list by exact
name. Those numbers fed the call ledger, the turn's stats line, the
Dashboard's spend, ``/cost`` and the daily cost limit (providers stage 1,
2026-11-08).

A price is now one of, in order:

1. the owner's own price for the model (Settings -> Providers -> Model
   prices: :data:`OWNER_PRICES_KEY`), under ``provider:model`` or the bare
   model name;
2. free, for a server on this computer or its network
   (:func:`kazma_core.runtime.live_llm.url_is_local`);
3. a vendor table the client keeps (Anthropic, Bedrock), matched by the
   longest name the model's id starts with, so a dated id
   (``claude-sonnet-4-20250514``) finds ``claude-sonnet-4``;
4. not known: the call's cost is recorded as unknown, never borrowed from
   another model.

A provider that reports what a call cost wins over all of them
(:func:`reported_cost`: OpenRouter's ``usage.cost``, in dollars, on every
response).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_PRICE_PER_1M",
    "OWNER_PRICES_KEY",
    "Price",
    "parse_owner_prices",
    "price_for",
    "reported_cost",
    "table_price",
]

#: The setting holding the owner's prices: ``{"provider:model" or "model":
#: {"input": usd_per_1m, "output": usd_per_1m}}``.
OWNER_PRICES_KEY = "models.prices"

#: The highest price per million tokens a setting may hold (a typo guard:
#: the dearest model listed anywhere in 2026 is well under it).
MAX_PRICE_PER_1M = 1000.0


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens, and where the price came from."""

    input_per_1m: float
    output_per_1m: float
    source: str  # "owner" | "local" | "table"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > MAX_PRICE_PER_1M:
        return None
    return number


def parse_owner_prices(raw: Any) -> dict[str, Price]:
    """The owner's prices from the setting's stored value; an entry that is
    not two numbers in range is left out (the Settings route refuses it)."""
    out: dict[str, Price] = {}
    if not isinstance(raw, dict):
        return out
    for name, entry in raw.items():
        key = str(name or "").strip().lower()
        if not key or not isinstance(entry, dict):
            continue
        inp, outp = _number(entry.get("input")), _number(entry.get("output"))
        if inp is None or outp is None:
            continue
        out[key] = Price(inp, outp, "owner")
    return out


def _owner_prices(store: Any = None) -> dict[str, Price]:
    """The owner's prices. Reads the settings store: off the event loop."""
    try:
        if store is None:
            from kazma_core.config_store import get_config_store

            store = get_config_store()
        return parse_owner_prices(store.get(OWNER_PRICES_KEY))
    except Exception:  # noqa: BLE001 - an unreadable store prices nothing
        logger.debug("[prices] owner prices unreadable", exc_info=True)
        return {}


def price_for(provider: str, base_url: str, model: str, *, store: Any = None) -> Price | None:
    """The price of *model* served by *provider* at *base_url*, or None when
    it is not known. The vendor tables are the clients' own (step 3 is
    :func:`table_price`, asked by the Anthropic and Bedrock clients)."""
    name = str(model or "").strip().lower()
    prov = str(provider or "").strip().lower()
    prices = _owner_prices(store)
    if name:
        for key in (f"{prov}:{name}" if prov else "", name):
            if key and key in prices:
                return prices[key]
    from kazma_core.runtime.live_llm import url_is_local

    if url_is_local(base_url):
        return Price(0.0, 0.0, "local")
    return None


#: What may follow a table's model name in an id of that same model: a date
#: (``-20250514``, Vertex's ``@20250514``), ``-latest``, ``-instruct``, a
#: Bedrock version (``-v1:0``, ``:0``). Never another version number:
#: ``claude-opus-4-5`` is not ``claude-opus-4`` (it costs a third as much).
_SAME_MODEL_SUFFIX = re.compile(r"^(?:$|[-@]\d{8}|-latest|-instruct|-v\d|:\d)")


def table_price(table: dict[str, tuple[float, float]], model: str) -> tuple[float, float] | None:
    """A vendor table's price for *model*, or None when the table does not
    name it.

    The entry whose name the id holds -- at its start, or after a Bedrock
    region and vendor (``us.anthropic.``) or a vendor prefix (``anthropic/``)
    -- followed by nothing but a date or variant (:data:`_SAME_MODEL_SUFFIX`).
    The tables answered $3 / $15 for any model they did not name exactly.
    """
    mid = str(model or "").strip().lower()
    if not mid:
        return None
    best = ""
    for name in table:
        n = name.lower()
        for start in (0, mid.find(f".{n}") + 1 or -1, mid.find(f"/{n}") + 1 or -1):
            if start < 0 or not mid.startswith(n, start):
                continue
            if _SAME_MODEL_SUFFIX.match(mid[start + len(n):]) and len(n) > len(best):
                best = name
    return table[best] if best else None


def reported_cost(usage: Any, base_url: str) -> float | None:
    """What the provider says the call cost, in dollars, when it says so.

    OpenRouter puts it in ``usage.cost`` on every response (its credits are
    dollars). Other providers' ``cost`` fields are not read: their units are
    not documented the same way.
    """
    if not isinstance(usage, dict):
        return None
    host = str(base_url or "").lower()
    if "openrouter.ai" not in host:
        return None
    value = usage.get("cost")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None
