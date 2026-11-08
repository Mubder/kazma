"""Several keys per provider, and the switch between them (providers stage 4).

A provider had one key: a second account of one vendor was a second provider
row (Z.AI and zai on the live install), and a key that ran out of credit or
requests stopped every chat until the owner pasted another. A provider row
now holds extra keys (``api_keys``: ``[{"id", "label", "api_key"}]``, vaulted
like its ``api_key``), tried after its own key, in order.

When a call fails for its KEY -- refused (HTTP 401/403), out of credit (402,
or a provider's "no balance" answer under any status), or out of requests
(429 after the client's own waits) -- the key rests
(:data:`REST_SECONDS`) and the client asks for the next one
(:func:`next_key`), says so once (an ops alert and the web banner), and sends
the call again with it. A key that rested is tried again when its rest ends.

What a key did is Kazma's own state, kept in the settings store under
``registry.key_state.<provider>`` (``settings_restore``: runtime): a key is
named there only by a fingerprint (:func:`fingerprint`), never stored twice.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_SWITCHES",
    "REST_SECONDS",
    "current_key",
    "fingerprint",
    "key_failure",
    "keys_of",
    "next_key",
]

#: How long a key rests after it failed, by why.
REST_SECONDS: dict[str, float] = {"refused": 24 * 3600.0, "no_credit": 6 * 3600.0, "rate_limited": 900.0}

#: At most this many switches for one call (a provider with many keys all
#: refused stops trying after this many).
MAX_SWITCHES = 5


def fingerprint(key: str) -> str:
    """A key's name in the state: the first 12 hex digits of its SHA-256."""
    return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()[:12]


def _state_key(provider: str) -> str:
    """The settings row holding *provider*'s key state."""
    return f"registry.key_state.{provider}"


def keys_of(primary: str, row: dict[str, Any] | None) -> list[tuple[str, str]]:
    """``[(label, key)]``: the provider's own key, then its extra keys in
    order; each key once, empty ones left out."""
    from kazma_core.runtime.live_llm import coerce_api_key, key_is_usable

    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    candidates = [("", primary)]
    for extra in (row or {}).get("api_keys") or []:
        if isinstance(extra, dict):
            candidates.append((str(extra.get("label") or extra.get("id") or ""), str(extra.get("api_key") or "")))
    for label, key in candidates:
        k = coerce_api_key(key)
        if key_is_usable(k) and k not in seen:
            seen.add(k)
            out.append((label, k))
    return out


def _resting(store: Any, provider: str) -> dict[str, dict[str, Any]]:
    from kazma_core.db.pg_helpers import store_errors

    try:
        raw = store.get(_state_key(provider))
    except store_errors():  # an unreadable state rests nothing
        logger.debug("[provider-keys] state unreadable", exc_info=True)
        return {}
    resting = (raw or {}).get("resting") if isinstance(raw, dict) else None
    return {str(k): v for k, v in resting.items() if isinstance(v, dict)} if isinstance(resting, dict) else {}


def current_key(store: Any, provider: str, primary: str, row: dict[str, Any] | None) -> str:
    """The key to send now: the first not resting, else the one whose rest
    ends first (a provider whose every key rests still gets a try)."""
    keys = keys_of(primary, row)
    if len(keys) <= 1:
        return keys[0][1] if keys else primary
    resting = _resting(store, provider)
    now = time.time()
    for _label, key in keys:
        if float((resting.get(fingerprint(key)) or {}).get("until") or 0) <= now:
            return key
    return min(keys, key=lambda lk: float((resting.get(fingerprint(lk[1])) or {}).get("until") or 0))[1]


def key_failure(exc: BaseException) -> tuple[str, float]:
    """``(why, retry_after)`` when *exc* failed for its key, else ``("", 0)``.
    ``why`` is ``refused``, ``no_credit`` or ``rate_limited``."""
    from kazma_core.retry import _extract_http_status_code

    text = str(exc or "").lower()
    if getattr(exc, "kind", "") == "rate_limit_exhausted":
        return "rate_limited", float(getattr(exc, "waited_s", 0) or 0)
    if "has no credit left" in text or "insufficient_quota" in text or "insufficient balance" in text:
        return "no_credit", 0.0
    status = _extract_http_status_code(exc if isinstance(exc, Exception) else Exception(str(exc)))
    if status is None:
        said = re.search(r"\bHTTP (\d{3})\b", str(exc or ""))
        status = int(said.group(1)) if said else None
    if status in (401, 403):
        return "refused", 0.0
    if status == 402:
        return "no_credit", 0.0
    return "", 0.0


def next_key(
    store: Any, provider: str, primary: str, row: dict[str, Any] | None,
    failed_key: str, why: str, retry_after: float = 0.0,
) -> tuple[str, str] | None:
    """Rest *failed_key* for *why*, and return ``(label, key)`` of the next
    key that is not resting, or None when there is none. Blocking (the
    settings store): off the loop."""
    keys = keys_of(primary, row)
    if len(keys) <= 1:
        return None
    now = time.time()
    rest = max(REST_SECONDS.get(why, 900.0), float(retry_after or 0))
    failed_fp = fingerprint(failed_key)

    def update(current: Any) -> dict[str, Any]:
        state = dict(current) if isinstance(current, dict) else {}
        resting = {k: v for k, v in (state.get("resting") or {}).items()
                   if isinstance(v, dict) and float(v.get("until") or 0) > now}
        resting[failed_fp] = {"until": now + rest, "why": why}
        state["resting"] = resting
        return state

    store.atomic_update(_state_key(provider), update, category="registry")
    resting = _resting(store, provider)
    for label, key in keys:
        fp = fingerprint(key)
        if fp != failed_fp and float((resting.get(fp) or {}).get("until") or 0) <= now:
            return label, key
    return None
