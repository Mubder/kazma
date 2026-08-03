"""Bulletproof scraping via pluggable proxy providers (opt-in addon).

Default is direct (``NullProvider``). Configure a provider (e.g. anyip.io) in
Settings → System → Proxy Provider, or via ConfigStore ``proxy.*`` keys.
"""

from kazma_core.proxy.base import NullProvider, ProxyProvider
from kazma_core.proxy.client import (
    USER_AGENT_POOL,
    get_active_proxy_url,
    get_scraping_client,
    get_scraping_client_sync,
    playwright_proxy,
    random_user_agent,
)
from kazma_core.proxy.registry import get_proxy_provider, list_provider_names

__all__ = [
    "ProxyProvider",
    "NullProvider",
    "get_proxy_provider",
    "list_provider_names",
    "get_scraping_client",
    "get_scraping_client_sync",
    "get_active_proxy_url",
    "playwright_proxy",
    "random_user_agent",
    "USER_AGENT_POOL",
]
