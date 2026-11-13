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
from collections.abc import Iterator
from contextlib import contextmanager
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


#: Name endings of hosts on this computer's own network (RFC 6762 ``.local``,
#: RFC 8375 ``.home.arpa``, Docker's ``host.docker.internal``, routers' ``.lan``).
_NETWORK_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localhost", ".localdomain")


def url_is_local(url: str) -> bool:
    """True for a model server on this computer or its own network.

    Such a server may be called without a key, and its tokens cost nothing
    (``kazma_core.model_prices``). It was decided by text in the address
    (localhost, 127.0.0.1, :11434), so a keyless vLLM or llama.cpp on
    192.168.1.20 was taken for a cloud provider with no key: swapped for
    another provider, refused by Test, hidden by the chat picker (providers
    stage 1, 2026-11-08). Now: loopback, private and link-local addresses,
    a host name with no dot (``gpu-box``), and the network name endings
    above -- and the old name checks, kept for addresses already saved.
    Never link-local (169.254.169.254 is cloud metadata).
    """
    u = (url or "").strip().lower()
    if not u:
        return False
    if any(tok in u for tok in ("localhost", "127.0.0.1", "0.0.0.0", "lmstudio", "lm-studio", ":11434")):
        return True
    import ipaddress
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(u if "://" in u else f"http://{u}").hostname or "").strip(".")
    except ValueError:
        return False
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host.endswith(_NETWORK_SUFFIXES)
    # One rule with model discovery (kazma_core.security.ssrf.own_network_ip):
    # loopback, private, Tailscale's shared space; never link-local or a
    # cloud metadata address.
    from kazma_core.security.ssrf import own_network_ip

    return own_network_ip(ip)


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


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or url
    except Exception:  # noqa: BLE001
        return url


def _provider_serves(base_url: str, model: str) -> bool:
    """Whether *base_url*'s provider is known to offer *model*.

    Conservative: unknown (no catalog, lookup failure, empty) returns True, so
    this can only ever REJECT a pairing we can positively disprove. Used to
    decide whether a pinned model may be carried onto a substituted provider.
    """
    if not base_url or not model:
        return True
    try:
        from kazma_core.llm_provider import LLMProvider

        catalog = LLMProvider._known_models_for(base_url)
    except Exception:  # noqa: BLE001 — never fail a turn over a lookup
        return True
    if not catalog:
        return True
    return model in catalog or model.split("/", 1)[-1] in catalog


@contextmanager
def _turn_tenant(state: dict[str, Any] | None) -> Iterator[None]:
    """Install this turn's tenant, if the caller does not already have one.

    An ambient tenant is never overridden: a request that already resolved
    its tenant outranks whatever a checkpointed state blob carries.
    """
    from kazma_core.tenant_context import (
        get_current_tenant_id,
        reset_current_tenant_id,
        set_current_tenant_id,
    )

    if get_current_tenant_id():
        yield
        return
    tid = str((state or {}).get("tenant_id") or "default")
    token = set_current_tenant_id(tid)
    try:
        yield
    finally:
        reset_current_tenant_id(token)


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
    # The model id the provider is asked for. A pin may be a ``provider:model``
    # reference (the chat's model picker, providers stage 3): the registry
    # builds that provider's client from it, and the provider is asked for the
    # model part alone. Compared or sent whole, a chat pinned to a model other
    # than its provider's default got the default (live 2026-11-12: "does not
    # serve 'deepseek:deepseek-flash' (its catalog has 'deepseek-flash')").
    pinned_model = pinned
    try:
        from kazma_core.model_registry import get_model_registry

        # Build the client AS THE TURN'S TENANT. Provider API keys are vault
        # secrets, and a key saved through Settings is written under the
        # request's tenant ("default" for a single-user install). The vault
        # falls back tenant -> global and deliberately not the reverse, so a
        # context-less caller reads None for a key that is sitting right
        # there. The registry then treats the provider as keyless and
        # SUBSTITUTES another vendor.
        #
        # That is not a theory. Live, 2026-09-16: the operator's DeepSeek key
        # was present and decrypting, this node ran with no tenant, the
        # registry swapped in Z.AI, and Telegram answered with Z.AI's
        # {"code":"1211","message":"Unknown Model"} for a DeepSeek model id.
        # Cron had the identical hole in 2026-09-12 (401, twice, at 09:00) and
        # was fixed the same way, at the call site -- see
        # `tests/test_cron_tenant_context.py` for why the fix does not belong
        # in the vault's fallback.
        with _turn_tenant(state):
            registry_client = get_model_registry().get_client(pinned)
            split = getattr(get_model_registry(), "split_model_ref", None)
            if pinned and callable(split):
                pinned_model = split(pinned)[1] or pinned
    except Exception:
        logger.debug("resolve_live_client: registry get_client failed", exc_info=True)

    if registry_client is None:
        return fallback, pinned_model

    if not _is_real_provider(fallback):
        return fallback, pinned_model

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
        # ...but the client and the model must travel TOGETHER.
        #
        # get_client(pinned) may hand back a DIFFERENT provider than the one
        # that owns `pinned` — most commonly because the pinned model's
        # provider has no usable API key, so the registry substitutes one
        # that has. Returning (substituted_client, pinned) then sends one
        # vendor's model id to another vendor's endpoint. Live, 2026-09-16:
        #
        #   registry: provider=deepseek model=deepseek-flash has no usable
        #             API key; using Z.AI/glm-5.3-flash which has one
        #   client  : base_url=https://api.z.ai/...  model=glm-5.3-flash
        #   here    : returned (that Z.AI client, "deepseek-flash")
        #   Z.AI    : {"code":"1211","message":"Unknown Model, ..."}
        #
        # Only override when we can SHOW the substituted provider does not
        # serve the pinned model — an OpenAI-compatible provider takes the
        # model per request, so a differing `config.model` is a default, not
        # proof. No catalog => keep the pin (the pre-existing behaviour) and
        # say so, because guessing is what produced the bad pairing.
        if rg_model and rg_model != pinned_model and not _provider_serves(rg_url, pinned_model):
            logger.warning(
                "[live_llm] %s does not serve %r (its catalog has %r) — using "
                "the substituted provider's own model. A pinned model cannot "
                "be carried onto a provider that was swapped in for it.",
                _host_of(rg_url), pinned, rg_model,
            )
            return registry_client, rg_model
        return registry_client, pinned_model

    if fb_url != rg_url or fb_key != rg_key:
        return registry_client, rg_model or pinned_model

    if url_is_cloud(fb_url) and not key_is_usable(fb_key):
        return registry_client, rg_model or pinned_model

    return fallback, pinned_model or rg_model
