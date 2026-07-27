"""One-shot / incremental repair for L3 timestamps, embeddings, and L2 graph.

Repairs stores that were written before the solid write-path fix:

- ``timestamp = 0`` on every ``memories`` row
- ``embedding`` BLOB always NULL (L3 semantic dead)
- empty knowledge graph despite L3 having content

Idempotent: skips rows that already have a positive timestamp and a non-empty
embedding; graph upserts are safe to re-run.

ConfigStore key ``memory.backfill_v2_done`` records a successful full pass so
startup only auto-runs once (unless *force*).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

__all__ = [
    "backfill_graph_from_memories",
    "backfill_l3_timestamps_and_embeddings",
    "maybe_auto_backfill",
    "run_memory_integrity_backfill",
]

logger = logging.getLogger(__name__)

_CFG_KEY = "memory.backfill_v2_done"
_BATCH_LOG_EVERY = 25


def _memory_db_path() -> Path:
    from kazma_core.paths import fts5_memory_path

    return Path(fts5_memory_path())


def backfill_l3_timestamps_and_embeddings(
    db_path: str | Path | None = None,
    *,
    limit: int | None = None,
    force: bool = False,
    encode: bool = True,
) -> dict[str, Any]:
    """Fill zero timestamps and missing embedding BLOBs on L3 ``memories``.

    Args:
        db_path: Override path (tests).
        limit: Max rows to process (None = all needing repair).
        force: Re-encode embeddings even when present.
        encode: When False, only repair timestamps (fast).

    Returns:
        Stats dict: scanned, ts_fixed, emb_fixed, errors, skipped.
    """
    path = Path(db_path) if db_path else _memory_db_path()
    stats: dict[str, Any] = {
        "path": str(path),
        "scanned": 0,
        "ts_fixed": 0,
        "emb_fixed": 0,
        "skipped": 0,
        "errors": 0,
    }
    if not path.exists():
        stats["errors"] = 1
        stats["error"] = "memory.db missing"
        return stats

    from kazma_core.swarm.memory.embedder import (
        encode_text_to_blob,
        resolve_unix_timestamp,
    )

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        # Repair schema/triggers first — broken FTS5 UPDATE triggers made
        # every memories UPDATE raise SQL logic error.
        from kazma_core.memory.schema import ensure_memories_schema_sync

        ensure_memories_schema_sync(conn)

        where = (
            "1=1"
            if force
            else (
                "(timestamp IS NULL OR timestamp = 0 OR embedding IS NULL "
                "OR length(embedding) = 0)"
            )
        )
        sql = f"SELECT id, content, metadata, timestamp, embedding FROM memories WHERE {where}"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        stats["scanned"] = len(rows)

        for i, row in enumerate(rows):
            mid = row["id"]
            content = row["content"] or ""
            meta_raw = row["metadata"] or "{}"
            try:
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
                if not isinstance(meta, dict):
                    meta = {}
            except Exception:
                meta = {}

            ts = int(row["timestamp"] or 0)
            emb = row["embedding"]
            need_ts = ts <= 0 or force
            need_emb = encode and (force or emb is None or len(emb) == 0)

            if not need_ts and not need_emb:
                stats["skipped"] += 1
                continue

            new_ts = ts
            if need_ts:
                # Prefer metadata time; fall back to "now - index" so order is preserved
                new_ts = resolve_unix_timestamp(meta)
                if new_ts <= 0:
                    new_ts = int(time.time()) - max(0, len(rows) - i)
                meta.setdefault("timestamp", new_ts)
                stats["ts_fixed"] += 1

            new_emb = emb
            if need_emb and content.strip():
                try:
                    blob = encode_text_to_blob(content)
                    if blob:
                        new_emb = blob
                        stats["emb_fixed"] += 1
                except Exception:
                    stats["errors"] += 1
                    logger.debug("[backfill] embed failed id=%s", mid, exc_info=True)

            try:
                conn.execute(
                    """
                    UPDATE memories
                    SET timestamp = ?, embedding = ?, metadata = ?
                    WHERE id = ?
                    """,
                    (
                        new_ts,
                        new_emb,
                        json.dumps(meta, ensure_ascii=False, default=str),
                        mid,
                    ),
                )
            except Exception:
                stats["errors"] += 1
                logger.debug("[backfill] update failed id=%s", mid, exc_info=True)

            if (i + 1) % _BATCH_LOG_EVERY == 0:
                conn.commit()
                logger.info(
                    "[backfill] L3 progress %d/%d ts=%d emb=%d",
                    i + 1,
                    len(rows),
                    stats["ts_fixed"],
                    stats["emb_fixed"],
                )

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "[backfill] L3 done scanned=%s ts_fixed=%s emb_fixed=%s errors=%s",
        stats["scanned"],
        stats["ts_fixed"],
        stats["emb_fixed"],
        stats["errors"],
    )
    return stats


def backfill_graph_from_memories(
    db_path: str | Path | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    """Ensure every L3 memory has a graph chunk node + user edge + heuristics."""
    path = Path(db_path) if db_path else _memory_db_path()
    stats: dict[str, Any] = {
        "path": str(path),
        "nodes": 0,
        "edges": 0,
        "triples": 0,
        "errors": 0,
        "scanned": 0,
    }
    if not path.exists():
        stats["errors"] = 1
        stats["error"] = "memory.db missing"
        return stats

    from kazma_core.swarm.memory.graph import get_knowledge_graph

    kg = get_knowledge_graph()
    if not getattr(kg, "available", False):
        stats["errors"] = 1
        stats["error"] = "graph unavailable"
        return stats

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT id, content, metadata, source FROM memories ORDER BY timestamp DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql).fetchall()
        stats["scanned"] = len(rows)

        kg.add_entity("user", "person", {"label": "user", "content": "chat user"})

        for row in rows:
            mid = str(row["id"] or "")
            content = str(row["content"] or "").strip()
            if not mid or not content:
                continue
            try:
                meta = {}
                raw = row["metadata"] or "{}"
                if isinstance(raw, str):
                    try:
                        meta = json.loads(raw) or {}
                    except Exception:
                        meta = {}
                source = str(row["source"] or meta.get("source") or "memory")
                kg.add_entity(
                    mid,
                    "memory_chunk",
                    {
                        "content": content[:2000],
                        "label": content[:80],
                        "source": source,
                    },
                )
                kg.add_relation(
                    "user", mid, "has_memory", {"source": source, "backfill": True}
                )
                stats["nodes"] += 1
                stats["edges"] += 1

                # Heuristic SPO from content
                try:
                    from kazma_core.memory.consolidator import extract_heuristic

                    extracted = extract_heuristic(content, "")
                    for t in extracted.get("triples") or []:
                        if not isinstance(t, dict):
                            continue
                        s = str(t.get("subject") or "").strip()
                        p = str(t.get("predicate") or "").strip()
                        o = str(t.get("object") or "").strip()
                        if s and p and o:
                            kg.upsert_triple(
                                s,
                                p,
                                o,
                                fact=f"{s} {p} {o}"[:240],
                                extra={"source": "backfill_heuristic"},
                            )
                            stats["triples"] += 1
                except Exception:
                    pass
            except Exception:
                stats["errors"] += 1
                logger.debug("[backfill] graph row failed id=%s", mid, exc_info=True)
    finally:
        conn.close()

    try:
        gstats = kg.stats()
        stats["graph_nodes"] = gstats.get("nodes")
        stats["graph_edges"] = gstats.get("edges")
    except Exception:
        pass

    logger.info(
        "[backfill] graph done scanned=%s chunks=%s triples=%s graph=%s/%s",
        stats["scanned"],
        stats["nodes"],
        stats["triples"],
        stats.get("graph_nodes"),
        stats.get("graph_edges"),
    )
    return stats


def run_memory_integrity_backfill(
    *,
    force: bool = False,
    encode: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run L3 timestamp/embedding repair + graph backfill."""
    l3 = backfill_l3_timestamps_and_embeddings(force=force, encode=encode, limit=limit)
    graph = backfill_graph_from_memories(limit=limit)
    return {"l3": l3, "graph": graph}


def maybe_auto_backfill(*, max_rows: int = 500) -> dict[str, Any] | None:
    """Startup helper: run once if L3 looks broken (all ts=0 or no embeddings).

    Returns stats if a run happened, else None.
    """
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        if store.get(_CFG_KEY):
            return None
    except Exception:
        store = None

    path = _memory_db_path()
    if not path.exists():
        return None

    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n,
                  SUM(CASE WHEN timestamp IS NULL OR timestamp = 0 THEN 1 ELSE 0 END) AS ts0,
                  SUM(CASE WHEN embedding IS NULL OR length(embedding) = 0 THEN 1 ELSE 0 END) AS emb0
                FROM memories
                """
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None

    if not row or not row[0]:
        # Empty store — mark done so we don't re-check forever
        try:
            if store is not None:
                store.set(_CFG_KEY, True)
        except Exception:
            pass
        return None

    n, ts0, emb0 = int(row[0]), int(row[1] or 0), int(row[2] or 0)
    # Run if majority of rows lack ts or emb
    need = (ts0 / n) > 0.5 or (emb0 / n) > 0.5
    if not need:
        try:
            if store is not None:
                store.set(_CFG_KEY, True)
        except Exception:
            pass
        return None

    logger.warning(
        "[backfill] Auto-repairing L3 integrity (n=%s ts0=%s emb0=%s, cap=%s)",
        n,
        ts0,
        emb0,
        max_rows,
    )
    result = run_memory_integrity_backfill(force=False, encode=True, limit=max_rows)
    try:
        if store is not None:
            store.set(_CFG_KEY, True)
    except Exception:
        pass
    return result
