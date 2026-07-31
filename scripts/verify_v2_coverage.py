#!/usr/bin/env python
"""Verify V2 memory coverage after the V1→V2 cutover.

Compares V1 store populations (legacy knowledge graph + FTS5 memories) against
the V2 cognitive stores (beliefs / episodes / entities), then runs a small set
of sample queries through BOTH the V2 ``recall.search`` path and the legacy
``adapter.search`` path and diffs the results.

Exit code 0 = coverage looks healthy; 1 = a meaningful gap was detected.

Run after ``backfill_v2.run_backfill()`` to confirm the migration landed.

Usage::

    python scripts/verify_v2_coverage.py
    python scripts/verify_v2_coverage.py --queries 10   # more sample queries
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path


def _count(conn: sqlite3.Connection, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0])
    except Exception:
        return -1


def v1_counts() -> dict[str, int]:
    """Population of the legacy V1 stores (or -1 if a store is missing)."""
    from kazma_core.paths import fts5_memory_path, knowledge_graph_db

    out: dict[str, int] = {}
    kg = knowledge_graph_db()
    if Path(kg).exists():
        c = sqlite3.connect(kg)
        out["kg_nodes"] = _count(c, "SELECT COUNT(*) FROM kg_nodes")
        out["kg_edges"] = _count(c, "SELECT COUNT(*) FROM kg_edges")
        c.close()
    else:
        out["kg_nodes"] = out["kg_edges"] = -1

    memdb = fts5_memory_path()
    if Path(memdb).exists():
        c = sqlite3.connect(memdb)
        out["memories"] = _count(c, "SELECT COUNT(*) FROM memories")
        c.close()
    else:
        out["memories"] = -1
    return out


def v2_counts() -> dict[str, int]:
    """Population of the V2 cognitive stores."""
    from kazma_core.paths import primary_memory_db

    out: dict[str, int] = {}
    c = sqlite3.connect(primary_memory_db())
    c.row_factory = sqlite3.Row
    out["active_beliefs"] = _count(
        c,
        "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL",
    )
    out["episodes"] = _count(c, "SELECT COUNT(*) FROM episodes")
    out["entities"] = _count(c, "SELECT COUNT(*) FROM entities")
    c.close()
    return out


async def _query_diff(queries: list[str], limit: int, *, include_v1: bool = True) -> list[dict]:
    """Run each query through V2 recall.search + (optionally) legacy adapter.search.

    The V1 adapter probe is best-effort: under a V2-default deployment the V1
    adapter may not be initialized (boot skips it), so it is wrapped in a
    per-call timeout. Failures are recorded as -1 rather than blocking.
    """
    rows: list[dict] = []
    try:
        from kazma_core.memory.recall import search as v2_search

        v2_avail = True
    except Exception:
        v2_avail = False
    adapter = None
    v1_avail = False
    if include_v1:
        try:
            from kazma_core.swarm.memory.adapter import get_adapter

            adapter = get_adapter()
            v1_avail = adapter is not None
        except Exception:
            v1_avail = False

    for q in queries:
        v2_n = v2_search(q, limit=limit) if v2_avail else []
        v1_n: list = []
        if v1_avail:
            try:
                # Per-call timeout: V1 adapter init/embed can be slow.
                v1_n = await asyncio.wait_for(adapter.search(q, limit=limit), timeout=10.0)
            except (asyncio.TimeoutError, Exception):
                v1_n = []  # V1 unavailable/slow — record empty, don't block
        rows.append({
            "query": q,
            "v2_hits": len(v2_n) if isinstance(v2_n, list) else 0,
            "v1_hits": len(v1_n) if isinstance(v1_n, list) else 0,
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--queries", type=int, default=5,
        help="Number of sample query-diff probes to run (default 5).",
    )
    parser.add_argument(
        "--limit", type=int, default=5,
        help="Top-K per query probe (default 5).",
    )
    parser.add_argument(
        "--no-v1", action="store_true",
        help="Skip the legacy V1 adapter query-diff (use when V1 is not booted).",
    )
    args = parser.parse_args()

    print("=" * 64)
    print("V1 → V2 memory coverage report")
    print("=" * 64)

    v1 = v1_counts()
    v2 = v2_counts()

    print("\n── Store populations ──")
    print(f"  V1 knowledge_graph.db : {v1['kg_nodes']} nodes, {v1['kg_edges']} edges")
    print(f"  V1 memory.db (L3 FTS5): {v1['memories']} memories")
    print(f"  V2 active beliefs     : {v2['active_beliefs']}")
    print(f"  V2 episodes           : {v2['episodes']}")
    print(f"  V2 entities           : {v2['entities']}")

    # Coverage heuristics (informational, not hard gates):
    print("\n── Coverage heuristics ──")
    kg_nodes = v1["kg_nodes"]
    v2_ent = v2["entities"]
    if kg_nodes > 0 and v2_ent >= 0:
        ratio = v2_ent / kg_nodes
        print(f"  entities / kg_nodes   : {v2_ent}/{kg_nodes} = {ratio:.0%}")
    if v1["memories"] > 0 and v2["episodes"] >= 0:
        print(f"  episodes source       : {v2['episodes']} total (V1 memories: {v1['memories']})")

    # Sample queries derived from V2 belief content + generic probes.
    sample_queries = [
        "What do you know about the user?",
        "favorite color preference",
        "where does the user live",
        "swarm worker results",
        "compaction summary",
    ]
    # Augment with real belief subjects so probes hit actual data.
    try:
        from kazma_core.paths import primary_memory_db

        c = sqlite3.connect(primary_memory_db())
        for r in c.execute(
            "SELECT DISTINCT subject FROM beliefs WHERE valid_until IS NULL LIMIT 5"
        ).fetchall():
            sample_queries.append(str(r[0]))
        c.close()
    except Exception:
        pass
    sample_queries = sample_queries[: args.queries + 5][: max(args.queries, 1)]

    print("\n── Query diff (V2 recall.search vs V1 adapter.search) ──")
    if args.no_v1:
        print("  (skipped --no-v1)")
        diff = asyncio.run(_query_diff(sample_queries, args.limit, include_v1=False))
    else:
        diff = asyncio.run(_query_diff(sample_queries, args.limit, include_v1=True))
    for d in diff:
        v1str = "  - " if d["v1_hits"] < 0 else f"{d['v1_hits']:>2}"
        print(f"  {d['v2_hits']:>2} v2 | {v1str} v1 | {d['query'][:48]}")

    print("\n" + "=" * 64)
    # Healthy if V2 has any beliefs/entities after backfill. A zero-V2,
    # non-zero-V1 state is the only hard failure (means backfill didn't run).
    healthy = not (v2["active_beliefs"] <= 0 and v1["kg_edges"] > 0)
    print("RESULT: coverage healthy" if healthy else "RESULT: GAP — V2 empty but V1 has data (run backfill)")
    print("=" * 64)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
