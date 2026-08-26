"""Live X connector config (ConfigStore + env). Never logs secret values."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "XCredentials",
    "XConfig",
    "get_x_config",
    "x_posting_enabled",
    "CREDENTIAL_KEYS",
    "ENV_CREDENTIAL_KEYS",
]

# ConfigStore dotted keys — last segments match is_sensitive_config_key
# (api_key, *_secret, access_token) so they auto-vault when KAZMA_VAULT_KEY is set.
CREDENTIAL_KEYS: tuple[str, str, str, str] = (
    "connectors.x.api_key",
    "connectors.x.api_key_secret",
    "connectors.x.access_token",
    "connectors.x.access_token_secret",
)

ENV_CREDENTIAL_KEYS: tuple[str, str, str, str] = (
    "X_API_KEY",
    "X_API_KEY_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
)

# Optional vault names matching the chat-side store plan (never echoed).
_VAULT_NAMES: tuple[str, str, str, str] = (
    "x_api_key",
    "x_api_key_secret",
    "x_access_token",
    "x_access_token_secret",
)


@dataclass(frozen=True)
class XCredentials:
    api_key: str
    api_key_secret: str
    access_token: str
    access_token_secret: str

    def complete(self) -> bool:
        return all(
            [
                self.api_key,
                self.api_key_secret,
                self.access_token,
                self.access_token_secret,
            ]
        )


@dataclass(frozen=True)
class XConfig:
    enabled: bool
    handle: str
    credentials: XCredentials
    max_posts_per_day: int
    max_posts_per_month: int
    max_mentions: int
    max_cashtags: int
    max_hashtags: int
    max_chars: int
    duplicate_window_days: int
    kill_switch: bool

    def can_post(self) -> bool:
        return (
            self.enabled
            and not self.kill_switch
            and self.credentials.complete()
        )


def _env_flag(name: str) -> bool | None:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return None
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return None


def _cs_get(key: str) -> Any:
    try:
        from kazma_core.config_store import get_config_store

        return get_config_store().get(key)
    except Exception:
        logger.debug("[x_api] ConfigStore read failed for %s", key, exc_info=True)
        return None


def _vault_get(name: str) -> str:
    try:
        from kazma_core.security.vault import get_vault

        vault = get_vault()
        if vault is None:
            return ""
        val = vault.retrieve(name)
        return str(val).strip() if val else ""
    except Exception:
        return ""


def _int_cfg(key: str, default: int, *, lo: int, hi: int) -> int:
    raw = _cs_get(key)
    try:
        n = int(raw) if raw is not None and str(raw).strip() != "" else default
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _load_credentials() -> XCredentials:
    values = ["", "", "", ""]
    for i, env_name in enumerate(ENV_CREDENTIAL_KEYS):
        values[i] = (os.environ.get(env_name) or "").strip()
    if not all(values):
        for i, key in enumerate(CREDENTIAL_KEYS):
            if values[i]:
                continue
            got = _cs_get(key)
            if isinstance(got, str) and got.strip() and not got.startswith("vault://"):
                values[i] = got.strip()
    if not all(values):
        for i, name in enumerate(_VAULT_NAMES):
            if values[i]:
                continue
            values[i] = _vault_get(name)
    return XCredentials(*values)


def x_posting_enabled() -> bool:
    """Kill-switch + enabled flag. ``KAZMA_X_POST=0`` always wins."""
    flag = _env_flag("KAZMA_X_POST")
    if flag is False:
        return False
    enabled = _cs_get("connectors.x.enabled")
    if enabled is None:
        return False
    if isinstance(enabled, str):
        return enabled.strip().lower() in ("1", "true", "yes", "on")
    return bool(enabled)


def get_x_config() -> XConfig:
    """Live-read config. Never raises."""
    kill = _env_flag("KAZMA_X_POST") is False
    enabled = False if kill else x_posting_enabled()
    handle = str(_cs_get("connectors.x.handle") or "").strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle.lstrip("@")
    return XConfig(
        enabled=enabled,
        handle=handle,
        credentials=_load_credentials(),
        max_posts_per_day=_int_cfg("connectors.x.max_posts_per_day", 8, lo=1, hi=50),
        max_posts_per_month=_int_cfg("connectors.x.max_posts_per_month", 80, lo=1, hi=500),
        max_mentions=_int_cfg("connectors.x.max_mentions", 2, lo=0, hi=5),
        max_cashtags=_int_cfg("connectors.x.max_cashtags", 1, lo=0, hi=3),
        max_hashtags=_int_cfg("connectors.x.max_hashtags", 4, lo=0, hi=8),
        max_chars=_int_cfg("connectors.x.max_chars", 280, lo=1, hi=25000),
        duplicate_window_days=_int_cfg(
            "connectors.x.duplicate_window_days", 30, lo=1, hi=365
        ),
        kill_switch=kill,
    )
