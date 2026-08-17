"""Live config consolidator for the Commitment Layer (plan §3.4 / §18 Appendix B).

Single reader for every ``agent.commitment.*`` knob so operators can tune the
gate (mode, TTLs, retention, caps, thresholds) and toggle the kill-switch
without a redeploy. Mirrors the ``get_hitl_config`` / ``get_proxy_provider``
live-read pattern: env → ConfigStore → defaults, never raises.

The other modules read this lazily where they need a live value:
``is_commitment_enabled`` delegates here; ``authorize_effect`` reads the mode +
high-confidence threshold; the store's TTL/GC reads retention/cap defaults here
when callers don't override.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["get_commitment_config", "MODES"]

MODES = ("strict", "balanced", "autonomous", "yolo")

_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "mode": "balanced",
    "high_confidence": 0.85,
    "enforce_unknown_mutators": True,
    # Default ON (2026-08-15): the intent engine can now auto-dispatch
    # (execute route), so dispatched workers get the HIGH-tier cap unless
    # explicitly disabled via env/ConfigStore. Kill-switch still wins.
    "swarm_scope_enforce": True,
    "soul_requires_confirm": False,
    "false_clarify_budget": 0.15,
    "outbound_allowed_targets": [],  # empty = permissive (HITL handles); populate to enforce an allowlist
    "ttl": {  # seconds; None = no expiry (terminal)
        "draft": 3600.0,
        "needs_clarify": 86400.0,
        "needs_confirm": 86400.0,
        "ready": 900.0,
    },
    "retention": {"ephemeral_days": 30, "critical_days": 365},
    "pending_cap_per_thread": 20,
}

_TRUTHY = {"1", "true", "on", "yes"}
_FALSY = {"0", "false", "off", "no"}


def _env_bool(name: str) -> bool | None:
    v = (os.environ.get(name) or "").strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


def get_commitment_config() -> dict[str, Any]:
    """Resolve the full agent.commitment.* config (env → ConfigStore → defaults).

    Returns a fresh dict each call (live). Never raises — any read failure
    falls back to defaults so the gate keeps working.
    """
    cfg = {k: (v.copy() if isinstance(v, (dict, list)) else v)
           for k, v in _DEFAULTS.items()}

    # ── env overrides (kill-switch + mode are the live operator knobs) ──────
    env_enabled = _env_bool("KAZMA_COMMITMENT_ENABLED")
    if env_enabled is not None:
        cfg["enabled"] = env_enabled
    env_mode = (os.environ.get("KAZMA_COMMITMENT_MODE") or "").strip().lower()
    if env_mode in MODES:
        cfg["mode"] = env_mode
    env_sse = _env_bool("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE")
    if env_sse is not None:
        cfg["swarm_scope_enforce"] = env_sse
    soul_explicit = False
    env_src = _env_bool("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM")
    if env_src is not None:
        cfg["soul_requires_confirm"] = env_src
        soul_explicit = True

    # ── ConfigStore (Settings UI / programmatic) ───────────────────────────
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        v = cs.get("agent.commitment.enabled")
        if v is not None:
            cfg["enabled"] = bool(v)
        m = cs.get("agent.commitment.mode")
        if isinstance(m, str) and m.strip().lower() in MODES:
            cfg["mode"] = m.strip().lower()
        hc = cs.get("agent.commitment.high_confidence")
        if hc is not None:
            try:
                cfg["high_confidence"] = float(hc)
            except (TypeError, ValueError):
                pass
        eum = cs.get("agent.commitment.enforce_unknown_mutators")
        if eum is not None:
            cfg["enforce_unknown_mutators"] = bool(eum)
        sse = cs.get("agent.commitment.swarm_scope_enforce")
        if sse is not None:
            cfg["swarm_scope_enforce"] = bool(sse)
        src = cs.get("agent.commitment.soul_requires_confirm")
        if src is not None:
            cfg["soul_requires_confirm"] = bool(src)
            soul_explicit = True
        oat = cs.get("agent.commitment.outbound_allowed_targets")
        if oat is not None:
            if isinstance(oat, str):
                cfg["outbound_allowed_targets"] = [t.strip() for t in oat.split(",") if t.strip()]
            elif isinstance(oat, (list, tuple)):
                cfg["outbound_allowed_targets"] = list(oat)
        cap = cs.get("agent.commitment.pending_cap_per_thread")
        if cap is not None:
            try:
                cfg["pending_cap_per_thread"] = int(cap)
            except (TypeError, ValueError):
                pass
        for ttl_key in ("draft", "needs_clarify", "needs_confirm", "ready"):
            raw = cs.get(f"agent.commitment.ttl.{ttl_key}")
            if raw is not None:
                try:
                    cfg["ttl"][ttl_key] = float(raw)
                except (TypeError, ValueError):
                    pass
        emph = cs.get("agent.commitment.retention.ephemeral_days")
        crit = cs.get("agent.commitment.retention.critical_days")
        if emph is not None:
            try:
                cfg["retention"]["ephemeral_days"] = int(emph)
            except (TypeError, ValueError):
                pass
        if crit is not None:
            try:
                cfg["retention"]["critical_days"] = int(crit)
            except (TypeError, ValueError):
                pass
    except Exception:
        logger.debug("[commitment] get_commitment_config: ConfigStore read failed — defaults", exc_info=True)

    # Soul confirm: operator-set value wins. Otherwise ON in production /
    # multi-user so a Soul delta cannot silently rewrite the supervisor.
    if not soul_explicit and not cfg["soul_requires_confirm"]:
        try:
            from kazma_core.tenant_isolation import multi_user_or_production

            if multi_user_or_production():
                cfg["soul_requires_confirm"] = True
        except Exception:
            pass

    return cfg
