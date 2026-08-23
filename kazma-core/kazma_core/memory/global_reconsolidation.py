"""Nightly global re-consolidation — merge near-duplicates + re-embed dirty rows.

Separate from backup/export and from 6h macro_sleep so slow disk work cannot
stall decay. Invoked via the durable task queue (``global_reconsolidation``)
or Settings / Dashboard "Run reconsolidation".

**Huge-corpus partitioning:** one sweep can target a hash partition of subjects
(``partition_index`` / ``partition_count``) and/or a subject cursor so the
worker never loads the full active-belief set into memory. When more work
remains, the handler enqueues the next partition (see worker_bootstrap).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["run_global_reconsolidation", "subject_partition_index"]

# Default shard count when auto-partitioning large corpora
_DEFAULT_PARTITION_COUNT = 8
# Above this many active beliefs, prefer multi-partition fan-out
_HUGE_CORPUS_THRESHOLD = 5_000


def subject_partition_index(subject: str, partition_count: int) -> int:
    """Stable 0..N-1 bucket for a subject slug (MD5, cross-platform)."""
    n = max(1, int(partition_count or 1))
    raw = (subject or "").strip().lower().encode("utf-8")
    h = int(hashlib.md5(raw).hexdigest(), 16)
    return h % n


def run_global_reconsolidation(
    conn: sqlite3.Connection,
    *,
    tenant_id: str = "default",
    max_merges: int = 50,
    reembed_limit: int = 100,
    partition_index: int = 0,
    partition_count: int = 1,
    auto_partition: bool = True,
) -> dict[str, Any]:
    """One reconsolidation sweep (optionally one subject-hash partition).

    1. Collapse near-duplicate active beliefs (same subject+predicate+object
       case-insensitive) keeping the highest-confidence row.
    2. Re-embed up to ``reembed_limit`` episodes/beliefs missing embeddings
       (beliefs filtered to the same partition).

    When ``auto_partition`` is True and the active belief count exceeds
    ``_HUGE_CORPUS_THRESHOLD`` and ``partition_count`` is 1, the run expands
    to ``_DEFAULT_PARTITION_COUNT`` shards and only executes shard 0; stats
    include ``has_more`` / ``next_partition_index`` so the worker can chain.

    Returns stats dict. Best-effort — never raises to the worker.
    """
    p_count = max(1, int(partition_count or 1))
    p_index = max(0, int(partition_index or 0)) % p_count

    stats: dict[str, Any] = {
        "duplicate_beliefs_merged": 0,
        "episodes_embedded": 0,
        "beliefs_embedded": 0,
        "errors": 0,
        "duration_s": 0.0,
        "partition_index": p_index,
        "partition_count": p_count,
        "active_beliefs_scanned": 0,
        "has_more": False,
        "next_partition_index": None,
        "huge_corpus": False,
    }
    t0 = time.time()

    # Detect huge corpus and auto-expand partition grid
    try:
        active_n = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM beliefs
                WHERE tenant_id = ?
                  AND valid_until IS NULL AND invalidated_at IS NULL
                """,
                (tenant_id,),
            ).fetchone()[0]
            or 0
        )
        stats["active_beliefs_total"] = active_n
        if (
            auto_partition
            and p_count == 1
            and active_n >= _HUGE_CORPUS_THRESHOLD
        ):
            p_count = _DEFAULT_PARTITION_COUNT
            p_index = 0
            stats["partition_count"] = p_count
            stats["partition_index"] = p_index
            stats["huge_corpus"] = True
            stats["has_more"] = p_count > 1
            stats["next_partition_index"] = 1 if p_count > 1 else None
            logger.info(
                "[reconsolidation] huge corpus (%d beliefs) — using %d partitions",
                active_n,
                p_count,
            )
    except Exception:
        stats["active_beliefs_total"] = -1

    try:
        merged, scanned = _merge_duplicate_beliefs(
            conn,
            tenant_id=tenant_id,
            max_merges=max_merges,
            partition_index=p_index,
            partition_count=p_count,
        )
        stats["duplicate_beliefs_merged"] = merged
        stats["active_beliefs_scanned"] = scanned
    except Exception:
        stats["errors"] += 1
        logger.warning("[reconsolidation] duplicate merge failed", exc_info=True)
    try:
        ep, bel = _reembed_missing(
            conn,
            tenant_id=tenant_id,
            limit=reembed_limit,
            partition_index=p_index,
            partition_count=p_count,
        )
        stats["episodes_embedded"] = ep
        stats["beliefs_embedded"] = bel
    except Exception:
        stats["errors"] += 1
        logger.warning("[reconsolidation] reembed failed", exc_info=True)

    # Chain remaining partitions when caller started a multi-shard run
    if p_count > 1 and p_index + 1 < p_count:
        stats["has_more"] = True
        stats["next_partition_index"] = p_index + 1
    elif not stats.get("has_more"):
        stats["has_more"] = False
        stats["next_partition_index"] = None

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
    partition_index: int = 0,
    partition_count: int = 1,
) -> tuple[int, int]:
    """Invalidate lower-confidence duplicates of the same SPO triple.

    Returns ``(merged_count, rows_scanned)``.
    """
    # Stream active beliefs ordered by subject — filter to partition in Python
    # so we never build a giant groups dict for the full corpus at once.
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

    p_count = max(1, int(partition_count or 1))
    p_index = max(0, int(partition_index or 0)) % p_count

    groups: dict[tuple[str, str, str], list[Any]] = {}
    scanned = 0
    for r in rows:
        sub = (r["subject"] or "").strip().lower()
        if p_count > 1 and subject_partition_index(sub, p_count) != p_index:
            continue
        scanned += 1
        key = (
            sub,
            (r["predicate"] or "").strip().lower(),
            (r["object"] or "").strip().lower(),
        )
        groups.setdefault(key, []).append(r)

    now = time.time()
    merged = 0
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(
            members,
            key=lambda m: float(m["confidence"] or 0)
            * float(m["structural_importance"] or 1),
            reverse=True,
        )
        keep = members_sorted[0]
        for loser in members_sorted[1:]:
            if merged >= max_merges:
                if merged:
                    conn.commit()
                return merged, scanned
            try:
                from kazma_core.memory.hygiene import beliefs_write

                beliefs_write(
                    conn,
                    """
                    UPDATE beliefs
                    SET valid_until = ?, invalidated_at = ?, supersedes_id = ?
                    WHERE id = ? AND valid_until IS NULL
                    """,
                    (now, now, keep["id"], loser["id"]),
                )
            except Exception:
                conn.execute(
                    """
                    UPDATE beliefs
                    SET valid_until = ?, invalidated_at = ?, supersedes_id = ?
                    WHERE id = ? AND valid_until IS NULL
                    """,
                    (now, now, keep["id"], loser["id"]),
                )
            # Best-effort Neo4j edge cleanup for invalidated duplicate
            try:
                from kazma_core.memory.graph_backend import delete_belief_edge

                delete_belief_edge(
                    belief_id=str(loser["id"]),
                    subject=str(loser["subject"] or ""),
                    predicate=str(loser["predicate"] or ""),
                    obj=str(loser["object"] or ""),
                    tenant_id=tenant_id,
                )
            except Exception:
                pass
            # M-09: mirror tombstone + audit row — dedupe invalidations must
            # behave like every other invalidation path.
            try:
                from kazma_core.memory.state_backend import remirror_belief_by_id

                remirror_belief_by_id(conn, str(loser["id"]))
            except Exception:
                logger.debug("[reconsolidation] mirror tombstone skipped", exc_info=True)
            try:
                import sqlite3 as _sq

                from kazma_core.memory.belief_mutation import _write_audit
                from kazma_core.memory.schema_v2 import ensure_ops_schema
                from kazma_core.paths import memory_ops_db

                ops = _sq.connect(memory_ops_db(), timeout=10)
                try:
                    ensure_ops_schema(ops)
                    _write_audit(
                        ops,
                        tenant_id=tenant_id,
                        event_type="dedupe_invalidate",
                        target_id=str(loser["id"]),
                        actor="global_reconsolidation",
                        reason="duplicate of higher-ranked belief",
                        state_before={"id": loser["id"], "subject": loser["subject"]},
                        state_after={"supersedes_id": keep["id"]},
                    )
                finally:
                    ops.close()
            except Exception:
                logger.debug("[reconsolidation] audit row skipped", exc_info=True)
            merged += 1
    if merged:
        conn.commit()
    return merged, scanned


def _reembed_missing(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    limit: int,
    partition_index: int = 0,
    partition_count: int = 1,
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

    p_count = max(1, int(partition_count or 1))
    p_index = max(0, int(partition_index or 0)) % p_count

    # Episodes: no subject key — only run on partition 0 (once per full cycle)
    ep_n = 0
    if p_index == 0:
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
    # Over-fetch then filter by partition so each shard re-embeds its share
    fetch_n = max(limit * max(1, p_count), limit)
    for r in conn.execute(
        """
        SELECT id, subject, predicate, object
        FROM beliefs
        WHERE tenant_id = ? AND embedding IS NULL
          AND valid_until IS NULL AND invalidated_at IS NULL
        ORDER BY ingested_at DESC
        LIMIT ?
        """,
        (tenant_id, fetch_n),
    ).fetchall():
        sub = (r["subject"] or "").strip().lower()
        if p_count > 1 and subject_partition_index(sub, p_count) != p_index:
            continue
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
        if bel_n >= limit:
            break

    if ep_n or bel_n:
        conn.commit()
    return ep_n, bel_n
