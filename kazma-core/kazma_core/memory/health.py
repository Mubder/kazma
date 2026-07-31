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
        from kazma_core.memory.config import read_memory_cfg

        return read_memory_cfg()
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
            detail="memory.enabled=true (ConfigStore ← kazma.yaml).",
        ))
    else:
        components.append(_comp(
            "memory_enabled", "Memory system",
            ok=False, status="off",
            detail="memory.enabled=false — set via TUI/Settings or kazma.yaml to use RAG.",
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
        from kazma_core.memory.embedder import get_embedder

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
                emb_ok = False
                emb_status = "error"
                emb_detail = (
                    "Embedder returns zero vectors — vector recall is broken. "
                    "Remote endpoint may be failing; check API key / network, "
                    "or switch memory.embedding.provider to local."
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

    # ── V2 cognitive-engine store counts ───────────────────────────────
    # Replaces the legacy VectorMemory + 4-layer adapter probes. The V2
    # health snapshot (build_v2_health) already reads memory_state.db /
    # memory_ops.db; synthesize the legacy component-envelope from its counts
    # so the 4 UI/TUI callers + their renderers keep working unchanged.
    v2 = {"status": "OFF", "db_available": False,
          "beliefs": {"active": 0, "superseded": 0, "archived": 0},
          "episodes": {"working": 0, "episodic": 0, "recall": 0, "archived": 0},
          "entities": 0, "procedural_dags": {"active": 0, "quarantine": 0},
          "queue": {"pending": 0, "processing": 0, "failed": 0}}
    try:
        from kazma_core.memory.v2_health import build_v2_health

        v2 = build_v2_health()
    except Exception as exc:
        logger.debug("[memory.health] V2 health probe failed: %s", exc)

    bel = v2.get("beliefs") or {}
    ep = v2.get("episodes") or {}
    v2_ok = bool(v2.get("db_available"))
    v2_status = "ok" if v2_ok else "error"
    components.append(_comp(
        "vector_memory", "V2 cognitive engine",
        ok=v2_ok, status=v2_status,
        detail=(
            f"V2 stack active — {bel.get('active', 0)} active beliefs, "
            f"{(ep.get('recall', 0) + ep.get('episodic', 0))} episodes, "
            f"{v2.get('entities', 0)} entities."
            if v2_ok
            else "V2 memory_state.db unavailable — check kazma-data/ and restart."
        ),
        meta={
            "active_beliefs": bel.get("active", 0),
            "superseded_beliefs": bel.get("superseded", 0),
            "entities": v2.get("entities", 0),
        },
    ))
    components.append(_comp(
        "layer_l1", "V2 beliefs",
        ok=v2_ok, status=v2_status,
        detail=(
            f"Bi-temporal belief graph: {bel.get('active', 0)} active, "
            f"{bel.get('superseded', 0)} superseded, {bel.get('archived', 0)} archived."
            if v2_ok else "Belief store unavailable."
        ),
        meta={"active": bel.get("active", 0), "superseded": bel.get("superseded", 0)},
    ))
    components.append(_comp(
        "layer_l2", "V2 episodes",
        ok=v2_ok, status=v2_status,
        detail=(
            f"4-tier episodes: {ep.get('recall', 0)} recall, {ep.get('episodic', 0)} episodic, "
            f"{ep.get('archived', 0)} archived."
            if v2_ok else "Episode store unavailable."
        ),
        meta={"recall": ep.get("recall", 0), "episodic": ep.get("episodic", 0)},
    ))
    components.append(_comp(
        "layer_l3", "V2 procedural + queue",
        ok=v2_ok, status=v2_status,
        detail=(
            f"{(v2.get('procedural_dags') or {}).get('active', 0)} procedural DAGs; "
            f"queue {(v2.get('queue') or {}).get('pending', 0)} pending."
            if v2_ok else "Procedural/queue store unavailable."
        ),
        meta={"procedural": (v2.get("procedural_dags") or {}).get("active", 0)},
    ))
    components.append(_comp(
        "layer_l4", "V2 entities",
        ok=v2_ok, status=v2_status,
        detail=(
            f"{v2.get('entities', 0)} resolved entities (3-tier merge cascade)."
            if v2_ok else "Entity store unavailable."
        ),
        meta={"entities": v2.get("entities", 0)},
    ))

    # Consolidation flag
    cons = cfg.get("consolidation") if isinstance(cfg.get("consolidation"), dict) else {}
    cons_on = bool(cons.get("enabled", cfg.get("consolidation_enabled", True))) and mem_enabled
    every_n = cons.get("every_n_turns", 1)
    components.append(_comp(
        "consolidation",
        "LLM consolidator",
        ok=cons_on,
        status="ok" if cons_on else "off",
        detail=(
            f"Post-turn librarian (every_n={every_n}, fence+dedup on). "
            "LLM with heuristic fallback → facts + graph triples."
            if cons_on
            else "Disabled (memory.consolidation.enabled=false)."
        ),
        meta={
            "use_llm": bool(cons.get("use_llm", True)),
            "every_n_turns": every_n,
            "skip_llm_in_demo": bool(cons.get("skip_llm_in_demo", True)),
        },
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
    # Core path: embedder + V2 cognitive engine + store config. The V2 store
    # probes are surfaced via the synthesized vector_memory/layer_l1..l3
    # components above (db_available gates them all).
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
        "V2 cognitive engine operational"
        if any(c["id"] == "vector_memory" and c["status"] == "ok" for c in components)
        else "V2 memory degraded or offline"
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
