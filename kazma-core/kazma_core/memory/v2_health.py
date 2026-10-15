"""V2 cognitive-engine health builder.

Returns a compact status dict consumed by the Web Dashboard and the TUI
Memory panel. Mirrors the shape of :func:`build_memory_health` (legacy)
so the UI renderers can treat both uniformly.

Reports:
  - V2 stack status (use_new_stack flag, DB availability)
  - Belief counts (active / superseded / archived)
  - Episode counts per tier (working / episodic / recall / archived)
  - Entity + procedural DAG counts
  - Worker queue depth (pending / processing / failed)
  - Recent audit-log activity
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["build_v2_health", "count_current_facts"]


def count_current_facts(tenant_id: str | None = None) -> int | None:
    """How many facts memory holds now (current beliefs), for one tenant or all.

    Read-only. ``None`` when the memory database cannot be read -- the caller
    says "unknown", never a zero that is not true.
    """
    from kazma_core.paths import primary_memory_db

    path = Path(primary_memory_db())
    if not path.is_file():
        return 0
    sql = "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
    params: tuple[Any, ...] = ()
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params = (tenant_id,)
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        logger.debug("[v2_health] memory database unreadable", exc_info=True)
        return None
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    except sqlite3.Error:
        logger.debug("[v2_health] fact count failed", exc_info=True)
        return None
    finally:
        conn.close()


def _safe_count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _lifecycle_signals(out: dict[str, Any], *, install_details: bool) -> None:
    """Mark *out* degraded for what memory gave up on or cannot do:

    * a dead letter -- kept until the operator retries or clears it, so one is
      enough (Memory audit M12: the threshold was five, and purging them after
      a week cleared it);
    * a maintenance sweep failing three passes in a row (M20, ``sweeps``);
    * the embedder's breaker open: meaning search is off and recall answers
      from keywords, marked degraded (M16, ``embedder``).
    """
    from kazma_core.memory.embedder import embedder_health
    from kazma_core.memory.worker_bootstrap import sweep_health

    out["sweeps"] = sweep_health() if install_details else {}
    out["embedder"] = embedder_health()
    for failing, detail in (
        (out["queue"]["failed"] >= 1, "dead_letters"),
        (any(int(s.get("consecutive_failures") or 0) >= 3 for s in out["sweeps"].values()), "sweep_failing"),
        (out["embedder"].get("state") == "open", "embedder_unavailable"),
    ):
        if failing:
            out["status"] = "DEGRADED"
            out["status_detail"] = detail


def _queue_ages(ops_conn: Any) -> dict[str, Any]:
    """How long the oldest pending task has waited and the oldest running one
    has run, and each task type's last success (Memory audit M20: health
    showed counts only, so a stuck queue read as a busy one)."""
    import time as _time

    now = _time.time()
    out: dict[str, Any] = {"oldest_pending_s": None, "oldest_processing_s": None, "last_success": {}}
    try:
        row = ops_conn.execute(
            "SELECT MIN(CASE WHEN status = 'pending' THEN created_at END), "
            "MIN(CASE WHEN status = 'processing' THEN updated_at END) FROM memory_task_queue").fetchone()
        if row and row[0] is not None:
            out["oldest_pending_s"] = round(now - float(row[0]), 1)
        if row and row[1] is not None:
            out["oldest_processing_s"] = round(now - float(row[1]), 1)
        out["last_success"] = {
            str(t): float(at) for t, at in ops_conn.execute(
                "SELECT task_type, MAX(updated_at) FROM memory_task_queue WHERE status = 'completed' "
                "GROUP BY task_type")
        }
    except sqlite3.Error:
        logger.warning("[v2_health] queue ages unreadable", exc_info=True)
    return out


#: The install's own state. Shown with ``install_details`` only: to the
#: install's own tenant and to an admin, never to a principal of another
#: tenant (engine errors and queue rows can carry any tenant's text).
_INSTALL_ONLY = (
    "queue",
    "recent_audits",
    "post_turn",
    "last_error",
    "last_reconsolidation",
    "findability",
    "vector_capability",
    "mirror",
    "embedder",
    "sweeps",
)


def build_v2_health(
    tenant_id: str | None = None, *, install_details: bool = True
) -> dict[str, Any]:
    """Build the V2 cognitive-engine health snapshot.

    ``tenant_id`` narrows the belief, episode, entity and skill counts to one
    tenant; ``None`` and the install's own tenant ("default") count the whole
    install. ``install_details=False`` leaves out the install's own state
    (``_INSTALL_ONLY``, the graph backend's address) and marks the answer
    ``"scope": "tenant"``. Every URL password in the answer is masked.

    Returns::

        {
          "status": "ACTIVE" | "DEGRADED" | "OFF",
          "use_new_stack": bool,
          "db_available": bool,
          "beliefs": {"active": int, "superseded": int, "archived": int},
          "episodes": {"working": int, "episodic": int, "recall": int, "archived": int},
          "entities": int,
          "entity_key": "tenant_and_id" | "id",   # memory/entity_key.py (M21)
          "procedural_dags": {"active": int, "quarantine": int},
          "queue": {"pending": int, "processing": int, "failed": int},
          "recent_audits": int,
        }

    Never raises — a missing/broken DB returns status="OFF" with zeros.
    """
    scoped = tenant_id not in (None, "", "default")
    # A tenant predicate for every count below: (" AND tenant_id=?", (tid,)).
    tsql = " AND tenant_id=?" if scoped else ""
    tparams: tuple = (tenant_id,) if scoped else ()
    # Read the flag from ConfigStore
    try:
        from kazma_core.memory.config import memory_v2_enabled

        use_new_stack = memory_v2_enabled()
    except Exception:
        use_new_stack = False

    out: dict[str, Any] = {
        "status": "OFF",
        "use_new_stack": use_new_stack,
        "db_available": False,
        "beliefs": {"active": 0, "superseded": 0, "archived": 0},
        "episodes": {"working": 0, "episodic": 0, "recall": 0, "archived": 0},
        "entities": 0,
        "procedural_dags": {"active": 0, "quarantine": 0},
        "queue": {"pending": 0, "processing": 0, "failed": 0},
        "recent_audits": 0,
        "post_turn": {},
        "embedder_ready": False,
        "last_error": None,
        "last_reconsolidation": None,
        "vector_capability": {},
        "graph": {
            "provider": "sqlite",
            "online": True,
            "dual_write": False,
            "paint_source": "sqlite",
            "detail": "",
        },
        "backends_mode": "local",
    }
    try:
        from kazma_core.memory.consolidator import get_post_turn_metrics

        out["post_turn"] = get_post_turn_metrics()
        out["last_error"] = out["post_turn"].get("last_error")
    except Exception:
        pass
    try:
        from kazma_core.memory.backends import vector_capability

        out["vector_capability"] = vector_capability()
    except Exception:
        pass
    try:
        from kazma_core.memory.backends import get_backends_cfg
        from kazma_core.memory.graph_backend import get_graph_backend, graph_capability

        cfg = get_backends_cfg()
        out["backends_mode"] = str(cfg.get("mode") or "local")
        gcfg = cfg.get("graph") or {}
        provider = str(gcfg.get("provider") or "sqlite").lower() or "sqlite"
        gcap = graph_capability(cfg)
        online = True
        dual = provider == "neo4j"
        if provider == "neo4j":
            gb = get_graph_backend()
            online = getattr(gb, "name", "") == "neo4j" and bool(
                getattr(gb, "available", False)
            )
        out["graph"] = {
            "provider": provider,
            "online": online,
            "dual_write": dual,
            "paint_source": "sqlite",
            "url": str(gcfg.get("url") or "") if dual else "",
            "detail": str(gcap.get("detail") or ""),
            "status": str(gcap.get("status") or ("online" if online else "offline")),
        }
    except Exception:
        pass
    try:
        from kazma_core.config_store import get_config_store

        lr = get_config_store().get("memory.v2.last_reconsolidation")
        if isinstance(lr, dict):
            out["last_reconsolidation"] = lr
    except Exception:
        pass
    # What this process holds, never a build: health is polled, and building
    # the embedder loads its model (embedder_readiness).
    from kazma_core.memory.embedder import embedder_readiness

    readiness = embedder_readiness()
    out["embedder_ready"] = readiness["ready"]
    out["embedder_state"] = readiness["state"]

    primary_conn = None
    ops_conn = None
    try:
        from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
        from kazma_core.paths import memory_ops_db, primary_memory_db

        import os

        if not os.path.exists(primary_memory_db()):
            return out
        primary_conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        primary_conn.row_factory = sqlite3.Row
        ensure_primary_schema(primary_conn)
        out["db_available"] = True

        # Beliefs
        out["beliefs"]["active"] = _safe_count(
            primary_conn,
            "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
            + tsql,
            tparams,
        )
        out["beliefs"]["superseded"] = _safe_count(
            primary_conn,
            "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NOT NULL" + tsql,
            tparams,
        )
        out["beliefs"]["archived"] = _safe_count(
            primary_conn, "SELECT COUNT(*) FROM beliefs_archive WHERE 1=1" + tsql, tparams
        )

        # Episodes per tier
        for tier in ("working", "episodic", "recall", "archived"):
            out["episodes"][tier] = _safe_count(
                primary_conn,
                "SELECT COUNT(*) FROM episodes WHERE tier=?" + tsql,
                (tier, *tparams),
            )

        # Findability (docs/plans/MEMORY_NOTHING_LOST_PLAN.md, item G): the
        # install's repair and recovery state.
        if install_details:
            findability = _findability(primary_conn)
            if findability is not None:
                out["findability"] = findability

        # Weekly topic summaries (plan C2): written, being rewritten, weeks done.
        try:
            from kazma_core.memory.topic_summaries import summary_health

            out["summaries"] = summary_health(primary_conn, tenant_id=tenant_id or "default")
        except sqlite3.Error:
            logger.warning("[v2_health] weekly summary counts unreadable", exc_info=True)

        # The Postgres mirror as the last sync pass found it (empty: no mirror,
        # or no pass since boot).
        from kazma_core.memory.state_backend import last_mirror_sync

        out["mirror"] = last_mirror_sync()

        # How clean the graph is (graph_hygiene): read-only counts + examples.
        try:
            from kazma_core.memory.graph_hygiene import graph_hygiene_report

            out["graph"] = graph_hygiene_report(primary_conn, tenant_id=tenant_id)
        except sqlite3.Error:
            logger.warning("[v2_health] graph hygiene counts unreadable", exc_info=True)

        # Entities + procedural DAGs
        only = tenant_id if scoped else None
        out["entities"] = _safe_count(
            primary_conn, "SELECT COUNT(*) FROM entities WHERE (? IS NULL OR tenant_id = ?)", (only, only)
        )
        # Which key the store has (memory/entity_key.py, Memory audit M21):
        # "id" until the operator migrates a store made before 2026-10-15.
        try:
            from kazma_core.memory.entity_key import tenant_keyed

            out["entity_key"] = "tenant_and_id" if tenant_keyed(primary_conn) else "id"
        except sqlite3.Error:
            logger.warning("[v2_health] entity key unreadable", exc_info=True)
        out["procedural_dags"]["active"] = _safe_count(
            primary_conn,
            "SELECT COUNT(*) FROM procedural_dags WHERE status='active'" + tsql,
            tparams,
        )
        out["procedural_dags"]["quarantine"] = _safe_count(
            primary_conn,
            "SELECT COUNT(*) FROM procedural_dags WHERE status='quarantine'" + tsql,
            tparams,
        )

        # Ops DB: queue + audits (the install's)
        if install_details and os.path.exists(memory_ops_db()):
            ops_conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            ensure_ops_schema(ops_conn)
            for st in ("pending", "processing", "failed"):
                out["queue"][st] = _safe_count(
                    ops_conn, "SELECT COUNT(*) FROM memory_task_queue WHERE status=?", (st,)
                )
            out["queue"].update(_queue_ages(ops_conn))
            out["recent_audits"] = _safe_count(
                ops_conn,
                "SELECT COUNT(*) FROM memory_audit_log WHERE timestamp > ?",
                (__import__("time").time() - 86400,),
            )

        # Overall status
        if out["db_available"]:
            # V1 dual-stack is gone — OFF when use_new_stack false means
            # injection/post-turn disabled, not "legacy RRF active".
            out["status"] = "ACTIVE" if use_new_stack else "OFF"
        # Degraded if queue has many failed tasks or recent post-turn errors
        _lifecycle_signals(out, install_details=install_details)
        pt = out.get("post_turn") or {}
        if int(pt.get("mirror_fail") or 0) + int(pt.get("extract_fail") or 0) >= 3:
            out["status"] = "DEGRADED"
        if pt.get("last_error") and out["status"] == "ACTIVE":
            # Soft signal: still ACTIVE but surface error for UI banner
            out["status_detail"] = "post_turn_errors"
    except Exception:
        logger.debug("[v2_health] build failed", exc_info=True)
        out["status"] = "DEGRADED"
    finally:
        for conn in (primary_conn, ops_conn):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    if not install_details:
        for key in _INSTALL_ONLY:
            out.pop(key, None)
        graph = out.get("graph")
        if isinstance(graph, dict):
            graph.pop("url", None)
            graph.pop("detail", None)
        out["scope"] = "tenant"
    from kazma_core.security.url_credentials import mask_url_credentials_deep

    return mask_url_credentials_deep(out)


def _findability(primary_conn: sqlite3.Connection) -> dict[str, Any] | None:
    """What meaning search can compare, what waits for the 15-minute repair,
    and the memories the pre-2026-09-26 archive rule erased. ``None`` when the
    stores cannot be read."""
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_helpers import store_errors
    from kazma_core.memory.legacy_tables import legacy_archive_counts
    from kazma_core.memory.reembed import vector_repair_counts
    from kazma_core.memory.rehydrate import STATE_KEY, erased_counts
    from kazma_core.memory.turn_reconcile import STATE_KEY as RECONCILE_KEY

    try:
        findability: dict[str, Any] = {
            "vectors": vector_repair_counts(primary_conn),
            "erased": erased_counts(primary_conn),
            "legacy_archive": legacy_archive_counts(primary_conn),
        }
        state = get_config_store().get(STATE_KEY)
        if isinstance(state, dict) and isinstance(state.get("last"), dict):
            findability["last_recovery"] = state["last"]
        reconcile = get_config_store().get(RECONCILE_KEY)
        if isinstance(reconcile, dict) and isinstance(reconcile.get("last"), dict):
            findability["turn_reconcile"] = reconcile["last"]
        return findability
    except store_errors():
        logger.debug("[v2_health] findability counts failed", exc_info=True)
        return None
