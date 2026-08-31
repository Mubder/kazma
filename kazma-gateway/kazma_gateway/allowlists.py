"""Live connector allowlists — one apply path for boot, refresh, and Settings.

Adapters expose ``set_allowed_users`` (and Slack team/channel setters).
Settings writes ``connectors.<platform>.allowed_users`` (and Slack
``allowed_teams`` / ``allowed_channels``); this module pushes those values
onto the running adapter without requiring a process restart.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "apply_adapter_allowlists",
    "apply_gateway_allowlists",
    "split_ids",
]


def split_ids(raw: Any) -> list[str]:
    """Split a comma-separated ConfigStore value into stripped ids."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _cs_get(config_store: Any, key: str, default: str = "") -> Any:
    try:
        return config_store.get(key, default)
    except Exception:
        logger.debug("[allowlists] config get %s failed", key, exc_info=True)
        return default


def apply_adapter_allowlists(adapter: Any, config_store: Any) -> None:
    """Push ConfigStore allowlists onto one live adapter."""
    if adapter is None or config_store is None:
        return
    name = str(getattr(adapter, "name", "") or "").strip().lower()
    if name == "telegram":
        raw = _cs_get(config_store, "connectors.telegram.allowed_users", "")
        ids: list[int] = []
        for part in split_ids(raw):
            try:
                ids.append(int(part))
            except ValueError:
                logger.warning("[allowlists] Invalid Telegram user id: %s", part)
        if hasattr(adapter, "set_allowed_users"):
            adapter.set_allowed_users(ids)
        return
    if name == "discord":
        raw = _cs_get(config_store, "connectors.discord.allowed_users", "")
        if hasattr(adapter, "set_allowed_users"):
            adapter.set_allowed_users(split_ids(raw))
        return
    if name == "slack":
        users = split_ids(_cs_get(config_store, "connectors.slack.allowed_users", ""))
        teams = split_ids(_cs_get(config_store, "connectors.slack.allowed_teams", ""))
        channels = split_ids(
            _cs_get(config_store, "connectors.slack.allowed_channels", "")
        )
        if hasattr(adapter, "set_allowed_users"):
            adapter.set_allowed_users(users)
        if hasattr(adapter, "set_allowed_teams"):
            adapter.set_allowed_teams(teams)
        if hasattr(adapter, "set_allowed_channels"):
            adapter.set_allowed_channels(channels)


def apply_gateway_allowlists(gateway: Any, config_store: Any) -> int:
    """Apply allowlists to every adapter on *gateway*. Returns adapter count."""
    if gateway is None:
        return 0
    adapters = list(getattr(gateway, "adapters", None) or [])
    for adapter in adapters:
        try:
            apply_adapter_allowlists(adapter, config_store)
        except Exception:
            logger.warning(
                "[allowlists] apply failed for %s",
                getattr(adapter, "name", type(adapter).__name__),
                exc_info=True,
            )
    return len(adapters)
