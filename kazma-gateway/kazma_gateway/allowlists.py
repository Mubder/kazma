"""Live connector allowlists — one apply path for boot, refresh, and Settings.

Adapters expose ``set_allowed_users`` (and Slack team/channel setters).
Settings writes ``connectors.<platform>.allowed_users`` (and Slack
``allowed_teams`` / ``allowed_channels``); this module pushes those values
onto the running adapter without requiring a process restart.

Also home of :func:`is_gateway_admin` — the admin gate for admin-grade
chat commands and alert-card buttons (audit H-8): "everyone may chat"
(allow_all) must never imply "everyone may install packages or change
global config".
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "GATEWAY_ADMINS_ENV",
    "apply_adapter_allowlists",
    "apply_gateway_allowlists",
    "is_gateway_admin",
    "split_ids",
]

#: Comma-separated admin identities (platform user ids or full ``platform:id``
#: sender ids). When set, it is the AUTHORITATIVE admin set — the per-platform
#: user allowlist no longer grants admin on its own.
GATEWAY_ADMINS_ENV = "KAZMA_GATEWAY_ADMINS"


def split_ids(raw: Any) -> list[str]:
    """Split a comma-separated ConfigStore value into stripped ids."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def _candidate_tokens(sender_id: str) -> set[str]:
    """Both the full sender id and its platform-local tail."""
    tokens = {sender_id}
    if ":" in sender_id:
        tail = sender_id.split(":", 1)[1]
        if tail:
            tokens.add(tail)
    return tokens


def is_gateway_admin(sender_id: str, platform: str = "") -> bool:
    """Whether this platform identity may use admin-grade gateway actions.

    Sources, in order:

    1. ``KAZMA_GATEWAY_ADMINS`` env (comma list of user ids or
       ``platform:id`` sender ids). When set it is authoritative.
    2. Membership in the platform's configured ``allowed_users`` list —
       the operator explicitly curated those users.
    3. Otherwise: NOT admin. In the default allow_all posture (empty
       allowlist) every workspace member may chat, and none of them may
       install packages, flip global config, or switch the global model.
    """
    sender_id = (sender_id or "").strip()
    if not sender_id:
        return False
    candidates = _candidate_tokens(sender_id)

    env_raw = (os.environ.get(GATEWAY_ADMINS_ENV) or "").strip()
    if env_raw:
        env_tokens: set[str] = set()
        for part in split_ids(env_raw):
            env_tokens |= _candidate_tokens(part)
        return bool(candidates & env_tokens)

    plat = (platform or "").strip().lower()
    if not plat and ":" in sender_id:
        plat = sender_id.split(":", 1)[0].strip().lower()
    if plat:
        try:
            from kazma_core.config_store import get_config_store

            raw = get_config_store().get(f"connectors.{plat}.allowed_users", "")
        except Exception:
            logger.debug("[allowlists] admin allowlist read failed", exc_info=True)
            return False
        allow_tokens: set[str] = set()
        for part in split_ids(raw):
            allow_tokens |= _candidate_tokens(part)
        return bool(candidates & allow_tokens)
    return False


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
