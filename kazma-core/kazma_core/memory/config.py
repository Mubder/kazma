"""Memory configuration — single source of truth.

Precedence (highest wins):
  1. ConfigStore keys (``memory.*``) — TUI / Settings / runtime toggles
  2. ``kazma.yaml`` ``memory:`` block — file defaults
  3. Hard-coded defaults

All chat-memory gates (enabled, per-turn RAG, auto-store, top-k) MUST read
through :func:`read_memory_cfg` so the TUI ``memory.enabled`` toggle is real.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_MEMORY_CFG",
    "memory_auto_store_enabled",
    "memory_auto_store_mode",
    "memory_enabled",
    "memory_per_turn_enabled",
    "memory_retrieval_top_k",
    "memory_v2_enabled",
    "read_memory_cfg",
    "set_memory_flag",
]

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_CFG: dict[str, Any] = {
    "enabled": True,
    "per_turn_retrieval": True,
    "auto_store": True,
    "auto_store_mode": "both",
    "tenant_mode": "shared",
    "retrieval_top_k": 5,
    "max_context_tokens": 128_000,
    "provenance": True,
    "consolidation": {
        "enabled": True,
        "use_llm": True,
        "min_user_chars": 24,
        "every_n_turns": 1,
        "skip_adapter_if_auto_stored": True,
        "skip_llm_if_auto_stored": True,
        "skip_llm_in_demo": True,
    },
    # ── Memory V2 cognitive engine (bi-temporal beliefs + tiers + PPR) ──
    # All keys flow through ConfigStore so the TUI/Settings panel can
    # surface and toggle them (AGENTS.md §8 single-source-of-truth rule).
    # use_new_stack=True: V2 is the active stack. Flip to False only disables
    # V2 injection/post-turn (V1 RRF stack was removed — no legacy rollback).
    "v2": {
        "use_new_stack": True,
        # Phase A: bump access_count on successful recall hits
        "access_bump_enabled": True,
        # Phase A: score boost for same-session episodes (RRF units)
        "session_boost": 0.35,
        # Source trust weights (W_trust in V_retention formula §4.1)
        "trust_weight_user": 1.0,
        "trust_weight_tool": 0.85,
        "trust_weight_llm": 0.60,
        # Retention-score blend weights (ω1, ω2 in §4.1)
        "retention_importance_weight": 0.60,
        "retention_access_weight": 0.40,
        # Decay constants (λ_type in §4.1) per derived memory_class.
        # memory_class is DERIVED (resolution #4): no schema column.
        "decay_lambda_identity": 0.0001,   # functional + importance≥4
        "decay_lambda_general": 0.01,      # default
        "decay_lambda_ephemeral": 0.10,    # importance≤2
        # memory_class derivation thresholds (resolution #4)
        "identity_min_importance": 4,
        "ephemeral_max_importance": 2,
        # Tier TTLs (days)
        "recall_ttl_days": 90,
        "episodic_ttl_days": 30,
        "recall_demote_idle_days": 30,     # no access → demote recall→episodic
        "archive_after_days": 180,         # superseded belief → beliefs_archive
        # Tier promotion/demotion rules
        "promote_to_recall_min_importance": 3,
        "promote_to_recall_min_access": 2,
        # Procedural DAG confidence (Laplace §4.2)
        "procedural_quarantine_threshold": 0.40,
        "procedural_quarantine_min_trials": 3,
        # Local Ego-Graph PPR (§5.1)
        "ppr_alpha": 0.15,
        "ppr_max_iter": 15,
        "ppr_max_nodes": 200,
        "ppr_seed_k": 10,
        # LLM extraction cost-gate. The micro_consolidation queue task runs
        # the LLM belief extractor; these knobs throttle it so it doesn't
        # fire on every single turn (one extra LLM call per turn is costly
        # at scale). extraction_every_n_turns=1 = every turn; 3 = every 3rd.
        # skip_llm_if_heuristic_extracted=True skips the LLM pass when the
        # sync heuristic extractor already found ≥1 belief this turn.
        "extraction_every_n_turns": 1,
        "skip_llm_if_heuristic_extracted": False,
        # Entity resolution (§5.2 micro-consolidation)
        "entity_vector_merge_threshold": 0.12,
    },
}

# ConfigStore keys ↔ nested yaml fields
_STORE_KEYS = (
    "enabled",
    "per_turn_retrieval",
    "auto_store",
    "auto_store_mode",
    "tenant_mode",
    "retrieval_top_k",
    "max_context_tokens",
    "provenance",
)


def _read_yaml_memory() -> dict[str, Any]:
    try:
        import yaml

        path = Path("kazma.yaml")
        if path.exists():
            with open(path, encoding="utf-8") as f:
                full = yaml.safe_load(f) or {}
            block = full.get("memory") or {}
            if isinstance(block, dict):
                return dict(block)
    except Exception:
        logger.debug("[memory.config] yaml read failed", exc_info=True)
    return {}


def _read_store_overlay() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        for key in _STORE_KEYS:
            val = store.get(f"memory.{key}")
            if val is not None:
                out[key] = val
        # Nested consolidation flags (TUI Settings Phase 3)
        cons: dict[str, Any] = {}
        for sub in (
            "enabled",
            "use_llm",
            "min_user_chars",
            "every_n_turns",
            "skip_adapter_if_auto_stored",
            "skip_llm_if_auto_stored",
            "skip_llm_in_demo",
        ):
            val = store.get(f"memory.consolidation.{sub}")
            if val is not None:
                cons[sub] = val
        if cons:
            out["consolidation"] = cons
        # Nested v2 flags — every tunable is ConfigStore-readable so the
        # TUI/Settings panel can surface them (AGENTS.md §8). The critical
        # one is `use_new_stack` (the dual-write → V2 recall rollback flag).
        v2: dict[str, Any] = {}
        for sub in (
            "use_new_stack",
            "trust_weight_user",
            "trust_weight_tool",
            "trust_weight_llm",
            "retention_importance_weight",
            "retention_access_weight",
            "decay_lambda_identity",
            "decay_lambda_general",
            "decay_lambda_ephemeral",
            "identity_min_importance",
            "ephemeral_max_importance",
            "recall_ttl_days",
            "episodic_ttl_days",
            "recall_demote_idle_days",
            "archive_after_days",
            "promote_to_recall_min_importance",
            "promote_to_recall_min_access",
            "procedural_quarantine_threshold",
            "procedural_quarantine_min_trials",
            "ppr_alpha",
            "ppr_max_iter",
            "ppr_max_nodes",
            "ppr_seed_k",
            "extraction_every_n_turns",
            "skip_llm_if_heuristic_extracted",
            "entity_vector_merge_threshold",
        ):
            val = store.get(f"memory.v2.{sub}")
            if val is not None:
                v2[sub] = val
        if v2:
            out["v2"] = v2
    except Exception:
        logger.debug("[memory.config] ConfigStore overlay failed", exc_info=True)
    return out


def _coerce(cfg: dict[str, Any]) -> dict[str, Any]:
    """Normalize types for known keys."""
    out = dict(cfg)
    for bool_key in ("enabled", "per_turn_retrieval", "auto_store", "provenance"):
        if bool_key in out:
            v = out[bool_key]
            if isinstance(v, str):
                out[bool_key] = v.strip().lower() in ("1", "true", "yes", "on")
            else:
                out[bool_key] = bool(v)
    cons = out.get("consolidation")
    if isinstance(cons, dict):
        c2 = dict(cons)
        for bk in (
            "enabled",
            "use_llm",
            "skip_adapter_if_auto_stored",
            "skip_llm_in_demo",
        ):
            if bk in c2:
                v = c2[bk]
                if isinstance(v, str):
                    c2[bk] = v.strip().lower() in ("1", "true", "yes", "on")
                else:
                    c2[bk] = bool(v)
        out["consolidation"] = c2
    if "retrieval_top_k" in out:
        try:
            out["retrieval_top_k"] = max(1, int(out["retrieval_top_k"]))
        except (TypeError, ValueError):
            out["retrieval_top_k"] = DEFAULT_MEMORY_CFG["retrieval_top_k"]
    if "max_context_tokens" in out:
        try:
            out["max_context_tokens"] = max(1024, int(out["max_context_tokens"]))
        except (TypeError, ValueError):
            out["max_context_tokens"] = DEFAULT_MEMORY_CFG["max_context_tokens"]
    mode = str(out.get("auto_store_mode", "both") or "both").strip().lower()
    if mode not in ("durable", "turns", "both"):
        mode = "both"
    out["auto_store_mode"] = mode
    # Tenant isolation mode: shared (default) | per_platform | per_user
    tmode = str(out.get("tenant_mode", "shared") or "shared").strip().lower()
    if tmode not in ("shared", "per_platform", "per_user"):
        tmode = "shared"
    out["tenant_mode"] = tmode
    _coerce_v2(out)
    return out


# ── V2 tunable coercion ───────────────────────────────────────────────────

# Booleans (use_new_stack is the dual-write → V2 recall rollback flag)
_V2_BOOL_KEYS = ("use_new_stack", "skip_llm_if_heuristic_extracted")
# Floats (trust weights, retention blend, decay λ, thresholds)
_V2_FLOAT_KEYS = (
    "trust_weight_user",
    "trust_weight_tool",
    "trust_weight_llm",
    "retention_importance_weight",
    "retention_access_weight",
    "decay_lambda_identity",
    "decay_lambda_general",
    "decay_lambda_ephemeral",
    "procedural_quarantine_threshold",
    "entity_vector_merge_threshold",
    "ppr_alpha",
)
# Integers (TTLs, days, counts, iteration caps)
_V2_INT_KEYS = (
    "identity_min_importance",
    "ephemeral_max_importance",
    "recall_ttl_days",
    "episodic_ttl_days",
    "recall_demote_idle_days",
    "archive_after_days",
    "promote_to_recall_min_importance",
    "promote_to_recall_min_access",
    "procedural_quarantine_min_trials",
    "ppr_max_iter",
    "ppr_max_nodes",
    "ppr_seed_k",
    "extraction_every_n_turns",
)


def _to_bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def _to_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _coerce_v2(out: dict[str, Any]) -> None:
    """Type-normalize the nested ``v2`` config block in place."""
    v2 = out.get("v2")
    if not isinstance(v2, dict):
        return
    c2 = dict(v2)
    defaults = DEFAULT_MEMORY_CFG.get("v2") or {}
    for bk in _V2_BOOL_KEYS:
        if bk in c2:
            c2[bk] = _to_bool(c2[bk])
    for fk in _V2_FLOAT_KEYS:
        if fk in c2:
            c2[fk] = _to_float(c2[fk], _to_float(defaults.get(fk), 0.0))
    for ik in _V2_INT_KEYS:
        if ik in c2:
            c2[ik] = _to_int(c2[ik], _to_int(defaults.get(ik), 0))
    out["v2"] = c2


def read_memory_cfg() -> dict[str, Any]:
    """Merged memory config: defaults ← yaml ← ConfigStore."""
    merged = dict(DEFAULT_MEMORY_CFG)
    # Deep-copy nested consolidation defaults
    merged["consolidation"] = dict(DEFAULT_MEMORY_CFG.get("consolidation") or {})
    # Deep-copy nested v2 defaults
    merged["v2"] = dict(DEFAULT_MEMORY_CFG.get("v2") or {})
    yaml_block = _read_yaml_memory()
    yaml_cons = yaml_block.pop("consolidation", None) if isinstance(yaml_block, dict) else None
    yaml_v2 = yaml_block.pop("v2", None) if isinstance(yaml_block, dict) else None
    merged.update(yaml_block)
    if isinstance(yaml_cons, dict):
        base = dict(merged.get("consolidation") or {})
        base.update(yaml_cons)
        merged["consolidation"] = base
    if isinstance(yaml_v2, dict):
        base = dict(merged.get("v2") or {})
        base.update(yaml_v2)
        merged["v2"] = base
    store_overlay = _read_store_overlay()
    store_cons = store_overlay.pop("consolidation", None) if isinstance(store_overlay, dict) else None
    store_v2 = store_overlay.pop("v2", None) if isinstance(store_overlay, dict) else None
    merged.update(store_overlay)
    if isinstance(store_cons, dict):
        base = dict(merged.get("consolidation") or {})
        base.update(store_cons)
        merged["consolidation"] = base
    if isinstance(store_v2, dict):
        base = dict(merged.get("v2") or {})
        base.update(store_v2)
        merged["v2"] = base
    return _coerce(merged)


def memory_enabled(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg if cfg is not None else read_memory_cfg()
    return bool(c.get("enabled", True))


def memory_per_turn_enabled(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg if cfg is not None else read_memory_cfg()
    if not memory_enabled(c):
        return False
    return bool(c.get("per_turn_retrieval", True))


def memory_auto_store_enabled(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg if cfg is not None else read_memory_cfg()
    if not memory_enabled(c):
        return False
    return bool(c.get("auto_store", True))


def memory_auto_store_mode(cfg: dict[str, Any] | None = None) -> str:
    c = cfg if cfg is not None else read_memory_cfg()
    mode = str(c.get("auto_store_mode", "both") or "both").strip().lower()
    if mode not in ("durable", "turns", "both"):
        return "both"
    return mode


def memory_tenant_mode(cfg: dict[str, Any] | None = None) -> str:
    """Return the active tenant isolation mode.

    - ``"shared"`` (default): all platforms + users share one memory pool.
    - ``"per_platform"``: each platform isolates (Telegram ≠ Web ≠ Discord).
    - ``"per_user"``: each sender/session gets fully isolated memory.
    """
    c = cfg if cfg is not None else read_memory_cfg()
    mode = str(c.get("tenant_mode", "shared") or "shared").strip().lower()
    if mode not in ("shared", "per_platform", "per_user"):
        return "shared"
    return mode


def resolve_tenant_id(
    platform: str, sender_id: str = "", session_id: str = ""
) -> str:
    """Resolve the tenant_id for the current request based on the active mode.

    Called by the 3 entry points (gateway store.py, SSE, WS) to centralize
    the mode → tenant_id mapping. The result flows into every V2 memory
    read/write as the SQL ``WHERE tenant_id = ?`` filter.

    Args:
        platform: Platform name (``"telegram"``, ``"discord"``, ``"web"`` …).
        sender_id: Platform-prefixed sender identity (``"telegram:12345"``).
            Empty for the Web path (which has no sender identity).
        session_id: The Web/browser session ID (empty for gateway paths).
    """
    mode = memory_tenant_mode()
    if mode == "shared":
        return "default"
    if mode == "per_platform":
        return platform or "default"
    # per_user: each sender/session gets their own isolated memory
    return sender_id or (f"{platform}:{session_id}" if session_id else "default")


def memory_retrieval_top_k(cfg: dict[str, Any] | None = None) -> int:
    c = cfg if cfg is not None else read_memory_cfg()
    try:
        return max(1, int(c.get("retrieval_top_k", 5)))
    except (TypeError, ValueError):
        return 5


def memory_v2_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """True when the V2 cognitive memory stack is the active read path.

    Requires both ``memory.enabled`` (master switch) AND
    ``memory.v2.use_new_stack`` (the dual-write → V2 recall cutover flag).
    During the transition ``use_new_stack`` stays False so the legacy
    4-layer RRF adapter serves reads; the V2 schema still receives
    dual-writes so no data is lost.
    """
    c = cfg if cfg is not None else read_memory_cfg()
    if not memory_enabled(c):
        return False
    v2 = c.get("v2") or {}
    return bool(v2.get("use_new_stack", False))


def set_memory_flag(key: str, value: Any, *, category: str = "memory") -> None:
    """Persist a memory.* flag to ConfigStore (used by TUI/settings)."""
    if key.startswith("memory."):
        store_key = key
    else:
        store_key = f"memory.{key}"
    from kazma_core.config_store import get_config_store

    get_config_store().set(store_key, value, category=category)
