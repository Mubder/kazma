"""Oxylabs residential proxy stub.

Same ``ProxyProvider`` interface as anyip. Unconfigured → ``get_proxy_url()``
is ``None`` (direct). No account required for tests.
"""

from __future__ import annotations

from urllib.parse import quote

from kazma_core.proxy.base import ProxyProvider

__all__ = ["OxylabsProvider"]


class OxylabsProvider(ProxyProvider):
    """``http://user:pass@pr.oxylabs.io:7777`` when configured."""

    name = "oxylabs"

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
        host = self._cfg("host") or "pr.oxylabs.io"
        port = self._cfg("port") or "7777"
        user = quote(self._cfg("username"), safe="")
        password = quote(self._cfg("password"), safe="")
        return f"http://{user}:{password}@{host}:{port}"
