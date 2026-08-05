"""Memory hygiene — reject bad beliefs, heal FTS, invalidate with graph cleanup.

Keeps product/version confusion out of the belief store (e.g. ``kazma_v2_4_0``
from “memory V2” language) and makes SQLite belief writes resilient to a
corrupt ``beliefs_fts`` index.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "is_blocked_belief_subject",
    "is_blocked_belief_triple",
    "is_junk_entity_token",
    "rebuild_beliefs_fts",
    "beliefs_write",
    "invalidate_belief",
]

# Subjects that confuse product version / memory-stack naming with entities.
# Product version is e.g. 0.6.x — "V2" means the cognitive memory engine.
_BLOCKED_SUBJECT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^kazma_v2([_\-]|$)", re.I),
    re.compile(r"^kazma[_-]?v2[_-]?\d", re.I),
    re.compile(r"^pure[_-]?v2([_\-]|$)", re.I),
    re.compile(r"^memory[_-]?v2([_\-]|$)", re.I),
    re.compile(r"^v2[_-]?(memory|cognitive|engine|stack)([_\-]|$)", re.I),
    re.compile(r"^v2[_-]?\d+[_\-]?\d+", re.I),  # bare v2_4_0 style
)

# Boolean / null tokens that become orphan graph "entities" when belief
# objects like memory_needs_cleanup → true are promoted to nodes.
# Keep them as *object text* on beliefs; never as entity id / virtual node.
_JUNK_ENTITY_TOKENS = frozenset(
    {
        "true",
        "false",
        "null",
        "none",
        "nil",
        "undefined",
        "nan",
        "yes",
        "no",
        "y",
        "n",
        "0",
        "1",
    }
)


def is_junk_entity_token(text: str) -> bool:
    """True for tokens that must never be graph entity ids (true/false/…)."""
    s = (text or "").strip().lower()
    if not s:
        return True
    if s in _JUNK_ENTITY_TOKENS:
        return True
    # Bare integers (importance/count shells like entity name "5")
    if s.isdigit() and len(s) <= 4:
        return True
    return False


def is_blocked_belief_subject(subject: str) -> bool:
    """True if this subject slug should never become a belief entity."""
    s = (subject or "").strip().lower().replace(" ", "_")
    if not s:
        return True
    if is_junk_entity_token(s):
        return True
    for pat in _BLOCKED_SUBJECT_RES:
        if pat.search(s):
            return True
    return False


def is_blocked_belief_triple(subject: str, predicate: str = "", obj: str = "") -> bool:
    """Reject triples whose subject is blocked.

    Boolean *objects* (``… → true``) are allowed as fact payloads — they are
    not blocked here. Graph emission must use :func:`is_junk_entity_token` so
    those objects never become stand-alone entity nodes.
    """
    del predicate, obj
    return is_blocked_belief_subject(subject)


def rebuild_beliefs_fts(conn: sqlite3.Connection) -> bool:
    """Best-effort rebuild of ``beliefs_fts``. Returns True on success."""
    try:
        conn.execute("INSERT INTO beliefs_fts(beliefs_fts) VALUES('rebuild')")
        try:
            conn.commit()
        except Exception:
            pass
        logger.info("[hygiene] rebuilt beliefs_fts after write error")
        return True
    except Exception:
        logger.debug("[hygiene] beliefs_fts rebuild failed", exc_info=True)
        try:
            # Last resort: drop FTS + triggers and re-create via schema ensure
            conn.executescript(
                """
                DROP TRIGGER IF EXISTS beliefs_fts_ai;
                DROP TRIGGER IF EXISTS beliefs_fts_ad;
                DROP TRIGGER IF EXISTS beliefs_fts_au;
                DROP TABLE IF EXISTS beliefs_fts;
                """
            )
            try:
                conn.commit()
            except Exception:
                pass
            from kazma_core.memory.schema_v2 import ensure_primary_schema

            ensure_primary_schema(conn)
            logger.info("[hygiene] recreated beliefs_fts via ensure_primary_schema")
            return True
        except Exception:
            logger.warning("[hygiene] could not recreate beliefs_fts", exc_info=True)
            return False


def beliefs_write(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...] | list[Any] = (),
) -> sqlite3.Cursor:
    """Execute a beliefs-table write; self-heal FTS if SQLite reports malformation."""
    try:
        return conn.execute(sql, params)
    except sqlite3.DatabaseError as exc:
        msg = str(exc).lower()
        if "malformed" not in msg and "corrupt" not in msg and "disk image" not in msg:
            raise
        logger.warning("[hygiene] beliefs write hit DB error (%s) — healing FTS", exc)
        if not rebuild_beliefs_fts(conn):
            raise
        return conn.execute(sql, params)


def invalidate_belief(
    belief_id: str,
    *,
    conn: sqlite3.Connection | None = None,
    now: float | None = None,
    remove_graph: bool = True,
) -> dict[str, Any]:
    """Soft-invalidate one belief and best-effort remove its Neo4j edge.

    Returns ``{"ok": bool, "updated": int, "graph_removed": bool, ...}``.
    """
    own_conn = conn is None
    ts = float(now if now is not None else time.time())
    bid = (belief_id or "").strip()
    if not bid:
        return {"ok": False, "updated": 0, "error": "belief_id required"}

    try:
        if own_conn:
            from kazma_core.memory.schema_v2 import ensure_primary_schema
            from kazma_core.paths import primary_memory_db

            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
        assert conn is not None

        row = conn.execute(
            """
            SELECT id, subject, predicate, object, tenant_id, valid_until, invalidated_at
            FROM beliefs WHERE id=? LIMIT 1
            """,
            (bid,),
        ).fetchone()
        if not row:
            if own_conn:
                conn.close()
            return {"ok": False, "updated": 0, "error": "not found"}

        def _col(name: str, idx: int) -> Any:
            if isinstance(row, sqlite3.Row):
                return row[name]
            return row[idx]

        # Already soft-deleted → idempotent success (UI unlink must not fail)
        if _col("valid_until", 5) is not None or _col("invalidated_at", 6) is not None:
            if own_conn:
                conn.close()
            return {
                "ok": True,
                "updated": 0,
                "already": True,
                "graph_removed": False,
                "belief_id": bid,
            }

        cur = beliefs_write(
            conn,
            """
            UPDATE beliefs SET valid_until=?, invalidated_at=?
            WHERE id=? AND valid_until IS NULL
            """,
            (ts, ts, bid),
        )
        conn.commit()
        n = int(cur.rowcount or 0)

        # Phase 3: invalidation removes the belief from the active set, so the
        # subject/object entities' belief_count / graph_degree drop. Recompute
        # them (centralized here so all invalidate paths stay consistent —
        # single, batch, and the UI unlink all funnel through this function).
        # Only on a real flip (n > 0); idempotent re-invalidates are no-ops.
        if n > 0:
            try:
                from kazma_core.memory.entity_counts import recompute_entity_counts

                sub = _col("subject", 1)
                obj = _col("object", 3)
                tid = _col("tenant_id", 4)
                recompute_entity_counts(
                    conn,
                    [str(sub or ""), str(obj or "")],
                    tenant_id=str(tid or "default"),
                )
                conn.commit()
            except Exception:
                logger.debug("[hygiene] entity count recompute skipped", exc_info=True)

        graph_removed = False
        if remove_graph and n > 0:
            try:
                from kazma_core.memory.graph_backend import delete_belief_edge

                sub = _col("subject", 1)
                pred = _col("predicate", 2)
                obj = _col("object", 3)
                tid = _col("tenant_id", 4)
                graph_removed = bool(
                    delete_belief_edge(
                        belief_id=bid,
                        subject=str(sub or ""),
                        predicate=str(pred or ""),
                        obj=str(obj or ""),
                        tenant_id=str(tid or "default"),
                    )
                )
            except Exception:
                logger.debug("[hygiene] neo4j edge delete skipped", exc_info=True)

        if own_conn:
            conn.close()
        return {
            "ok": n > 0,
            "updated": n,
            "graph_removed": graph_removed,
            "belief_id": bid,
        }
    except Exception as exc:
        logger.debug("[hygiene] invalidate_belief failed", exc_info=True)
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return {"ok": False, "updated": 0, "error": str(exc)[:300]}
