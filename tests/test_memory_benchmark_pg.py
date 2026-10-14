"""The retrieval benchmark on the Postgres-primary path (Memory audit M24:
"for both backends").

``tests/test_memory_benchmark.py`` scores the local path (SQLite, exact
search). Here the same memories are written the product's way, mirrored into
Postgres (``kazma_episodes`` / ``kazma_beliefs``, ``mirror_episode_rows`` and
``mirror_belief``) with their vectors in pgvector, and every question -- the
tuning set and the held-out set -- goes through ``recall()`` with Postgres as
the primary store: the mirror's keyword search, pgvector's nearest, the same
evidence ranking. The scores are held to ``thresholds.json``'s
``postgres_primary`` floors (``KAZMA_TEST_MEMORY_BENCH_PG_LOCK=1`` raises
them to a run's scores, never lowers one).

Opt-in: ``KAZMA_DATABASE_URL`` (a DISPOSABLE database with the pgvector
extension available: its memory tables are emptied) and
``KAZMA_TEST_ALLOW_REAL_DB=1``; ``KAZMA_TEST_MEMORY_BENCH_PG_OUT`` writes the
report. Vectors replay bge-m3's recording, as in CI.
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from pathlib import Path

import pytest

pytestmark = pytest.mark.postgres

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
SLACK = 0.0001


def _vec(blob) -> list[float] | None:
    if not blob or len(blob) % 4:
        return None
    return list(struct.unpack(f"{len(blob) // 4}f", bytes(blob)))


def test_the_benchmark_on_postgres_primary(tmp_path, monkeypatch):
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn:
        pytest.skip("no Postgres configured")
    from kazma_core.memory import state_backend as sb
    from kazma_core.memory.backends import PgvectorBackend
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    state = sb.PostgresStateBackend(dsn)
    if not state.available:
        pytest.skip("Postgres not reachable")
    vectors = PgvectorBackend(dsn=dsn, dimension=1024)
    if not vectors.available:
        pytest.skip("pgvector is not available on this database")

    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    db = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()

    data = benchmark.load_dataset()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    pg = state._connect()
    try:
        state._ensure(pg)
        pg.execute("TRUNCATE kazma_episodes, kazma_beliefs")
        pg.commit()
    finally:
        pg.close()
    try:
        ensure_primary_schema(conn)
        refs = benchmark._seed(conn, data)
        covers = benchmark._covers(data, refs)
        # The mirror and the index as the product's writers leave them.
        monkeypatch.setattr(sb, "get_state_backend", lambda: state)
        monkeypatch.setattr(sb, "_this_install_id", lambda: "e" * 32)
        assert sb.remirror_episodes(conn, [r["id"] for r in conn.execute("SELECT id FROM episodes")]) > 1000
        for row in conn.execute("SELECT * FROM beliefs"):
            assert state.mirror_belief(dict(row))
        vpg = vectors._connect() if hasattr(vectors, "_connect") else None
        if vpg is not None:
            try:
                if vpg.execute("SELECT to_regclass(%s)", (vectors._table,)).fetchone()[0]:
                    vpg.execute(f"DELETE FROM {vectors._table} WHERE tenant_id = %s", (benchmark.BENCH_TENANT,))
                vpg.commit()
            finally:
                vpg.close()
        for r in conn.execute("SELECT id, tier, session_id, embedding FROM episodes"):
            vec = _vec(r["embedding"])
            if vec:
                assert vectors.upsert(r["id"], vec, tenant_id=benchmark.BENCH_TENANT,
                                      meta={"kind": "episode", "tier": r["tier"], "session_id": r["session_id"]})
        for r in conn.execute("SELECT id, embedding FROM beliefs"):
            vec = _vec(r["embedding"])
            if vec:
                assert vectors.upsert(r["id"], vec, tenant_id=benchmark.BENCH_TENANT,
                                      meta={"kind": "belief", "tier": "semantic"})
        monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: True)
        monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend", lambda: vectors)

        def ask(questions):
            results = []
            for q in questions:
                res = recall(q["text"], tenant_id=benchmark.BENCH_TENANT, limit=5)
                injected = ([h.id for h in res.beliefs[:5]] + [h.id for h in res.episodes[:5]]
                            + [h.id for h in res.summaries])
                results.append(benchmark._score(q, refs, injected, covers))
            return results

        report = benchmark._summarise(ask(data["questions"]), [])
        report["heldout"] = benchmark._summarise(ask(data["heldout"]), [])
    finally:
        conn.close()
        state.close()
    out = os.environ.get("KAZMA_TEST_MEMORY_BENCH_PG_OUT")
    if out:
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    scores = benchmark.gated_scores(report)
    path = FIXTURES / "thresholds.json"
    locked = json.loads(path.read_text(encoding="utf-8"))
    floor = locked.get("postgres_primary", {})
    if os.environ.get("KAZMA_TEST_MEMORY_BENCH_PG_LOCK") == "1":
        # Raise the floors to this run (never lower one); the dataset version
        # is the main ratchet's.
        floor = {k: max(v, floor.get(k, -1.0)) for k, v in scores.items()}
        locked["postgres_primary"] = dict(sorted(floor.items()))
        path.write_text(json.dumps(locked, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if not floor:
        pytest.fail("no postgres_primary floors yet: run once with KAZMA_TEST_MEMORY_BENCH_PG_LOCK=1")
    dropped = [f"{k}: {scores.get(k)} < {v}" for k, v in floor.items() if scores.get(k, -1.0) < v - SLACK]
    assert not dropped, "Postgres-primary recall got worse:\n  " + "\n  ".join(dropped)
