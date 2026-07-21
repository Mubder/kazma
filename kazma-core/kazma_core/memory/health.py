"""Live memory-subsystem health for the Dashboard Memory & Governance panel.

Each component is reported as ok / warn / error / off with a short human
reason (e.g. missing API key, package not installed) so operators can see
what is real and working without reading server logs.
"""

from __future__ import annotations

import logging
import os
from typing import Any

__all__ = ["build_memory_health"]

logger = logging.getLogger(__name__)


def _read_memory_cfg() -> dict[str, Any]:
    try:
        from pathlib import Path

        import yaml

        path = Path("kazma.yaml")
        if path.exists():
            with open(path, encoding="utf-8") as f:
                full = yaml.safe_load(f) or {}
            return dict((full.get("memory") or {}))
    except Exception:
        logger.debug("[memory.health] config read failed", exc_info=True)
    return {}


def _comp(
    id_: str,
    name: str,
    *,
    ok: bool,
    status: str,
    detail: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "ok": ok,
        "status": status,  # ok | warn | error | off
        "detail": detail,
        "meta": meta or {},
    }


def build_memory_health() -> dict[str, Any]:
    """Return overall status + per-component health rows."""
    cfg = _read_memory_cfg()
    components: list[dict[str, Any]] = []
    demo = os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes")

    # ── Config flags ──────────────────────────────────────────────────
    mem_enabled = bool(cfg.get("enabled", True)) and not demo
    per_turn = bool(cfg.get("per_turn_retrieval", True)) and mem_enabled
    auto_store = bool(cfg.get("auto_store", True)) and mem_enabled
    auto_mode = str(cfg.get("auto_store_mode", "both") or "both")

    if demo:
        components.append(_comp(
            "memory_enabled", "Memory system",
            ok=False, status="off",
            detail="Demo mode — RAG memory is disabled for this deployment.",
        ))
    elif mem_enabled:
        components.append(_comp(
            "memory_enabled", "Memory system",
            ok=True, status="ok",
            detail="memory.enabled=true in config.",
        ))
    else:
        components.append(_comp(
            "memory_enabled", "Memory system",
            ok=False, status="off",
            detail="memory.enabled=false in kazma.yaml — turn it on to use RAG.",
        ))

    components.append(_comp(
        "per_turn_retrieval", "Per-turn RAG",
        ok=per_turn,
        status="ok" if per_turn else "off",
        detail=(
            "Injects relevant memories on every user turn."
            if per_turn
            else "Disabled (memory.per_turn_retrieval=false or memory off)."
        ),
    ))
    components.append(_comp(
        "auto_store", "Auto-store",
        ok=auto_store,
        status="ok" if auto_store else "off",
        detail=(
            f"Writes durable facts / turn snapshots after each reply (mode={auto_mode})."
            if auto_store
            else "Disabled (memory.auto_store=false) — only memory_store tool / compaction write."
        ),
        meta={"mode": auto_mode},
    ))

    # ── Embedder ──────────────────────────────────────────────────────
    emb_cfg = (cfg.get("embedding") or {}) if isinstance(cfg.get("embedding"), dict) else {}
    provider = str(
        os.environ.get("KAZMA_EMBED_PROVIDER", "") or emb_cfg.get("provider", "local")
    ).strip().lower()
    model = str(os.environ.get("KAZMA_EMBED_MODEL", "") or emb_cfg.get("model", "all-MiniLM-L6-v2"))
    api_key_env = str(emb_cfg.get("api_key_env") or "KAZMA_EMBED_API_KEY")
    has_key = bool(
        os.environ.get(api_key_env)
        or os.environ.get("KAZMA_EMBED_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NGC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    base_url = str(os.environ.get("KAZMA_EMBED_BASE_URL", "") or emb_cfg.get("base_url", "")).strip()

    emb_ok = False
    emb_status = "error"
    emb_detail = "Embedder not initialized."
    emb_meta: dict[str, Any] = {"provider": provider, "model": model, "dim": None}
    try:
        from kazma_core.swarm.memory.embedder import get_embedder

        emb = get_embedder()
        if emb is None:
            emb_detail = "get_embedder() returned None — check logs."
        else:
            sample = emb.encode("health-check")
            dim = getattr(emb, "dim", len(sample) if sample else 0)
            emb_meta["dim"] = dim
            emb_meta["class"] = type(emb).__name__
            nonzero = bool(sample) and any(abs(float(x)) > 1e-12 for x in sample[:8])
            if sample and nonzero:
                emb_ok = True
                emb_status = "ok"
                emb_detail = f"{type(emb).__name__} ready (model={model}, dim={dim})."
            elif sample and not nonzero:
                emb_status = "warn"
                emb_detail = (
                    "Embedder returns zero vectors — recall quality is degraded. "
                    "Remote endpoint may be failing; check API key / network."
                )
            else:
                emb_status = "error"
                emb_detail = "Embedder encode() returned empty vector."
    except Exception as exc:
        emb_detail = f"Embedder failed: {exc}"

    if provider in ("openai-compatible", "openai", "nim", "remote") and not has_key:
        # Remote configured but no key — may still be OK if local fallback worked.
        if emb_ok and emb_meta.get("class") == "LocalSentenceTransformerEmbedder":
            emb_status = "warn"
            emb_detail = (
                f"Remote provider '{provider}' has no API key "
                f"(set {api_key_env} or NVIDIA_API_KEY). "
                f"Falling back to local MiniLM — working, but not using {model}."
            )
        elif not emb_ok:
            emb_status = "error"
            emb_detail = (
                f"Remote embeddings need {api_key_env} (or NVIDIA_API_KEY). "
                f"base_url={base_url or '(empty)'}."
            )
    if provider in ("openai-compatible", "openai", "nim", "remote") and not base_url and not emb_ok:
        emb_detail = (
            f"Remote provider '{provider}' has empty base_url and no working fallback. "
            "Set memory.embedding.base_url or switch provider to local."
        )

    components.append(_comp(
        "embedder", "Embedder",
        ok=emb_ok, status=emb_status, detail=emb_detail, meta=emb_meta,
    ))

    # ── VectorMemory singleton (tools + compaction fallback) ──────────
    vm_ok = False
    vm_status = "error"
    vm_detail = "VectorMemory not initialized (app boot failed or DEMO mode)."
    vm_meta: dict[str, Any] = {"count": 0, "degraded": False, "path": ""}
    try:
        from kazma_core.agent.tool_registry import get_vector_memory

        vm = get_vector_memory()
        if vm is None:
            vm_detail = (
                "VectorMemory singleton is None. "
                "Install the rag extra: pip install -e '.[rag]' then restart."
            )
        else:
            degraded = bool(getattr(vm, "degraded", False))
            vm_meta["degraded"] = degraded
            vm_meta["path"] = str(getattr(vm, "_path", "") or "")
            try:
                count = vm.count
                if callable(count):
                    count = count()
                vm_meta["count"] = int(count or 0)
            except Exception:
                vm_meta["count"] = 0
            if degraded:
                vm_ok = True  # still usable via FTS5 fallback
                vm_status = "warn"
                vm_detail = (
                    f"VectorMemory degraded to FTS5 keyword fallback "
                    f"({vm_meta['count']} docs). Install chromadb + sentence-transformers."
                )
            else:
                vm_ok = True
                vm_status = "ok"
                vm_detail = (
                    f"VectorMemory active ({vm_meta['count']} vectors"
                    f"{', path=' + vm_meta['path'] if vm_meta['path'] else ''})."
                )
    except Exception as exc:
        vm_detail = f"VectorMemory probe failed: {exc}"

    components.append(_comp(
        "vector_memory", "VectorMemory (tools)",
        ok=vm_ok, status=vm_status, detail=vm_detail, meta=vm_meta,
    ))

    # ── 4-layer adapter ───────────────────────────────────────────────
    layers = {"chromadb": False, "graph": False, "fts5": False, "sqlite_vec": False}
    try:
        from kazma_core.swarm.memory.adapter import get_adapter

        adapter = get_adapter()
        if adapter is not None and hasattr(adapter, "health"):
            layers.update(adapter.health() or {})
    except Exception as exc:
        logger.debug("[memory.health] adapter health failed: %s", exc)

    layer_specs = [
        (
            "layer_l1",
            "L1 ChromaDB",
            layers.get("chromadb"),
            "Semantic vector index (shared agent_memory collection).",
            "ChromaDB layer unavailable — pip install chromadb (rag extra).",
        ),
        (
            "layer_l2",
            "L2 Knowledge graph",
            layers.get("graph"),
            "Structural / tag relations for swarm + self-improvement.",
            "Knowledge graph unavailable (NetworkX init failed).",
        ),
        (
            "layer_l3",
            "L3 FTS5 lexical",
            layers.get("fts5"),
            "Keyword / BM25 search (works offline, no embeddings).",
            "FTS5 lexical store failed to initialize.",
        ),
        (
            "layer_l4",
            "L4 sqlite-vec",
            layers.get("sqlite_vec"),
            "Local vector tables via sqlite-vec.",
            "sqlite-vec not loaded — pip install sqlite-vec (included in .[rag]).",
        ),
    ]
    for id_, name, up, ok_msg, bad_msg in layer_specs:
        components.append(_comp(
            id_, name,
            ok=bool(up),
            status="ok" if up else "error",
            detail=ok_msg if up else bad_msg,
        ))

    # ── Package presence (actionable install hints) ───────────────────
    def _has_mod(name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            return False

    pkgs = [
        ("pkg_chromadb", "Package: chromadb", "chromadb", "Required for VectorMemory / L1."),
        ("pkg_st", "Package: sentence-transformers", "sentence_transformers", "Required for local MiniLM embeddings."),
        ("pkg_sqlite_vec", "Package: sqlite-vec", "sqlite_vec", "Required for L4 local vectors."),
        (
            "pkg_psycopg",
            "Package: psycopg",
            "psycopg",
            "Required for Postgres multi-replica stores. Fix: pip install -e '.[postgres]'",
        ),
        (
            "pkg_lg_pg",
            "Package: langgraph-checkpoint-postgres",
            "langgraph.checkpoint.postgres",
            "Required for Postgres graph checkpoints. Fix: pip install -e '.[postgres]'",
        ),
    ]
    for id_, name, mod, why in pkgs:
        present = _has_mod(mod)
        # Postgres packages are optional unless backend is postgres
        if id_ in ("pkg_psycopg", "pkg_lg_pg"):
            try:
                from kazma_core.db.backend import is_postgres

                want_pg = is_postgres()
            except Exception:
                want_pg = False
            if not want_pg and not present:
                components.append(_comp(
                    id_, name,
                    ok=True, status="off",
                    detail=f"{mod} not installed (optional until KAZMA_DATABASE_URL is set).",
                ))
                continue
            if want_pg and not present:
                components.append(_comp(
                    id_, name,
                    ok=False, status="error",
                    detail=f"{mod} missing while Postgres is configured — {why}",
                ))
                continue
        components.append(_comp(
            id_, name,
            ok=present,
            status="ok" if present else "error",
            detail=(
                f"{mod} is installed."
                if present
                else f"{mod} not installed — {why} Fix: pip install -e '.[rag]'"
            ),
        ))

    # ── Persistence backends (ConfigStore / swarm / checkpoints) ──────
    backend_meta: dict[str, str] = {
        "config": "sqlite",
        "swarm_tasks": "sqlite",
        "checkpoints": "sqlite",
    }
    try:
        from kazma_core.db.backend import get_database_url, is_postgres

        if is_postgres():
            backend_meta["config"] = "postgres"
            backend_meta["swarm_tasks"] = "postgres"
            dsn = get_database_url() or ""
            # Redact password for UI
            safe_dsn = dsn
            if "@" in dsn and "://" in dsn:
                try:
                    scheme, rest = dsn.split("://", 1)
                    if "@" in rest:
                        creds, hostpart = rest.rsplit("@", 1)
                        user = creds.split(":")[0] if creds else "user"
                        safe_dsn = f"{scheme}://{user}:***@{hostpart}"
                except Exception:
                    safe_dsn = "postgresql://***"
            components.append(_comp(
                "store_config",
                "ConfigStore",
                ok=True,
                status="ok",
                detail=f"Postgres backend active ({safe_dsn}). Settings / sessions / swarm tasks share this DB.",
                meta={"backend": "postgres"},
            ))
            # Probe connectivity
            try:
                from kazma_core.db.postgres_pool import get_postgres_pool

                pool = get_postgres_pool()
                if pool is None:
                    components[-1] = _comp(
                        "store_config", "ConfigStore",
                        ok=False, status="error",
                        detail="KAZMA_DATABASE_URL set but Postgres pool is unavailable.",
                        meta={"backend": "postgres"},
                    )
                else:
                    pool.execute_one("SELECT 1 AS ok")
            except Exception as exc:
                components[-1] = _comp(
                    "store_config", "ConfigStore",
                    ok=False, status="error",
                    detail=f"Postgres pool probe failed: {exc}",
                    meta={"backend": "postgres"},
                )
        else:
            components.append(_comp(
                "store_config",
                "ConfigStore",
                ok=True,
                status="ok",
                detail="SQLite backend (kazma-data/settings.db). Set KAZMA_DATABASE_URL for multi-replica Postgres.",
                meta={"backend": "sqlite"},
            ))
    except Exception as exc:
        components.append(_comp(
            "store_config", "ConfigStore",
            ok=False, status="warn",
            detail=f"Could not resolve DB backend: {exc}",
        ))

    # Checkpointer backend is best-effort (module may not expose status)
    try:
        from kazma_core.db.backend import is_postgres

        if is_postgres() and _has_mod("langgraph.checkpoint.postgres"):
            backend_meta["checkpoints"] = "postgres"
            components.append(_comp(
                "store_checkpoints",
                "LangGraph checkpoints",
                ok=True,
                status="ok",
                detail="Postgres checkpointer available (AsyncPostgresSaver). HITL pause/resume can share state across replicas.",
                meta={"backend": "postgres"},
            ))
        elif is_postgres():
            backend_meta["checkpoints"] = "sqlite_fallback"
            components.append(_comp(
                "store_checkpoints",
                "LangGraph checkpoints",
                ok=False,
                status="warn",
                detail=(
                    "Postgres URL is set but checkpoint-postgres package or setup failed — "
                    "graph state may use SQLite fallback. pip install -e '.[postgres]' and restart."
                ),
                meta={"backend": "sqlite_fallback"},
            ))
        else:
            components.append(_comp(
                "store_checkpoints",
                "LangGraph checkpoints",
                ok=True,
                status="ok",
                detail="SQLite checkpointer (default single-node).",
                meta={"backend": "sqlite"},
            ))
    except Exception as exc:
        components.append(_comp(
            "store_checkpoints", "LangGraph checkpoints",
            ok=False, status="warn",
            detail=f"Checkpoint probe failed: {exc}",
        ))

    # ── Overall rollup ────────────────────────────────────────────────
    # Core path: embedder + VectorMemory + at least one of L1/L3 for recall.
    critical_ids = {"embedder", "vector_memory", "store_config"}
    core_errors = [
        c for c in components
        if c["id"] in critical_ids and c["status"] == "error"
    ]
    has_search_layer = any(
        c["id"] in ("layer_l1", "layer_l3") and c["ok"] for c in components
    )
    if demo:
        overall = "DEMO"
    elif core_errors or (mem_enabled and not has_search_layer):
        overall = "DEGRADED"
    else:
        overall = "ACTIVE"

    try:
        from kazma_core.config_store import get_config_store

        if get_config_store().get("system.memory.status", "") == "INSTALLING":
            overall = "INSTALLING"
    except Exception:
        pass

    issues = [
        c["detail"]
        for c in components
        if c["status"] in ("error", "warn")
    ]

    ok_n = sum(1 for c in components if c["status"] == "ok")
    cfg_backend = backend_meta.get("config", "sqlite")
    ckpt_backend = backend_meta.get("checkpoints", "sqlite")
    vector_bit = (
        "ChromaDB vector store + embeddings operational"
        if any(c["id"] == "vector_memory" and c["status"] == "ok" for c in components)
        else "vector memory degraded or offline"
    )
    headline = (
        f"Persistence: {cfg_backend} (config/sessions/swarm); "
        f"checkpoints: {ckpt_backend}. {vector_bit}."
    )
    return {
        "status": overall,
        "components": components,
        "issues": issues[:12],
        "summary": f"{ok_n}/{len(components)} components healthy",
        "headline": headline,
        "backend": backend_meta,
    }
