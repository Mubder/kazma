"""Memory scale backends — ConfigStore-driven vector/embedder profiles.

Default remains local SQLite + local/sqlite-vec. Remote providers are opt-in
via Settings → Memory backends. Factory re-reads ConfigStore live (mirrors
``get_proxy_provider`` / HITL) so saves take effect without restart for
config; embedder singleton still uses :func:`reset_embedder` on save.

Never break chat: remote failures fall back per ``failover.on_remote_error``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_BACKENDS_CFG",
    "get_backends_cfg",
    "save_backends_cfg",
    "mask_backends_cfg",
    "test_embedder_backend",
    "test_vector_backend",
    "reset_backends_to_local",
    "is_sensitive_backend_key",
]

DEFAULT_BACKENDS_CFG: dict[str, Any] = {
    "mode": "local",  # local | hybrid | remote
    "vector": {
        "provider": "sqlite_vec",  # sqlite_vec | pgvector | qdrant
        "url": "",
        "api_key": "",
        "collection": "kazma_memory",
        "dimension": 1024,
    },
    "embedder": {
        "provider": "local",  # local | openai_compat
        "model": "BAAI/bge-m3",
        "base_url": "",
        "api_key": "",
        "dim": 1024,
    },
    "graph": {
        "provider": "sqlite",
        "url": "",
    },
    "failover": {
        "on_remote_error": "local",  # local | empty | raise
    },
}

_SENSITIVE_SUFFIXES = ("api_key", "password", "token", "secret")


def is_sensitive_backend_key(key: str) -> bool:
    k = (key or "").lower()
    return any(s in k for s in _SENSITIVE_SUFFIXES)


def get_backends_cfg() -> dict[str, Any]:
    """Merged backends config: defaults ← ConfigStore ``memory.backends.*``."""
    out: dict[str, Any] = {
        "mode": DEFAULT_BACKENDS_CFG["mode"],
        "vector": dict(DEFAULT_BACKENDS_CFG["vector"]),
        "embedder": dict(DEFAULT_BACKENDS_CFG["embedder"]),
        "graph": dict(DEFAULT_BACKENDS_CFG["graph"]),
        "failover": dict(DEFAULT_BACKENDS_CFG["failover"]),
    }
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        mode = store.get("memory.backends.mode")
        if mode is not None:
            out["mode"] = str(mode).strip().lower() or "local"
        for section in ("vector", "embedder", "graph", "failover"):
            for k, default in DEFAULT_BACKENDS_CFG[section].items():
                val = store.get(f"memory.backends.{section}.{k}")
                if val is not None:
                    out[section][k] = val
                else:
                    out[section][k] = default
        # Align with embedding.* keys when backends embedder not set
        if not store.get("memory.backends.embedder.provider"):
            emb_p = store.get("embedding.provider")
            emb_m = store.get("embedding.model")
            emb_u = store.get("embedding.base_url")
            emb_k = store.get("embedding.api_key")
            emb_d = store.get("embedding.dim")
            if emb_p is not None:
                out["embedder"]["provider"] = emb_p
            if emb_m is not None:
                out["embedder"]["model"] = emb_m
            if emb_u is not None:
                out["embedder"]["base_url"] = emb_u
            if emb_k is not None:
                out["embedder"]["api_key"] = emb_k
            if emb_d is not None:
                out["embedder"]["dim"] = emb_d
    except Exception:
        logger.debug("[backends] ConfigStore read failed", exc_info=True)
    # Normalize mode
    if out["mode"] not in ("local", "hybrid", "remote"):
        out["mode"] = "local"
    return out


def mask_backends_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return config with secrets masked for API responses."""
    import copy

    c = copy.deepcopy(cfg if cfg is not None else get_backends_cfg())
    for section in ("vector", "embedder", "graph"):
        sec = c.get(section) or {}
        for k, v in list(sec.items()):
            if is_sensitive_backend_key(k) and v:
                sec[k] = "***"
        c[section] = sec
    return c


def save_backends_cfg(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist backends profile via batch_set. Never writes ``***`` as a secret."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    pairs: list[tuple[str, Any]] = []
    mode = str(payload.get("mode") or "local").strip().lower()
    if mode not in ("local", "hybrid", "remote"):
        mode = "local"
    pairs.append(("memory.backends.mode", mode))

    for section in ("vector", "embedder", "graph", "failover"):
        sec = payload.get(section) or {}
        if not isinstance(sec, dict):
            continue
        for k, v in sec.items():
            if is_sensitive_backend_key(str(k)) and (
                v is None or str(v).strip() in ("", "***")
            ):
                continue  # keep existing secret
            pairs.append((f"memory.backends.{section}.{k}", v))

    # Mirror embedder into embedding.* so get_embedder() sees the change
    emb = payload.get("embedder") or {}
    if isinstance(emb, dict):
        if emb.get("provider") is not None:
            pairs.append(("embedding.provider", emb["provider"]))
        if emb.get("model") is not None:
            pairs.append(("embedding.model", emb["model"]))
        if emb.get("base_url") is not None:
            pairs.append(("embedding.base_url", emb.get("base_url") or ""))
        if emb.get("dim") is not None:
            pairs.append(("embedding.dim", emb["dim"]))
        key = emb.get("api_key")
        if key is not None and str(key).strip() not in ("", "***"):
            pairs.append(("embedding.api_key", key))

    items = [(k, v, "memory") for k, v in pairs]
    if hasattr(store, "batch_set"):
        store.batch_set(items)
    else:
        for k, v, cat in items:
            store.set(k, v, category=cat)

    # Invalidate embedder singleton so next call rebuilds
    try:
        from kazma_core.memory.embedder import reset_embedder

        reset_embedder()
    except Exception:
        logger.debug("[backends] reset_embedder failed", exc_info=True)

    return mask_backends_cfg()


def reset_backends_to_local() -> dict[str, Any]:
    """Wipe remote backend keys → local defaults."""
    return save_backends_cfg(
        {
            "mode": "local",
            "vector": dict(DEFAULT_BACKENDS_CFG["vector"]),
            "embedder": dict(DEFAULT_BACKENDS_CFG["embedder"]),
            "graph": dict(DEFAULT_BACKENDS_CFG["graph"]),
            "failover": dict(DEFAULT_BACKENDS_CFG["failover"]),
        }
    )


def test_embedder_backend(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe embedder: encode a short string, return latency + dim."""
    t0 = time.perf_counter()
    try:
        from kazma_core.memory.embedder import get_embedder

        emb = get_embedder()
        if emb is None:
            return {"ok": False, "error": "embedder unavailable", "latency_ms": 0}
        vec = emb.encode("kazma memory backend probe")
        ms = (time.perf_counter() - t0) * 1000
        dim = len(vec) if vec else getattr(emb, "dim", 0)
        return {
            "ok": bool(vec),
            "latency_ms": round(ms, 1),
            "dim": dim,
            "provider": (cfg or get_backends_cfg()).get("embedder", {}).get("provider"),
        }
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return {"ok": False, "error": str(exc)[:300], "latency_ms": round(ms, 1)}


def test_vector_backend(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe vector path: open primary DB + VectorEngine availability."""
    t0 = time.perf_counter()
    c = cfg or get_backends_cfg()
    provider = (c.get("vector") or {}).get("provider") or "sqlite_vec"
    try:
        if provider in ("sqlite_vec", "local", "local_sqlite"):
            import sqlite3

            from kazma_core.memory.schema_v2 import ensure_primary_schema
            from kazma_core.memory.vector_engine import VectorEngine
            from kazma_core.paths import primary_memory_db

            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            try:
                ensure_primary_schema(conn)
                ve = VectorEngine(conn)
                ms = (time.perf_counter() - t0) * 1000
                return {
                    "ok": True,
                    "provider": provider,
                    "available": ve.available,
                    "sqlite_vec": ve.has_sqlite_vec,
                    "numpy": ve.has_numpy,
                    "latency_ms": round(ms, 1),
                }
            finally:
                conn.close()
        # Remote providers: connectivity check only (no hard dep)
        url = (c.get("vector") or {}).get("url") or ""
        if not url:
            return {
                "ok": False,
                "error": f"{provider} requires a connection URL",
                "latency_ms": 0,
            }
        # Lightweight TCP/HTTP reachability without importing heavy clients
        import httpx

        with httpx.Client(timeout=5.0) as client:
            # Qdrant / generic health
            r = client.get(url.rstrip("/") + "/collections")
            ms = (time.perf_counter() - t0) * 1000
            return {
                "ok": r.status_code < 500,
                "provider": provider,
                "status_code": r.status_code,
                "latency_ms": round(ms, 1),
            }
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        failover = (c.get("failover") or {}).get("on_remote_error", "local")
        return {
            "ok": False,
            "error": str(exc)[:300],
            "latency_ms": round(ms, 1),
            "failover": failover,
        }
