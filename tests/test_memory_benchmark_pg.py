"""The retrieval benchmark on the pgvector path (Memory audit M24: "for both
backends"; benchmark audit R7, the owner's decision 5).

Two Postgres measurements, two names:

* **the Postgres-primary keyword path** (``tests/test_memory_pg_primary.py``,
  the Postgres job): the mirror's keyword search with the local vector index;
* **the pgvector path** (this file, the pgvector job): the same memories
  written the product's way, mirrored into Postgres (``kazma_episodes`` /
  ``kazma_beliefs``) with their vectors in pgvector, every question -- the
  tuning set and the second set -- through ``recall()`` with Postgres as the
  primary store: the mirror's keyword search, pgvector's nearest, the same
  evidence ranking.

**Exact against approximate is measured, not assumed** (decision 5): the
questions are asked twice on one seeding, with the product's search (the
planner may answer from the HNSW index: approximate) and with index scans off
on the test's own connection (exact). Both are printed as one
``PGVECTOR_MEASUREMENT`` line and, with ``KAZMA_TEST_MEMORY_BENCH_PG_OUT``,
written to a file; ``scripts/memory_bench.py lock-pgvector`` locks floors from
it, naming the run.

The product's search is held to the lock's ``pgvector`` floors **both ways**
(a drop and an unlocked rise). Floors whose ``status`` is not ``verified``
-- the run made by hand on 2026-10-14, until CI's first run is locked -- make
the test report, not fail: every CI job is a required check, and a job must
not go red before its floors exist. A run that did not measure fails whatever
the status.

Needs ``KAZMA_DATABASE_URL`` (a DISPOSABLE database with the pgvector
extension available: its memory tables are emptied) and
``KAZMA_TEST_ALLOW_REAL_DB=1``. Vectors replay bge-m3's recording, as in CI.
In the pgvector job a skip fails the job (``MUST_RUN`` in
``scripts/postgres_suite.py``).
"""

from __future__ import annotations

import json
import os
import sqlite3
import struct
from pathlib import Path
from urllib.parse import quote

import pytest

pytestmark = pytest.mark.pgvector

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
SLACK = 0.0001
#: The search modes measured: the product's, and every row compared.
MODES = ("approximate", "exact")
#: What makes a connection's search exact: no index scan, so the planner
#: compares every row (this test's connections only; the product is unchanged).
EXACT_OPTIONS = "-c enable_indexscan=off"


def _vec(blob) -> list[float] | None:
    if not blob or len(blob) % 4:
        return None
    return list(struct.unpack(f"{len(blob) // 4}f", bytes(blob)))


def exact_dsn(dsn: str) -> str:
    """*dsn* with index scans off for every statement on its connections."""
    return f"{dsn}{'&' if '?' in dsn else '?'}options={quote(EXACT_OPTIONS)}"


def _locked() -> dict:
    return json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8")).get("pgvector") or {}


def floor_problems(scores: dict[str, float], floors: dict[str, float]) -> list[str]:
    """Where *scores* differ from *floors*, both ways: a drop, a rise not
    locked, or a score the run did not make."""
    problems = []
    for key, floor in sorted(floors.items()):
        got = scores.get(key)
        if got is None:
            problems.append(f"{key}: missing from the run")
        elif got < floor - SLACK:
            problems.append(f"{key}: {got} < {floor} (worse)")
        elif got > floor + SLACK:
            problems.append(f"{key}: {got} > {floor} (better: lock it, naming the run)")
    return problems


def judge(measurement: dict[str, dict[str, float]], locked: dict) -> tuple[list[str], list[str]]:
    """(failures, reports) of one measurement against the lock: floors
    marked verified fail on a difference, other floors only report it. A
    measurement missing a mode or a score fails either way."""
    failures = [f"no {mode} measurement" for mode in MODES if not measurement.get(mode)]
    if failures:
        return failures, []
    mode = locked.get("mode") or "approximate"
    problems = floor_problems(measurement[mode], locked.get("floors") or {})
    if not locked.get("floors"):
        problems = ["no floors locked: python scripts/memory_bench.py lock-pgvector --from <measurement> "
                    "--run \"<the CI run>\""]
    if locked.get("status") == "verified":
        return problems, []
    return [], [f"(unverified floors, {locked.get('measured') or 'no run named'}) {p}" for p in problems]


def test_the_benchmark_on_pgvector_exact_and_approximate(tmp_path, monkeypatch):
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
    exact = PgvectorBackend(dsn=exact_dsn(dsn), dimension=1024)
    assert exact.available, "the exact connection options were refused"

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
        state._ensure()
        pg.execute("TRUNCATE kazma_episodes, kazma_beliefs")
        pg.commit()
    finally:
        pg.close()
    measurement: dict[str, dict[str, float]] = {}
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

        def ask(backend, questions):
            monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend", lambda *_a, **_k: backend)
            results = []
            for q in questions:
                res = recall(q["text"], tenant_id=benchmark.BENCH_TENANT, limit=5)
                injected = ([h.id for h in res.beliefs[:5]] + [h.id for h in res.episodes[:5]]
                            + [h.id for h in res.summaries])
                results.append(benchmark._score(q, refs, injected, covers))
            return results

        for mode, backend in (("approximate", vectors), ("exact", exact)):
            report = benchmark._summarise(ask(backend, data["questions"]), [])
            report["second"] = benchmark._summarise(ask(backend, data["second"]), [])
            measurement[mode] = benchmark.gated_scores(report)
    finally:
        conn.close()
        state.close()
    line = json.dumps(measurement, sort_keys=True)
    print(f"PGVECTOR_MEASUREMENT {line}")
    out = os.environ.get("KAZMA_TEST_MEMORY_BENCH_PG_OUT")
    if out:
        Path(out).write_text(line + "\n", encoding="utf-8")
    differ = {k: [measurement["approximate"][k], v] for k, v in measurement["exact"].items()
              if abs(measurement["approximate"].get(k, -1.0) - v) > SLACK}
    print(f"PGVECTOR_EXACT_AGAINST_APPROXIMATE {json.dumps(differ, sort_keys=True)}")
    failures, reports = judge(measurement, _locked())
    for line in reports:
        print(f"PGVECTOR_REPORT {line}")
    assert not failures, "The pgvector path is not what the lock says:\n  " + "\n  ".join(failures)


# ── The rules, without a database ─────────────────────────────────────────


def test_the_floors_are_compared_both_ways():
    floors = {"overall.hit_rate": 0.9, "overall.mrr": 0.8}
    assert floor_problems(dict(floors), floors) == []
    assert floor_problems({**floors, "overall.hit_rate": 0.85}, floors)[0].endswith("(worse)")
    assert "lock it" in floor_problems({**floors, "overall.mrr": 0.85}, floors)[0]
    assert floor_problems({"overall.mrr": 0.8}, floors)[0].endswith("missing from the run")


def test_unverified_floors_report_and_verified_ones_fail():
    """Negative control: the same lower run fails against verified floors and
    is only reported against unverified ones; a run that did not measure a
    mode fails either way."""
    floors = {"overall.hit_rate": 0.9}
    low = {"approximate": {"overall.hit_rate": 0.8}, "exact": {"overall.hit_rate": 0.8}}
    failures, reports = judge(low, {"status": "unverified", "floors": floors})
    assert failures == [] and "worse" in reports[0]
    failures, reports = judge(low, {"status": "verified", "floors": floors, "mode": "approximate"})
    assert failures and reports == []
    assert judge({"approximate": {"overall.hit_rate": 0.9}}, {"status": "unverified", "floors": floors})[0] == [
        "no exact measurement"]


def test_the_lock_names_its_floors_unverified_until_a_run_is_named():
    locked = _locked()
    assert locked.get("floors"), "the pgvector floors are gone from the lock"
    if locked.get("status") == "verified":
        assert locked.get("run") and locked.get("mode") in MODES
    else:
        assert locked.get("status") == "unverified" and locked.get("measured")


def test_the_exact_connection_turns_index_scans_off():
    assert exact_dsn("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db?options=-c%20enable_indexscan%3Doff"
    assert exact_dsn("postgresql://h/db?sslmode=disable").endswith("&options=-c%20enable_indexscan%3Doff")


def _script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_memory_bench_pg", REPO / "scripts" / "memory_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_floors_are_locked_from_a_named_run(tmp_path, monkeypatch, capsys):
    """``lock-pgvector`` reads the measurement from a CI log, needs the run's
    name, and marks the floors verified; then the same run passes and a
    lower one fails."""
    script = _script()
    lock = tmp_path / "thresholds.json"
    lock.write_text((FIXTURES / "thresholds.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(script, "THRESHOLDS", lock)
    scores = json.loads(lock.read_text(encoding="utf-8"))["scores"]
    measurement = {"approximate": {**scores, "overall.hit_rate": 0.9}, "exact": dict(scores)}
    log = tmp_path / "job.log"
    log.write_text("collected 3 items\nPGVECTOR_MEASUREMENT " + json.dumps(measurement) + "\nPASSED\n",
                   encoding="utf-8")
    assert script._cmd_lock_pgvector(str(log), None, "approximate") == 1  # no run named
    assert "name the run" in capsys.readouterr().err
    assert script._cmd_lock_pgvector(str(log), "CI run 123", "approximate") == 0
    locked = json.loads(lock.read_text(encoding="utf-8"))["pgvector"]
    assert locked["status"] == "verified" and locked["run"] == "CI run 123" and locked["mode"] == "approximate"
    assert locked["floors"]["overall.hit_rate"] == 0.9 and locked["exact_for_the_record"] == scores
    assert judge(measurement, locked) == ([], [])
    lower = {**measurement, "approximate": {**measurement["approximate"], "overall.hit_rate": 0.8}}
    assert judge(lower, locked)[0]
    # Negative control: a measurement of another dataset's scores is refused.
    log.write_text("PGVECTOR_MEASUREMENT " + json.dumps({"approximate": {"x": 1.0}, "exact": {"x": 1.0}}),
                   encoding="utf-8")
    assert script._cmd_lock_pgvector(str(log), "CI run 124", "approximate") == 1
