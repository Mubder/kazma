"""Non-Stop & Self-Healing configuration.

Single source of truth for the ``agent.nonstop.*`` settings tree. Mirrors the
live-re-read pattern of ``get_hitl_config()`` / ``get_proxy_provider()``:
ConfigStore (Settings UI) first, ``kazma.yaml`` fallback, never raises — any
error yields safe defaults with the feature OFF.

Settings tree (ConfigStore flat dotted keys / YAML ``agent.nonstop``):

    enabled                         master toggle (default False)
    watchdog.stall_threshold_seconds
    watchdog.tool_timeout_seconds     (also read by graph_builder)
    healing.max_recovery_attempts
    healing.backoff_base_seconds
    healing.backoff_coefficient
    healing.backoff_max_seconds
    failover.enabled
    failover.chain                    list[str] of model ids
    failover.trigger_threshold        consecutive transient failures before failover
    failover.cooldown_seconds         before the primary model is tried again
    context.emergency_compact_threshold
    ledger.enabled                    per-LLM-call SQLite ledger
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = ["NonStopConfig", "get_nonstop_config"]

logger = logging.getLogger(__name__)


@dataclass
class WatchdogConfig:
    stall_threshold_seconds: float = 180.0
    tool_timeout_seconds: float = 120.0


@dataclass
class HealingConfig:
    max_recovery_attempts: int = 3
    backoff_base_seconds: float = 5.0
    backoff_coefficient: float = 2.0
    backoff_max_seconds: float = 300.0


@dataclass
class FailoverConfig:
    enabled: bool = False
    chain: list[str] = field(default_factory=list)
    trigger_threshold: int = 2
    cooldown_seconds: float = 300.0


@dataclass
class ContextRecoveryConfig:
    emergency_compact_threshold: float = 0.92


@dataclass
class NonStopConfig:
    enabled: bool = False
    watchdog: WatchdogConfig = field(default_factory=WatchdogConfig)
    healing: HealingConfig = field(default_factory=HealingConfig)
    failover: FailoverConfig = field(default_factory=FailoverConfig)
    context: ContextRecoveryConfig = field(default_factory=ContextRecoveryConfig)
    ledger_enabled: bool = True


def _as_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _as_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _as_bool(val: Any, default: bool) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    if val is None:
        return default
    return bool(val)


def _as_str_list(val: Any) -> list[str]:
    if isinstance(val, (list, tuple)):
        return [str(v).strip() for v in val if str(v or "").strip()]
    if isinstance(val, str) and val.strip():
        return [p.strip() for p in val.split(",") if p.strip()]
    return []


def get_nonstop_config(raw_config: dict[str, Any] | None = None) -> NonStopConfig:
    """Resolve the non-stop config: ConfigStore overrides over YAML, never raises."""
    cfg = NonStopConfig()
    try:
        yaml_ns: dict[str, Any] = {}
        if raw_config is None:
            try:
                from kazma_core.config_loader import load_config

                raw_config = load_config()
            except Exception:
                raw_config = {}
        yaml_ns = (raw_config.get("agent", {}) or {}).get("nonstop", {}) or {}

        def y(path: list[str], default: Any) -> Any:
            node: Any = yaml_ns
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

        cfg.enabled = _as_bool(y(["enabled"], cfg.enabled), cfg.enabled)
        cfg.watchdog.stall_threshold_seconds = _as_float(
            y(["watchdog", "stall_threshold_seconds"], cfg.watchdog.stall_threshold_seconds),
            cfg.watchdog.stall_threshold_seconds,
        )
        cfg.watchdog.tool_timeout_seconds = _as_float(
            y(["watchdog", "tool_timeout_seconds"], cfg.watchdog.tool_timeout_seconds),
            cfg.watchdog.tool_timeout_seconds,
        )
        cfg.healing.max_recovery_attempts = _as_int(
            y(["healing", "max_recovery_attempts"], cfg.healing.max_recovery_attempts),
            cfg.healing.max_recovery_attempts,
        )
        cfg.healing.backoff_base_seconds = _as_float(
            y(["healing", "backoff_base_seconds"], cfg.healing.backoff_base_seconds),
            cfg.healing.backoff_base_seconds,
        )
        cfg.healing.backoff_coefficient = _as_float(
            y(["healing", "backoff_coefficient"], cfg.healing.backoff_coefficient),
            cfg.healing.backoff_coefficient,
        )
        cfg.healing.backoff_max_seconds = _as_float(
            y(["healing", "backoff_max_seconds"], cfg.healing.backoff_max_seconds),
            cfg.healing.backoff_max_seconds,
        )
        cfg.failover.enabled = _as_bool(
            y(["failover", "enabled"], cfg.failover.enabled), cfg.failover.enabled
        )
        cfg.failover.chain = _as_str_list(y(["failover", "chain"], cfg.failover.chain))
        cfg.failover.trigger_threshold = _as_int(
            y(["failover", "trigger_threshold"], cfg.failover.trigger_threshold),
            cfg.failover.trigger_threshold,
        )
        cfg.failover.cooldown_seconds = _as_float(
            y(["failover", "cooldown_seconds"], cfg.failover.cooldown_seconds),
            cfg.failover.cooldown_seconds,
        )
        cfg.context.emergency_compact_threshold = _as_float(
            y(["context", "emergency_compact_threshold"], cfg.context.emergency_compact_threshold),
            cfg.context.emergency_compact_threshold,
        )
        cfg.ledger_enabled = _as_bool(y(["ledger", "enabled"], cfg.ledger_enabled), cfg.ledger_enabled)

        # ConfigStore (Settings UI) overrides — flat dotted keys win over YAML.
        try:
            from kazma_core.config_store import get_config_store

            cs = get_config_store()

            def c(key: str, current: Any, conv: Any) -> Any:
                val = cs.get(f"agent.nonstop.{key}")
                return current if val is None else conv(val, current)

            cfg.enabled = c("enabled", cfg.enabled, _as_bool)
            cfg.watchdog.stall_threshold_seconds = c(
                "watchdog.stall_threshold_seconds", cfg.watchdog.stall_threshold_seconds, _as_float
            )
            cfg.watchdog.tool_timeout_seconds = c(
                "watchdog.tool_timeout_seconds", cfg.watchdog.tool_timeout_seconds, _as_float
            )
            cfg.healing.max_recovery_attempts = c(
                "healing.max_recovery_attempts", cfg.healing.max_recovery_attempts, _as_int
            )
            cfg.healing.backoff_base_seconds = c(
                "healing.backoff_base_seconds", cfg.healing.backoff_base_seconds, _as_float
            )
            cfg.healing.backoff_coefficient = c(
                "healing.backoff_coefficient", cfg.healing.backoff_coefficient, _as_float
            )
            cfg.healing.backoff_max_seconds = c(
                "healing.backoff_max_seconds", cfg.healing.backoff_max_seconds, _as_float
            )
            cfg.failover.enabled = c("failover.enabled", cfg.failover.enabled, _as_bool)
            chain_val = cs.get("agent.nonstop.failover.chain")
            if chain_val is not None:
                cfg.failover.chain = _as_str_list(chain_val)
            cfg.failover.trigger_threshold = c(
                "failover.trigger_threshold", cfg.failover.trigger_threshold, _as_int
            )
            cfg.failover.cooldown_seconds = c(
                "failover.cooldown_seconds", cfg.failover.cooldown_seconds, _as_float
            )
            cfg.context.emergency_compact_threshold = c(
                "context.emergency_compact_threshold",
                cfg.context.emergency_compact_threshold,
                _as_float,
            )
            cfg.ledger_enabled = c("ledger.enabled", cfg.ledger_enabled, _as_bool)
        except Exception as exc:  # ConfigStore unavailable → YAML/defaults only
            logger.debug("[nonstop] ConfigStore read failed, using YAML/defaults: %s", exc)
    except Exception as exc:  # noqa: BLE001 — config must never break the agent
        logger.debug("[nonstop] config resolution failed, using defaults: %s", exc)
        return NonStopConfig()
    return cfg
