"""The Postgres-primary recall path answers part F's questions as the local
path does, without a Postgres (CI's main job; the real-Postgres benchmark is
``tests/test_memory_pg_primary.py``, the Postgres job).

From part F (2026-11-21) to the fix the primary path skipped every step part
F added after ranking: a question about a period answered with today's facts
and turns of other months ("Where was I living in June?" -> today's home),
"at first" showed no earlier value, a list question lost the list's
siblings, and a turn's follow-ups came as separate memories. CI's Postgres
job measured MRR 0.6889 against local 0.7133 (run 44886678495).

Here the mirror is a stand-in over this install's own rows (the sync pass
copies exactly those), answering the way ``PostgresStateBackend`` does:
content words with ``LIKE`` prefilters confirmed as whole words, most words
held first, current facts only. The vector index is the local one with the
benchmark's recorded vectors. The benchmark's time, list, follow-up and
roll-up questions must inject exactly what local recall injects. Negative
control: the primary path without the local steps does not.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import state_backend as sb  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "memory_bench"
#: The categories part F and part G added (benchmark dataset v6, v7).
PART_F_CATEGORIES = ("temporal", "list", "follow_up", "rollup")


class _Mirror:
    """A stand-in Postgres mirror over the local database's rows."""

    name = "fake_mirror"
    write_ready = True
    available = True

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _episode_rows(self, tenant_id: str, between: tuple[float, float] | None = None) -> list[dict]:
        from kazma_core.memory.vector_engine import RECALLABLE_TIERS

        sql = (f"SELECT id, session_id, turn_number, user_text, assistant_text, summary_text, tier, "
               f"structural_importance, created_at, metadata_json FROM episodes WHERE tenant_id = ? "
               f"AND tier IN ({','.join('?' for _ in RECALLABLE_TIERS)})")
        params: list[Any] = [tenant_id, *RECALLABLE_TIERS]
        if between is not None:
            sql += " AND created_at >= ? AND created_at < ?"
            params += [between[0], between[1]]
        return [dict(r) for r in self.conn.execute(sql, params)]

    def search_episodes(self, query, *, tenant_id="default", limit=10, strict=False, between=None):
        from kazma_core.memory.query_terms import mentions, search_terms

        terms = search_terms(query)[:8]
        if not terms:
            return []
        out = []
        for row in self._episode_rows(tenant_id, between):
            text = " ".join(str(row.get(k) or "") for k in ("user_text", "assistant_text", "summary_text")).lower()
            held = sum(1 for t in terms if t in text)
            if held:
                out.append((held, float(row["created_at"] or 0), row))
        out.sort(key=lambda x: (x[0], x[1]), reverse=True)
        rows = [r for _h, _c, r in out[: max(1, min(int(limit), 50)) * 5]]
        rows = [r for r in rows if mentions(" ".join(str(r.get(k) or "") for k in
                                                     ("user_text", "assistant_text", "summary_text")), terms)]
        return rows[: max(1, min(int(limit), 50))]

    def episodes_between(self, start, end, *, tenant_id, limit=50):
        rows = self._episode_rows(tenant_id, (start, end))
        rows.sort(key=lambda r: float(r["created_at"] or 0), reverse=True)
        return rows[:limit]

    def _belief_rows(self, tenant_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT id, subject, predicate, object, predicate_type, confidence, structural_importance, "
            "source_trust_weight, valid_from, metadata_json FROM beliefs WHERE tenant_id = ? "
            "AND valid_until IS NULL AND invalidated_at IS NULL", (tenant_id,))]

    def search_beliefs(self, query, *, tenant_id="default", limit=10, strict=False):
        from kazma_core.memory.query_terms import mentions, search_terms

        terms = search_terms(query)[:8]
        if not terms:
            return []
        out = []
        for row in self._belief_rows(tenant_id):
            text = " ".join(str(row.get(k) or "") for k in ("subject", "predicate", "object")).lower()
            held = sum(1 for t in terms if t in text)
            if held:
                standing = float(row["structural_importance"] or 0) * float(row["confidence"] or 0)
                out.append((held, standing, row))
        out.sort(key=lambda x: (x[0], x[1]), reverse=True)
        rows = [r for _h, _s, r in out[: max(1, min(int(limit), 50)) * 5]]
        rows = [r for r in rows if mentions(" ".join(str(r.get(k) or "") for k in
                                                     ("subject", "predicate", "object")), terms)]
        return rows[: max(1, min(int(limit), 50))]

    def fetch_episodes(self, ids, *, tenant_id="default", strict=False):
        wanted = {str(i) for i in ids[:50]}
        return [r for r in self._episode_rows(tenant_id) if str(r["id"]) in wanted]

    def fetch_beliefs(self, ids, *, tenant_id="default", strict=False):
        wanted = {str(i) for i in ids[:50]}
        return [r for r in self._belief_rows(tenant_id) if str(r["id"]) in wanted]


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    """The benchmark seeded into this test's own memory database, which the
    primary path's local steps read too (``KAZMA_MEMORY_STATE_DB``)."""
    from kazma_core.memory import benchmark
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    db = tmp_path / "bench.db"
    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    data = benchmark.load_dataset()
    refs = benchmark._seed(conn, data)
    yield conn, data, refs
    conn.close()


def _injected(conn, data, *, primary: bool, monkeypatch) -> dict[str, list[str]]:
    from kazma_core.memory import benchmark
    from kazma_core.memory.backends import LocalSqliteVectorBackend
    from kazma_core.memory.recall import recall

    with monkeypatch.context() as mp:
        mp.setattr(sb, "is_state_primary", lambda cfg=None: primary)
        if primary:
            mirror = _Mirror(conn)
            mp.setattr(sb, "get_state_backend", lambda: mirror)
            mp.setattr("kazma_core.memory.backends.get_vector_backend",
                       lambda conn_=None, **_k: LocalSqliteVectorBackend(conn))
        out = {}
        for q in data["questions"]:
            if q.get("category") not in PART_F_CATEGORIES:
                continue
            res = recall(q["text"], conn=conn, tenant_id=benchmark.BENCH_TENANT, limit=5)
            out[q["id"]] = ([h.id for h in res.beliefs[:5]] + [h.id for h in res.episodes[:5]]
                            + [h.id for h in res.summaries])
        return out


def test_the_primary_path_answers_part_fs_questions_as_local_does(seeded, monkeypatch):
    conn, data, _refs = seeded
    local = _injected(conn, data, primary=False, monkeypatch=monkeypatch)
    primary = _injected(conn, data, primary=True, monkeypatch=monkeypatch)
    assert len(local) >= 20
    assert {q: v for q, v in primary.items() if v != local[q]} == {}


def test_control_without_the_local_steps_the_primary_path_answers_otherwise(seeded, monkeypatch):
    from tests.test_memory_pg_primary import _without_the_local_steps

    conn, data, _refs = seeded
    local = _injected(conn, data, primary=False, monkeypatch=monkeypatch)
    _without_the_local_steps(monkeypatch)
    primary = _injected(conn, data, primary=True, monkeypatch=monkeypatch)
    differ = {q for q in local if primary[q] != local[q]}
    assert {"q1306", "q1308", "q1309"} <= differ, sorted(differ)
