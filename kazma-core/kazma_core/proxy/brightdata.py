"""Bright Data (formerly Luminati) residential proxy.

Same ``ProxyProvider`` interface as anyip. Unconfigured → ``get_proxy_url()``
is ``None`` (direct). No account required for tests. When username+password
are set, this is a real proxy URL builder (not a no-op stub).
"""

from __future__ import annotations

from urllib.parse import quote

from kazma_core.proxy.base import ProxyProvider
from kazma_core.proxy.config import proxy_config_scope, proxy_value

__all__ = ["BrightDataProvider"]


class BrightDataProvider(ProxyProvider):
    """``http://user:pass@brd.superproxy.io:22225`` when configured."""

    name = "brightdata"

    @staticmethod
    def _cfg(key: str, default: str = "") -> str:
        return str(proxy_value(key, default)).strip()


    @proxy_config_scope()
    def is_configured(self) -> bool:
        return bool(self._cfg("username") and self._cfg("password"))

    @proxy_config_scope()
    def get_proxy_url(self) -> str | None:
        if not self.is_configured():
            return None
        host = self._cfg("host") or "brd.superproxy.io"
        port = self._cfg("port") or "22225"
        user = quote(self._cfg("username"), safe="")
        password = quote(self._cfg("password"), safe="")
        return f"http://{user}:{password}@{host}:{port}"
