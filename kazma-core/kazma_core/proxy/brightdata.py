"""Bright Data (formerly Luminati) residential proxy.

Same ``ProxyProvider`` interface as anyip. Unconfigured → ``get_proxy_url()``
is ``None`` (direct). No account required for tests. When username+password
are set, this is a real proxy URL builder (not a no-op stub).
"""

from __future__ import annotations

from urllib.parse import quote

from kazma_core.proxy.base import ProxyProvider

__all__ = ["BrightDataProvider"]


class BrightDataProvider(ProxyProvider):
    """``http://user:pass@brd.superproxy.io:22225`` when configured."""

    name = "brightdata"

    @staticmethod
    def _cfg(key: str, default: str = "") -> str:
        try:
            from kazma_core.config_store import get_config_store

            val = get_config_store().get(f"proxy.{key}")
            return str(val if val is not None else default).strip()
        except Exception:  # noqa: BLE001 — config best-effort
            return default

    def is_configured(self) -> bool:
        return bool(self._cfg("username") and self._cfg("password"))

    def get_proxy_url(self) -> str | None:
        if not self.is_configured():
            return None
        host = self._cfg("host") or "brd.superproxy.io"
        port = self._cfg("port") or "22225"
        user = quote(self._cfg("username"), safe="")
        password = quote(self._cfg("password"), safe="")
        return f"http://{user}:{password}@{host}:{port}"
