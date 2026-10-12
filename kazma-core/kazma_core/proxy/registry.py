"""Proxy provider registry — resolves the active provider from live config.

``get_proxy_provider()`` reads one ``proxy`` category snapshot fresh on each
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
from kazma_core.proxy.config import proxy_config_scope, proxy_value
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

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

    Only an explicit/missing ``none`` selects direct access. Configuration or
    construction errors are typed refusals; they never change the route.
    """
    global _current_name, _current_provider
    with proxy_config_scope():
        name = proxy_value('provider', 'none')
        if not isinstance(name, str) or name.strip().lower() not in _PROVIDERS:
            raise AcquisitionError(FetchErrorCode.ROUTING,
                'Unknown scraping proxy provider. Select a supported provider or none in Proxy Provider settings.')
        name = name.strip().lower()
        with _lock:
            if _current_provider is None or name != _current_name:
                cls = _PROVIDERS[name]
                try:
                    provider = cls()
                    if not isinstance(provider, ProxyProvider):
                        raise TypeError('Invalid proxy provider implementation')
                except Exception:
                    raise AcquisitionError(FetchErrorCode.ROUTING,
                        f'Scraping proxy provider {name} could not initialize; direct fallback was refused.') from None
                _current_provider, _current_name = provider, name
            return _current_provider
