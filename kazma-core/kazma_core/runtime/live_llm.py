"""Call-time LLM client resolution.

The supervisor graph is compiled with a captured ``llm`` object. Saving a
key in Settings invalidates the registry cache but does **not** replace that
object, so later ReAct iterations (and unpinned turns) can send a stale or
empty ``Authorization`` header to the same URL Settings > Test just proved
works.

Live evidence (2026-09-11, thread ``7c5a3568-…``): iteration 0 with
``deepseek-flash`` succeeded; iteration 1/2 of the *same turn* 401'd against
``https://api.deepseek.com/v1`` with ``routed_model=""``. The key was never
invalid — the compile-time client was.

Every supervisor / respond LLM call must go through :func:`resolve_live_client`.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.runtime.turn_model import current_turn_model

__all__ = [
    "coerce_api_key",
    "key_is_usable",
    "resolve_live_client",
    "url_is_cloud",
    "url_is_local",
]

logger = logging.getLogger(__name__)

# Tokens that look like a key to ``bool(key)`` but are never a credential.
# ``sk-real-key`` leaked into live provider rows from test fixtures and was
# treated as usable — DeepSeek 401'd, Settings > Test on the real key passed.
_PLACEHOLDERS = frozenset({
    "",
    "not-needed",
    "***",
    "****",
    "********",
    "none",
    "null",
    "undefined",
    "sk-real-key",
    "your-api-key",
    "changeme",
    "api_key",
})


def coerce_api_key(value: Any) -> str:
    """Normalize a stored API key. Never ``str(None) == "None"``.

    Vault misses return ``None`` from ConfigStore. ``str(None)`` was sent as
    ``Authorization: Bearer None`` — a 401 that Test did not reproduce,
    because Test uses ``value or ""``.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return ""
    key = value.replace("\ufeff", "").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
        key = key[1:-1].strip()
    return key


def key_is_usable(key: Any) -> bool:
    """True when *key* is safe to send to a cloud LLM endpoint."""
    k = coerce_api_key(key)
    if not k:
        return False
    if k.lower() in _PLACEHOLDERS:
        return False
    if "****" in k or k.startswith("vault://"):
        return False
    return True


def url_is_local(url: str) -> bool:
    u = (url or "").lower()
    return any(
        tok in u
        for tok in ("localhost", "127.0.0.1", "0.0.0.0", "lmstudio", "lm-studio", ":11434")
    )


def url_is_cloud(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    return (url.startswith("http://") or url.startswith("https://")) and not url_is_local(url)


def _is_real_provider(obj: Any) -> bool:
    """True only for a genuine provider whose client we may swap.

    ``resolve_live_client`` promises to leave a test double alone, but an
    ``isinstance`` check alone does not deliver that: ``MagicMock(spec=
    LLMProvider)`` sets ``__class__`` and therefore *passes* isinstance. The
    mock was being replaced by a live registry client, so a caller that had
    carefully stubbed the LLM got a real HTTP call to whatever provider
    happened to be active — which is how an integration test ended up dialling
    the operator's configured endpoint.

    Checking for ``unittest.mock`` first is what makes the docstring true.
    """
    try:
        from unittest.mock import Mock

        if isinstance(obj, Mock):
            return False
    except Exception:  # pragma: no cover - stdlib import cannot realistically fail
        pass
    try:
        from kazma_core.llm_provider import LLMProvider

        return isinstance(obj, LLMProvider)
    except Exception:
        return False


def _clean_model(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s or None


def resolve_live_client(
    fallback: Any,
    *,
    state: dict[str, Any] | None = None,
    model: str | None = None,
) -> tuple[Any, str | None]:
    """Return ``(client, model_id)`` for this LLM call.

    Precedence for the model id: turn pin → checkpointed ``last_model`` →
    explicit *model* (router hint). Pin and last_model win so a later ReAct
    iteration cannot be rerouted onto a placeholder provider after the
    first hop already succeeded with DeepSeek.

    The client is the registry's current client for that model (fresh
    credentials) unless *fallback* is a test mock.

    Never raises — a registry failure keeps *fallback*.
    """
    pinned = (
        current_turn_model()
        or _clean_model((state or {}).get("last_model"))
        or _clean_model(model)
    )

    registry_client = None
    try:
        from kazma_core.model_registry import get_model_registry

        registry_client = get_model_registry().get_client(pinned)
    except Exception:
        logger.debug("resolve_live_client: registry get_client failed", exc_info=True)

    if registry_client is None:
        return fallback, pinned

    if not _is_real_provider(fallback):
        return fallback, pinned

    fb_cfg = getattr(fallback, "config", None)
    rg_cfg = getattr(registry_client, "config", None)
    fb_key = coerce_api_key(getattr(fb_cfg, "api_key", ""))
    fb_url = str(getattr(fb_cfg, "base_url", "") or "")
    rg_key = coerce_api_key(getattr(rg_cfg, "api_key", ""))
    rg_url = str(getattr(rg_cfg, "base_url", "") or "")
    rg_model = _clean_model(getattr(rg_cfg, "model", None))

    # A pinned / checkpointed model always uses the registry client that
    # owns it — the captured graph llm may point at the right URL with
    # yesterday's key.
    if pinned:
        return registry_client, pinned

    if fb_url != rg_url or fb_key != rg_key:
        return registry_client, rg_model or pinned

    if url_is_cloud(fb_url) and not key_is_usable(fb_key):
        return registry_client, rg_model or pinned

    return fallback, pinned or rg_model
