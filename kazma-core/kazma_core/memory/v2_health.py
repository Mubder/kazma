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
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["build_v2_health"]


def _safe_count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def build_v2_health() -> dict[str, Any]:
    """Build the V2 cognitive-engine health snapshot.

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
    }
    try:
        from kazma_core.memory.consolidator import get_post_turn_metrics

        out["post_turn"] = get_post_turn_metrics()
        out["last_error"] = out["post_turn"].get("last_error")
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
            "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL",
        )
        out["beliefs"]["superseded"] = _safe_count(
            primary_conn,
            "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NOT NULL",
        )
        out["beliefs"]["archived"] = _safe_count(primary_conn, "SELECT COUNT(*) FROM beliefs_archive")

        # Episodes per tier
        for tier in ("working", "episodic", "recall", "archived"):
            out["episodes"][tier] = _safe_count(
                primary_conn, "SELECT COUNT(*) FROM episodes WHERE tier=?", (tier,)
            )

        # Entities + procedural DAGs
        out["entities"] = _safe_count(primary_conn, "SELECT COUNT(*) FROM entities")
        out["procedural_dags"]["active"] = _safe_count(
            primary_conn, "SELECT COUNT(*) FROM procedural_dags WHERE status='active'"
        )
        out["procedural_dags"]["quarantine"] = _safe_count(
            primary_conn, "SELECT COUNT(*) FROM procedural_dags WHERE status='quarantine'"
        )

        # Ops DB: queue + audits
        if os.path.exists(memory_ops_db()):
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
    return out
