"""Meaning search narrows past a size, and re-scores every answer exactly
(the memory framework audit's F15, part H; ``memory/vector_codes.py``).

* **Equal benchmark results with narrowing on** (the audit's acceptance):
  the retrieval benchmark, codes made after seeding and narrowing forced on
  (``NARROW_FROM`` 0), answers every question exactly as the full scan does.
  Negative control: the codes' own estimates without the exact re-score
  change the results.
* A code goes with its vector: a changed vector, a deleted row, a row moved
  to another space and a forget (the vector dropped) each take it.
* A row with no code yet is always a candidate; another space's rows never
  are; under ``NARROW_FROM`` every row is read.
* Exact search stays the truth: the first narrowed search and every 50th
  also read every row, and a difference is said and the full answer used.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark, vector_codes, vector_engine  # noqa: E402

VECTORS = Path(__file__).resolve().parent / "fixtures" / "memory_bench" / "vectors.npz"


def _run(*, narrow: bool, rescore: bool = True) -> dict:
    replay = benchmark.ReplayEmbedder(VECTORS)
    real_seed = benchmark._seed

    def seed_and_code(conn, data, *a, **k):
        refs = real_seed(conn, data, *a, **k)
        vector_codes.fill_codes(conn, budget=10**9, seconds=600)
        return refs

    patches = [mock.patch("kazma_core.memory.embedder.get_embedder", lambda: replay),
               mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model),
               mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 300.0)]
    if narrow:
        patches += [mock.patch.object(benchmark, "_seed", seed_and_code),
                    mock.patch.object(vector_codes, "NARROW_FROM", 0),
                    mock.patch.object(vector_engine, "NARROW_AUDIT_EVERY", 10**9)]
        if not rescore:
            patches.append(mock.patch.object(vector_engine.VectorEngine, "_rescore", _estimates_only))
    with contextlib_exit(patches):
        return benchmark.run_benchmark(keep_results=True)


class contextlib_exit:
    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        for p in self.patches:
            p.start()

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def _estimates_only(self, table, predicate, params, rowids, query_vec, limit):
    """The candidates ranked by their codes' estimate, never re-scored: the
    negative control."""
    q = vector_codes._sign_code(np, query_vec)
    marks = ",".join("?" for _ in rowids)
    rows = self.conn.execute(
        f"SELECT t.id, c.code FROM {table} t JOIN memory_vector_codes c ON c.id = t.id "
        f"WHERE t.rowid IN ({marks})", rowids).fetchall()
    out = []
    for rid, code in rows:
        d = int(np.unpackbits(np.bitwise_xor(np.frombuffer(bytes(code), np.uint8), np.frombuffer(q, np.uint8))).sum())
        out.append((str(rid), 1.0 - 2.0 * d / len(query_vec)))
    out.sort(key=lambda x: -x[1])
    return out[:limit]


def _per_question(report: dict) -> dict[str, tuple]:
    rows = report["results"] + report.get("second", {}).get("results", [])
    return {r["id"]: tuple(r.get(k) for k in ("hit", "rr", "precision", "injected", "order")) for r in rows}


@pytest.fixture(scope="module")
def exact() -> dict:
    return _run(narrow=False)


def test_the_benchmark_answers_alike_with_narrowing_on(exact):
    narrowed = _run(narrow=True)
    assert _per_question(narrowed) == _per_question(exact)
    assert benchmark.gated_scores(narrowed) == benchmark.gated_scores(exact)


def test_control_without_the_exact_rescore_the_results_change(exact):
    estimated = _run(narrow=True, rescore=False)
    differ = [q for q, v in _per_question(estimated).items() if _per_question(exact)[q] != v]
    assert len(differ) > 20, differ


# ── the codes ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path):
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(conn)
    yield conn
    conn.close()


def _vec(seed: int, dim: int = 64) -> bytes:
    v = np.random.default_rng(seed).standard_normal(dim).astype(np.float32)
    return (v / np.linalg.norm(v)).tobytes()


def _episode(conn, eid: str, vec: bytes, tenant: str = "default") -> None:
    conn.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
                 "created_at, last_accessed, embedding) VALUES (?, ?, 's', 1, 'q', 'a', 'episodic', 1, 1, ?)",
                 (eid, tenant, vec))
    conn.commit()


def _codes(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM memory_vector_codes")}


def test_a_code_goes_with_its_vector(db):
    for i in range(4):
        _episode(db, f"e{i}", _vec(i))
    assert vector_codes.fill_codes(db)["made"] == 4
    db.execute("UPDATE episodes SET embedding = ? WHERE id = 'e0'", (_vec(99),))
    db.execute("DELETE FROM episodes WHERE id = 'e1'")
    db.execute("UPDATE episodes SET tenant_id = 'other' WHERE id = 'e2'")
    db.execute("UPDATE episodes SET embedding = NULL, user_text = '', tier = 'forgotten' WHERE id = 'e3'")
    db.commit()
    assert _codes(db) == set()
    assert vector_codes.fill_codes(db)["made"] == 2  # e0's new vector, e2 in its space; e3 has none


def test_the_fill_keeps_to_its_budget_and_resumes(db):
    for i in range(30):
        _episode(db, f"e{i}", _vec(i))
    assert vector_codes.fill_codes(db, budget=12)["made"] == 12
    assert vector_codes.fill_codes(db, budget=12) == {"made": 12, "waiting": 6}
    assert vector_codes.fill_codes(db, budget=12) == {"made": 6, "waiting": 0}


def _engine(conn, monkeypatch, *, narrow_from=0):
    monkeypatch.setattr(vector_codes, "NARROW_FROM", narrow_from)
    monkeypatch.setattr(vector_codes, "NARROW_MIN", 5)
    monkeypatch.setattr(vector_codes, "NARROW_FACTOR", 1)
    monkeypatch.setattr(vector_engine, "NARROW_AUDIT_EVERY", 10**9)
    eng = vector_engine.VectorEngine(conn, model="")
    return eng


def test_a_row_with_no_code_yet_is_found_and_another_space_is_not(db, monkeypatch):
    for i in range(200):
        _episode(db, f"e{i}", _vec(i))
    _episode(db, "theirs", _vec(7), tenant="other")
    vector_codes.fill_codes(db)
    _episode(db, "late", _vec(7))  # written after the sweep: no code
    eng = _engine(db, monkeypatch)
    q = np.frombuffer(_vec(7), dtype=np.float32).tolist()
    found = [i for i, _s in eng.search(q, tenant_id="default", tier=None, limit=3)]
    assert found[:2] == sorted(["e7", "late"]) or set(found[:2]) == {"e7", "late"}
    assert "theirs" not in found


def test_under_the_size_every_row_is_read(db, monkeypatch):
    for i in range(50):
        _episode(db, f"e{i}", _vec(i))
    vector_codes.fill_codes(db)
    eng = _engine(db, monkeypatch, narrow_from=10**6)
    called = []
    monkeypatch.setattr(vector_engine.VectorEngine, "_rescore", lambda *a, **k: called.append(1) or [])
    q = np.frombuffer(_vec(3), dtype=np.float32).tolist()
    assert eng.search(q, tenant_id="default", tier=None, limit=3)[0][0] == "e3"
    assert called == []


def test_narrowing_rescores_far_fewer_rows_than_it_holds(db, monkeypatch):
    for i in range(400):
        _episode(db, f"e{i}", _vec(i))
    vector_codes.fill_codes(db)
    eng = _engine(db, monkeypatch)
    seen = []
    real = vector_engine.VectorEngine._rescore

    def spy(self, table, predicate, params, rowids, query_vec, limit):
        seen.append(len(rowids))
        return real(self, table, predicate, params, rowids, query_vec, limit)

    monkeypatch.setattr(vector_engine.VectorEngine, "_rescore", spy)
    q = np.frombuffer(_vec(11), dtype=np.float32).tolist()
    assert eng.search(q, tenant_id="default", tier=None, limit=3)[0][0] == "e11"
    assert seen and seen[0] < 40


def test_the_full_scan_checks_the_narrowing_and_wins(db, monkeypatch):
    for i in range(200):
        _episode(db, f"e{i}", _vec(i))
    vector_codes.fill_codes(db)
    eng = _engine(db, monkeypatch)
    monkeypatch.setattr(vector_engine, "NARROW_AUDIT_EVERY", 1)
    monkeypatch.setattr(vector_engine, "_NARROW_STATE", {"searches": 0, "audited": 0, "differed": 0})
    # A narrowing that loses the answer: the candidates without it.
    real = vector_codes.candidates

    def lossy(*a, **k):
        rowid = db.execute("SELECT rowid FROM episodes WHERE id = 'e5'").fetchone()[0]
        return [r for r in real(*a, **k) if r != rowid]

    monkeypatch.setattr(vector_codes, "candidates", lossy)
    q = np.frombuffer(_vec(5), dtype=np.float32).tolist()
    assert eng.search(q, tenant_id="default", tier=None, limit=3)[0][0] == "e5"
    assert vector_engine.narrowing_health()["differed"] == 1
