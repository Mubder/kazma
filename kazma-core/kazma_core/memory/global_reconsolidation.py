"""Nightly global re-consolidation — merge near-duplicates + re-embed dirty rows.

Separate from backup/export and from 6h macro_sleep so slow disk work cannot
stall decay. Invoked via the durable task queue (``global_reconsolidation``)
or Settings / Dashboard "Run reconsolidation".
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["run_global_reconsolidation"]


def run_global_reconsolidation(
    conn: sqlite3.Connection,
    *,
    tenant_id: str = "default",
    max_merges: int = 50,
    reembed_limit: int = 100,
) -> dict[str, Any]:
    """One reconsolidation sweep.

    1. Collapse near-duplicate active beliefs (same subject+predicate+object
       case-insensitive) keeping the highest-confidence row.
    2. Re-embed up to ``reembed_limit`` episodes/beliefs missing embeddings.

    Returns stats dict. Best-effort — never raises to the worker.
    """
    stats: dict[str, Any] = {
        "duplicate_beliefs_merged": 0,
        "episodes_embedded": 0,
        "beliefs_embedded": 0,
        "errors": 0,
        "duration_s": 0.0,
    }
    t0 = time.time()
    try:
        stats["duplicate_beliefs_merged"] = _merge_duplicate_beliefs(
            conn, tenant_id=tenant_id, max_merges=max_merges
        )
    except Exception:
        stats["errors"] += 1
        logger.warning("[reconsolidation] duplicate merge failed", exc_info=True)
    try:
        ep, bel = _reembed_missing(conn, tenant_id=tenant_id, limit=reembed_limit)
        stats["episodes_embedded"] = ep
        stats["beliefs_embedded"] = bel
    except Exception:
        stats["errors"] += 1
        logger.warning("[reconsolidation] reembed failed", exc_info=True)
    stats["duration_s"] = round(time.time() - t0, 3)
    stats["finished_at"] = time.time()
    logger.info("[reconsolidation] done: %s", stats)
    # Persist last-run for Dashboard (best-effort ConfigStore)
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(
            "memory.v2.last_reconsolidation",
            stats,
            category="memory",
        )
    except Exception:
        pass
    return stats


def _merge_duplicate_beliefs(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    max_merges: int,
) -> int:
    """Invalidate lower-confidence duplicates of the same SPO triple."""
    rows = conn.execute(
        """
        SELECT id, subject, predicate, object, confidence, structural_importance
        FROM beliefs
        WHERE tenant_id = ?
          AND valid_until IS NULL AND invalidated_at IS NULL
        ORDER BY subject, predicate, LOWER(object)
        """,
        (tenant_id,),
    ).fetchall()
    groups: dict[tuple[str, str, str], list[Any]] = {}
    for r in rows:
        key = (
            (r["subject"] or "").strip().lower(),
            (r["predicate"] or "").strip().lower(),
            (r["object"] or "").strip().lower(),
        )
        groups.setdefault(key, []).append(r)

    now = time.time()
    merged = 0
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        # Keep best by confidence * importance
        members_sorted = sorted(
            members,
            key=lambda m: float(m["confidence"] or 0)
            * float(m["structural_importance"] or 1),
            reverse=True,
        )
        keep = members_sorted[0]
        for loser in members_sorted[1:]:
            if merged >= max_merges:
                return merged
            conn.execute(
                """
                UPDATE beliefs
                SET valid_until = ?, invalidated_at = ?, supersedes_id = ?
                WHERE id = ? AND valid_until IS NULL
                """,
                (now, now, keep["id"], loser["id"]),
            )
            merged += 1
    if merged:
        conn.commit()
    return merged


def _reembed_missing(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    limit: int,
) -> tuple[int, int]:
    """Encode rows with NULL embedding. Returns (episodes, beliefs) counts."""
    try:
        from kazma_core.memory.embedder import encode_text_to_blob, get_embedding_model_name
    except Exception:
        return 0, 0

    model = ""
    try:
        model = get_embedding_model_name() or ""
    except Exception:
        pass

    ep_n = 0
    for r in conn.execute(
        """
        SELECT id, user_text, assistant_text, summary_text
        FROM episodes
        WHERE tenant_id = ? AND embedding IS NULL
          AND tier IN ('working', 'episodic', 'recall')
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (tenant_id, limit),
    ).fetchall():
        text = (r["summary_text"] or r["user_text"] or r["assistant_text"] or "").strip()
        if not text:
            continue
        blob = encode_text_to_blob(text)
        if blob is None:
            continue
        conn.execute(
            "UPDATE episodes SET embedding=?, embedding_model_version=? WHERE id=?",
            (blob, model, r["id"]),
        )
        ep_n += 1

    bel_n = 0
    for r in conn.execute(
        """
        SELECT id, subject, predicate, object
        FROM beliefs
        WHERE tenant_id = ? AND embedding IS NULL
          AND valid_until IS NULL AND invalidated_at IS NULL
        ORDER BY ingested_at DESC
        LIMIT ?
        """,
        (tenant_id, limit),
    ).fetchall():
        text = f"{r['subject']} {r['predicate']} {r['object']}".strip()
        if not text:
            continue
        blob = encode_text_to_blob(text)
        if blob is None:
            continue
        conn.execute(
            "UPDATE beliefs SET embedding=?, embedding_model_version=? WHERE id=?",
            (blob, model, r["id"]),
        )
        bel_n += 1

    if ep_n or bel_n:
        conn.commit()
    return ep_n, bel_n
