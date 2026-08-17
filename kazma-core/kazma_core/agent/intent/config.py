"""Intent engine kill-switches — live ConfigStore reads, never raise."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

__all__ = [
    "intent_engine_enabled",
    "intent_execute_enabled",
    "intent_tier2_enabled",
]


def _env_off(name: str) -> bool:
    return (os.environ.get(name, "") or "").strip().lower() in ("0", "false", "no", "off")


def _config_on(key: str, default: bool = True) -> bool:
    try:
        from kazma_core.config_store import get_config_store

        v = get_config_store().get(key)
        if v is None:
            return default
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        return default


def intent_engine_enabled() -> bool:
    if _env_off("KAZMA_INTENT_ENGINE"):
        return False
    return _config_on("agent.intent.enabled", True)


def intent_execute_enabled() -> bool:
    if _env_off("KAZMA_INTENT_EXECUTE"):
        return False
    return _config_on("agent.intent.execute_enabled", True)


def intent_tier2_enabled() -> bool:
    if _env_off("KAZMA_INTENT_TIER2"):
        return False
    return _config_on("agent.intent.tier2_enabled", True)
