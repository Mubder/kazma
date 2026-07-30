"""anyip.io residential/mobile rotating proxy provider.

anyip.io exposes a single endpoint (``portal.anyip.io:1080``, HTTP+SOCKS5 on the
same port) authenticated by username/password. Rotation, network type, country,
and session stickiness are controlled by **flags appended to the username**
rather than a separate API — e.g. ``user_YOURID_type_residential_country_US``.

Config (ConfigStore ``proxy.*``, password vault-encrypted):
  - ``proxy.host``       (default ``portal.anyip.io``)
  - ``proxy.port``       (default ``1080``)
  - ``proxy.username``   (e.g. ``user_YOURID``)
  - ``proxy.password``   (vault-encrypted)
  - ``proxy.network``    (``residential`` | ``mobile`` | ``mixed``; default ``mixed``)
  - ``proxy.country``    (optional ISO code, e.g. ``US``)
  - ``proxy.session_sticky`` (bool; default False = rotate per request)

When ``session_sticky`` is False, every ``get_proxy_url()`` call appends a fresh
random session id so each request exits from a different IP (true rotation).
When True, a stable session id is reused so requests share an IP (needed for
logins / multi-page flows that expect session continuity).
"""

from __future__ import annotations

import logging
import random
from typing import Any
from urllib.parse import quote

from kazma_core.proxy.base import ProxyProvider

__all__ = ["AnyIpProvider"]

logger = logging.getLogger(__name__)


class AnyIpProvider(ProxyProvider):
    """Residential/mobile rotating proxy via anyip.io."""

    name = "anyip"

    def __init__(self) -> None:
        # A sticky session id, minted once and reused while session_sticky=True.
        self._sticky_session: str = f"sess_{random.randint(100000, 999999)}"

    # ── Config helpers (read live from ConfigStore each call) ───────────

    @staticmethod
    def _cfg(key: str, default: Any = "") -> Any:
        try:
            from kazma_core.config_store import get_config_store

            val = get_config_store().get(f"proxy.{key}")
            return val if val is not None else default
        except Exception:  # noqa: BLE001 — config best-effort
            return default

    def is_configured(self) -> bool:
        return bool(str(self._cfg("username", "")).strip()) and bool(
            str(self._cfg("password", "")).strip()
        )

    def _build_username(self) -> str:
        """Build the flag-decorated username anyip.io expects."""
        base = str(self._cfg("username", "")).strip()
        if not base:
            return ""
        parts = [base]

        network = str(self._cfg("network", "mixed")).strip().lower()
        if network and network != "mixed":
            parts.append(f"type_{network}")

        country = str(self._cfg("country", "")).strip().upper()
        if country:
            parts.append(f"country_{country}")

        sticky = bool(self._cfg("session_sticky", False))
        if sticky:
            parts.append(f"sessid_{self._sticky_session}")
        else:
            # Rotate: a fresh session id per request → new exit IP each time.
            parts.append(f"sessid_{random.randint(100000, 999999)}")

        return "_".join(parts)

    def get_proxy_url(self) -> str | None:
        """Return ``http://USERFLAGS:pass@portal.anyip.io:1080`` or None."""
        if not self.is_configured():
            return None
        host = str(self._cfg("host", "portal.anyip.io")).strip() or "portal.anyip.io"
        port = str(self._cfg("port", "1080")).strip() or "1080"
        username = self._build_username()
        if not username:
            return None
        password = str(self._cfg("password", "")).strip()
        # URL-encode creds so special chars don't break the URL.
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}"
        return f"http://{auth}@{host}:{port}"
