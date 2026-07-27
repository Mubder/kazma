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
    "read_memory_cfg",
    "set_memory_flag",
]

logger = logging.getLogger(__name__)

DEFAULT_MEMORY_CFG: dict[str, Any] = {
    "enabled": True,
    "per_turn_retrieval": True,
    "auto_store": True,
    "auto_store_mode": "both",
    "retrieval_top_k": 5,
    "max_context_tokens": 128_000,
    "provenance": True,
    "consolidation": {
        "enabled": True,
        "use_llm": True,
        "min_user_chars": 24,
        "every_n_turns": 1,
        "skip_adapter_if_auto_stored": True,
        "skip_llm_in_demo": True,
    },
}

# ConfigStore keys ↔ nested yaml fields
_STORE_KEYS = (
    "enabled",
    "per_turn_retrieval",
    "auto_store",
    "auto_store_mode",
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
    return out


def read_memory_cfg() -> dict[str, Any]:
    """Merged memory config: defaults ← yaml ← ConfigStore."""
    merged = dict(DEFAULT_MEMORY_CFG)
    merged.update(_read_yaml_memory())
    # embedding block may come from yaml only
    store_overlay = _read_store_overlay()
    merged.update(store_overlay)
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


def memory_retrieval_top_k(cfg: dict[str, Any] | None = None) -> int:
    c = cfg if cfg is not None else read_memory_cfg()
    try:
        return max(1, int(c.get("retrieval_top_k", 5)))
    except (TypeError, ValueError):
        return 5


def set_memory_flag(key: str, value: Any, *, category: str = "memory") -> None:
    """Persist a memory.* flag to ConfigStore (used by TUI/settings)."""
    if key.startswith("memory."):
        store_key = key
    else:
        store_key = f"memory.{key}"
    from kazma_core.config_store import get_config_store

    get_config_store().set(store_key, value, category=category)
