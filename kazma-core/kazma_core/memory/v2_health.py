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
    try:
        from kazma_core.memory.embedder import get_embedder

        emb = get_embedder()
        out["embedder_ready"] = emb is not None
    except Exception:
        out["embedder_ready"] = False

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

        # Entities + procedural DAGs
        out["entities"] = _safe_count(
            primary_conn, "SELECT COUNT(*) FROM entities WHERE 1=1" + tsql, tparams
        )
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
        if out["queue"]["failed"] >= 5:
            out["status"] = "DEGRADED"
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
