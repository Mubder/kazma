"""Proxy provider registry — resolves the active provider from live config.

``get_proxy_provider()`` reads ``proxy.provider`` from ConfigStore fresh on each
call (like HITL's ``get_hitl_config``), so a Settings change takes effect
without a server restart. The provider instance is cached per-name and rebuilt
only when the configured name changes.

Adding a new provider:
  1. Subclass :class:`ProxyProvider` in a new module under ``kazma_core.proxy``.
  2. Register its name → class in :data:`_PROVIDERS` below.
  3. Add it to the Settings dropdown.
No scraper changes required — the scraper talks to the interface.
"""

from __future__ import annotations

import logging
import threading

from kazma_core.proxy.anyip import AnyIpProvider
from kazma_core.proxy.base import NullProvider, ProxyProvider
from kazma_core.proxy.brightdata import BrightDataProvider
from kazma_core.proxy.oxylabs import OxylabsProvider

__all__ = ["get_proxy_provider", "list_provider_names"]

logger = logging.getLogger(__name__)

# Provider name → class. New providers register here.
_PROVIDERS: dict[str, type[ProxyProvider]] = {
    "none": NullProvider,
    "anyip": AnyIpProvider,
    "brightdata": BrightDataProvider,
    "oxylabs": OxylabsProvider,
}

_current_name: str = ""
_current_provider: ProxyProvider | None = None
_lock = threading.Lock()


def list_provider_names() -> list[str]:
    """Return the registered provider keys (for the Settings dropdown)."""
    return list(_PROVIDERS.keys())


def get_proxy_provider() -> ProxyProvider:
    """Return the active proxy provider, re-reading config live.

    Returns a :class:`NullProvider` (direct, no proxy) when the configured
    provider is unknown or config read fails — never raises, so the scraper
    always gets a usable provider.
    """
    global _current_name, _current_provider
    try:
        from kazma_core.config_store import get_config_store

        name = str(get_config_store().get("proxy.provider", "none") or "none").strip().lower()
    except Exception:  # noqa: BLE001 — config best-effort
        name = "none"

    if name not in _PROVIDERS:
        name = "none"

    with _lock:
        if _current_provider is None or name != _current_name:
            cls = _PROVIDERS[name]
            try:
                _current_provider = cls()
                _current_name = name
            except Exception as exc:  # noqa: BLE001 — never break the scraper
                logger.warning("[proxy] failed to init provider %s: %s — using direct", name, exc)
                _current_provider = NullProvider()
                _current_name = "none"
        return _current_provider
