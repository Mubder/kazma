"""Live memory-subsystem health for the Dashboard Memory & Governance panel.

Each component is reported as ok / warn / error / off with a short human
reason (e.g. missing API key, package not installed) so operators can see
what is real and working without reading server logs.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

__all__ = ["build_memory_health", "mark_recall_degraded", "recall_degraded"]

logger = logging.getLogger(__name__)

# ── Recall degradation tracking ──────────────────────────────────────────
# recall() fails soft (returns []) on DB/embedder failure. Without a signal,
# a broken embedder feels like amnesia — the operator sees no error unless
# they're watching the Dashboard. This lightweight counter lets the health
# check surface "recall failures detected" as a DEGRADED status.
_recall_fail_count: int = 0
# Guards the recall-failure counters (mark_recall_degraded is called from
# consolidator threads + the loop concurrently — bare `+= 1` undercounts).
_recall_fail_lock = threading.Lock()
_recall_last_failure: float = 0.0
_recall_last_reason: str = ""

# TTL cache for the embedder liveness probe in build_memory_health. The
# dashboard polls health every few seconds; without this, each poll fired a
# real embed API call (billed + latency) on a remote embedder (audit finding).
# Only successful (non-empty) probes are cached; a fresh process re-probes.
_HEALTH_EMBED_TTL = 300.0
_health_embed_cache: dict[str, Any] = {}
_health_embed_cache_ts: float = 0.0


def mark_recall_degraded(reason: str = "") -> None:
    """Record a recall failure so build_memory_health() can surface it.

    Called by recall() on its outer except path. Increment-only;
    build_memory_health() reads the count and timestamp to decide
    whether to flag DEGRADED.
    """
    global _recall_fail_count, _recall_last_failure, _recall_last_reason
    # Guarded: recall runs from the consolidator's per-turn threads AND the
    # loop concurrently; the bare `+= 1` on a module global could undercount
    # under concurrent failures (audit finding).
    with _recall_fail_lock:
        _recall_fail_count += 1
        _recall_last_failure = time.time()
    _recall_last_reason = reason or "unknown"


def recall_degraded() -> dict[str, Any]:
    """Return the recall-failure state for the health dashboard."""
    return {
        "fail_count": _recall_fail_count,
        "last_failure_epoch": _recall_last_failure,
        "last_reason": _recall_last_reason,
    }


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
    recall_state = recall_degraded()
    recall_fail_count = int(recall_state.get("fail_count") or 0)
    recall_degraded_now = recall_fail_count > 0
    recall_last_reason = str(recall_state.get("last_reason") or "").strip()

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

    components.append(_comp(
        "recall_failures",
        "Recall pipeline reliability",
        ok=not recall_degraded_now,
        status="warn" if recall_degraded_now else "ok",
        detail=(
            f"Recall failures detected: {recall_fail_count} (last: {recall_last_reason or 'unknown'})."
            if recall_degraded_now
            else "No recall failures recorded in this process."
        ),
        meta=recall_state,
    ))

    # ── Embedder ──────────────────────────────────────────────────────
    emb_cfg = (cfg.get("embedding") or {}) if isinstance(cfg.get("embedding"), dict) else {}
    _emb_cfg: dict[str, Any] = {}
    try:
        from kazma_core.memory.embedder import get_embedding_config, DEFAULT_MODEL

        _emb_cfg = get_embedding_config()
        provider = str(_emb_cfg["provider"])
        model = str(_emb_cfg["model"])
    except Exception:
        provider = str(
            os.environ.get("KAZMA_EMBED_PROVIDER", "") or emb_cfg.get("provider", "local")
        ).strip().lower()
        model = str(
            os.environ.get("KAZMA_EMBED_MODEL", "")
            or emb_cfg.get("model", "BAAI/bge-m3")
        )
    _cfg = _emb_cfg if _emb_cfg else emb_cfg
    api_key_env = str(_cfg.get("api_key_env") or "KAZMA_EMBED_API_KEY")
    has_key = bool(
        os.environ.get(api_key_env)
        or os.environ.get("KAZMA_EMBED_API_KEY")
        or os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NGC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    base_url = str(os.environ.get("KAZMA_EMBED_BASE_URL", "") or _cfg.get("base_url", "")).strip()

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
            # Reuse a cached successful probe within the TTL (values are stable
            # for a given embedder) instead of calling encode() on every poll.
            _now = time.monotonic()
            _cached = (
                _health_embed_cache
                if (_now - _health_embed_cache_ts) < _HEALTH_EMBED_TTL
                and _health_embed_cache.get("_emb_class") == type(emb).__name__
                else None
            )
            if _cached and _cached.get("sample"):
                sample = _cached["sample"]
            else:
                sample = emb.encode("health-check")
                if sample:  # only cache a successful probe
                    _health_embed_cache["sample"] = sample
                    _health_embed_cache["_emb_class"] = type(emb).__name__
                    _health_embed_cache_ts = _now
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
                f"Falling back to local {model} — working, but not using {model}."
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
            f"{v2.get('entities', 0)} entities. "
            f"Graph dual-write: {(v2.get('graph') or {}).get('provider', 'sqlite')}."
            if v2_ok
            else "V2 memory_state.db unavailable — check kazma-data/ and restart."
        ),
        meta={
            "active_beliefs": bel.get("active", 0),
            "superseded_beliefs": bel.get("superseded", 0),
            "entities": v2.get("entities", 0),
            "graph": v2.get("graph") or {},
        },
    ))
    components.append(_comp(
        "layer_l1", "V2 beliefs (SQLite SoT)",
        ok=v2_ok, status=v2_status,
        detail=(
            f"Bi-temporal beliefs: {bel.get('active', 0)} active, "
            f"{bel.get('superseded', 0)} superseded, {bel.get('archived', 0)} archived. "
            "Dashboard topology paints from SQLite."
            if v2_ok else "Belief store unavailable."
        ),
        meta={"active": bel.get("active", 0), "superseded": bel.get("superseded", 0)},
    ))
    components.append(_comp(
        "layer_l2", "V2 episodes",
        ok=v2_ok, status=v2_status,
        detail=(
            f"4-tier episodes: {ep.get('working', 0)} working, {ep.get('recall', 0)} recall, "
            f"{ep.get('episodic', 0)} episodic, {ep.get('archived', 0)} archived."
            if v2_ok else "Episode store unavailable."
        ),
        meta={"recall": ep.get("recall", 0), "episodic": ep.get("episodic", 0)},
    ))
    components.append(_comp(
        "layer_l3", "V2 procedural + queue",
        ok=v2_ok, status=v2_status,
        detail=(
            f"{(v2.get('procedural_dags') or {}).get('active', 0)} procedural DAGs; "
            f"queue {(v2.get('queue') or {}).get('pending', 0)} pending / "
            f"{(v2.get('queue') or {}).get('failed', 0)} failed."
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

    # Neo4j dual-write / topology adapter
    gmeta = v2.get("graph") if isinstance(v2.get("graph"), dict) else {}
    try:
        from kazma_core.memory.backends import get_backends_cfg
        from kazma_core.memory.graph_backend import get_graph_backend, graph_capability

        _bcfg = get_backends_cfg()
        gcap = graph_capability(_bcfg)
        gprov = str(((_bcfg.get("graph") or {}).get("provider") or "sqlite")).lower()
        if gprov == "neo4j":
            gb = get_graph_backend()
            online = getattr(gb, "name", "") == "neo4j" and bool(getattr(gb, "available", False))
            components.append(_comp(
                "graph_neo4j",
                "Neo4j dual-write",
                ok=online,
                status="ok" if online else "warn",
                detail=(
                    f"Neo4j online — dual-write on belief mutate; Dashboard paint remains SQLite. "
                    f"{gcap.get('detail') or ''}"
                    if online
                    else (
                        "Graph provider=neo4j but server/driver auth failed — "
                        "Settings → Memory → Test Neo4j (re-enter password if masked)."
                    )
                ),
                meta={"provider": "neo4j", "online": online, "paint_source": "sqlite"},
            ))
        else:
            components.append(_comp(
                "graph_neo4j",
                "Neo4j dual-write",
                ok=True,
                status="off",
                detail="Optional. Graph store is SQLite (default). Enable Neo4j in Settings → Memory.",
                meta={"provider": gprov or "sqlite"},
            ))
    except Exception as exc:
        components.append(_comp(
            "graph_neo4j", "Neo4j dual-write",
            ok=False, status="warn",
            detail=f"Graph backend probe failed: {exc}",
        ))

    # Knowledge Library → chat inject (product merge)
    try:
        from kazma_core.memory.config import read_memory_cfg as _rmc

        _v2cfg = (_rmc().get("v2") or {}) if isinstance(_rmc(), dict) else {}
        kb_merge = bool(_v2cfg.get("merge_knowledge_into_chat", True))
        kb_promote = bool(_v2cfg.get("promote_kb_to_episodes", True))
        components.append(_comp(
            "kb_merge",
            "KB → chat inject",
            ok=kb_merge,
            status="ok" if kb_merge else "off",
            detail=(
                f"Labeled Knowledge Library hits inject into supervisor "
                f"(promote_kb_to_episodes={kb_promote}). Stores stay separate."
                if kb_merge
                else "merge_knowledge_into_chat=false — KB not auto-injected into chat."
            ),
            meta={"merge": kb_merge, "promote": kb_promote},
        ))
    except Exception:
        components.append(_comp(
            "kb_merge", "KB → chat inject",
            ok=True, status="warn",
            detail="Could not read merge_knowledge_into_chat flag (defaults on).",
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
        (
            "pkg_st",
            "Package: sentence-transformers",
            "sentence_transformers",
            "Local embedder (BAAI/bge-m3 etc.). Fix: pip install -e '.[rag]'",
            "rag",
        ),
        (
            "pkg_sqlite_vec",
            "Package: sqlite-vec",
            "sqlite_vec",
            "Local vector backend for dense recall. Fix: pip install -e '.[rag]'",
            "rag",
        ),
        (
            "pkg_chromadb",
            "Package: chromadb",
            "chromadb",
            "Optional legacy vector store (not required for V2 SQLite-first stack).",
            "optional",
        ),
        (
            "pkg_neo4j",
            "Package: neo4j",
            "neo4j",
            "Official Bolt driver for Neo4j dual-write. Fix: pip install neo4j",
            "neo4j",
        ),
        (
            "pkg_psycopg",
            "Package: psycopg",
            "psycopg",
            "Postgres multi-replica stores. Fix: pip install -e '.[postgres]'",
            "postgres",
        ),
        (
            "pkg_lg_pg",
            "Package: langgraph-checkpoint-postgres",
            "langgraph.checkpoint.postgres",
            "Postgres graph checkpoints. Fix: pip install -e '.[postgres]'",
            "postgres",
        ),
    ]
    # Detect whether Neo4j is configured
    want_neo4j = False
    try:
        from kazma_core.memory.backends import get_backends_cfg as _gbc

        want_neo4j = str(((_gbc().get("graph") or {}).get("provider") or "")).lower() == "neo4j"
    except Exception:
        want_neo4j = False

    for id_, name, mod, why, kind in pkgs:
        present = _has_mod(mod)
        if kind == "postgres":
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
        if kind == "neo4j":
            if not want_neo4j and not present:
                components.append(_comp(
                    id_, name,
                    ok=True, status="off",
                    detail="neo4j driver not installed (optional until Graph store = Neo4j).",
                ))
                continue
            if want_neo4j and not present:
                components.append(_comp(
                    id_, name,
                    ok=False, status="error",
                    detail=f"{mod} missing while Neo4j is configured — {why}",
                ))
                continue
        if kind == "optional":
            components.append(_comp(
                id_, name,
                ok=True,
                status="ok" if present else "off",
                detail=(
                    f"{mod} is installed (optional)."
                    if present
                    else f"{mod} not installed — {why}"
                ),
            ))
            continue
        components.append(_comp(
            id_, name,
            ok=present,
            status="ok" if present else ("warn" if kind == "rag" else "error"),
            detail=(
                f"{mod} is installed."
                if present
                else f"{mod} not installed — {why}"
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
    elif core_errors or (mem_enabled and (not has_search_layer or recall_degraded_now)):
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
