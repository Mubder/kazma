"""Memory at 10x and 100x the live size: the measured limits (Memory audit
M31, MEM-L16).

Opt-in, like the documents restore drill at scale:

* ``KAZMA_TEST_MEMORY_SCALE=1,10,100`` -- multiples of the live size (about
  1,700 memories on 2026-10-13; facts were not counted that day, so as many
  facts as memories are assumed);
* ``KAZMA_DATABASE_URL`` + ``KAZMA_TEST_ALLOW_REAL_DB=1`` for the mirror half
  (a DISPOSABLE database: its ``kazma_episodes`` / ``kazma_beliefs`` are
  emptied);
* ``KAZMA_TEST_MEMORY_SCALE_OUT`` -- where the JSON report is written.

Each size is seeded into a private memory database the product's shape
(1024-dimension vectors, the full-text index, the mirror's triggers) and
timed: a whole recall, exact meaning search over every memory, a keyword
search, a macro-sleep pass, and on Postgres the mirror -- a full comparison
with nothing to push, an incremental pass with 0 and 100 changes, and 100
pushes pooled against a connection each. ``LIMITS`` are the limits the guide
publishes ("Measured limits" in ``docs/docs/guide/memory-and-rag.md``); a run
over them fails. The machine is recorded in the report.
"""

from __future__ import annotations

import json
import os
import platform
import random
import sqlite3
import statistics
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow]

LIVE_MEMORIES = 1700
LIVE_FACTS = 1700
DIM = 1024
#: Text is drawn from a 20,000-word vocabulary with a Zipf distribution, as
#: language is: a few dozen words in every row made every full-text doclist hold
#: every row, and an archive move (it rewrites the summary in the index) cost
#: seconds at 10x -- a property of the generator, not of memory.
_VOCAB = 20_000
_SYLLABLES = ("ka", "zu", "mi", "ro", "ta", "ne", "lo", "sa", "di", "fu", "ba", "qe", "wi", "ho", "ja", "pe")
_ARABIC = ("موعد", "الميزانية", "رحلة", "النادي", "خزانة", "تقرير", "اجتماع", "مشروع", "قهوة", "مدرسة",
           "طبيب", "فندق", "رحلات", "جدول", "تذكير", "نسخة", "خادم", "تغريدة", "مسودة", "مبيعات")


def _vocabulary() -> list[str]:
    words = list(_ARABIC)
    i = 0
    while len(words) < _VOCAB:
        n, w = i, ""
        for _ in range(3):
            w += _SYLLABLES[n % len(_SYLLABLES)]
            n //= len(_SYLLABLES)
        words.append(w + str(i // 4096) if i >= 4096 else w)
        i += 1
    return words


#: Seconds; the published limits, about twice what was measured on 2026-10-14
#: (the guide's table). A size not listed is measured, never judged.
LIMITS = {
    1: {"recall": 1.0, "vector_search": 0.25, "keyword_search": 0.05, "macro_sleep": 5.0,
        "mirror_incremental_0": 0.5, "mirror_incremental_100": 2.0, "mirror_full": 5.0},
    10: {"recall": 2.5, "vector_search": 1.5, "keyword_search": 0.1, "macro_sleep": 30.0,
         "mirror_incremental_0": 0.5, "mirror_incremental_100": 2.0, "mirror_full": 30.0},
    100: {"recall": 15.0, "vector_search": 4.0, "keyword_search": 0.5, "macro_sleep": 150.0,
          "mirror_incremental_0": 0.5, "mirror_incremental_100": 2.0, "mirror_full": 30.0},
}


def _sizes() -> list[int]:
    raw = os.environ.get("KAZMA_TEST_MEMORY_SCALE", "").strip()
    if not raw:
        pytest.skip("opt-in: set KAZMA_TEST_MEMORY_SCALE=1,10,100")
    return [int(x) for x in raw.split(",") if x.strip()]


def _texts(nrng, vocab: list[str], rows: int, words: int) -> list[str]:
    ranks = nrng.zipf(1.15, size=rows * words) % len(vocab)
    flat = [vocab[r] for r in ranks.tolist()]
    return [" ".join(flat[i * words:(i + 1) * words]) for i in range(rows)]


def _vec(np, rng) -> bytes:
    v = rng.standard_normal(DIM).astype(np.float32)
    return (v / float(np.linalg.norm(v))).tobytes()


def _seed(path: Path, factor: int) -> tuple[int, int]:
    import numpy as np

    from kazma_core.memory.schema_v2 import ensure_primary_schema

    n_ep, n_bel = LIVE_MEMORIES * factor, LIVE_FACTS * factor
    rng, nrng = random.Random(factor), np.random.default_rng(factor)
    vocab = _vocabulary()
    conn = sqlite3.connect(str(path))
    ensure_primary_schema(conn)
    now = time.time()
    year = 365 * 86400
    batch = 5000
    for start in range(0, n_ep, batch):
        rows = []
        end = min(n_ep, start + batch)
        questions, answers = _texts(nrng, vocab, end - start, 30), _texts(nrng, vocab, end - start, 90)
        for i in range(start, end):
            created = now - rng.random() * year
            rows.append((f"ep{i}", f"chat{i // 6}", i % 6, questions[i - start], answers[i - start],
                         rng.choice(("episodic", "episodic", "recall", "working")), created,
                         created, _vec(np, nrng)))
        conn.executemany(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
            "created_at, last_accessed, embedding, embedding_model_version) "
            "VALUES (?, 'default', ?, ?, ?, ?, ?, ?, ?, ?, NULL)", rows)
        conn.commit()
    for start in range(0, n_bel, batch):
        rows = []
        end = min(n_bel, start + batch)
        objects = _texts(nrng, vocab, end - start, 6)
        for i in range(start, end):
            created = now - rng.random() * year
            rows.append((f"b{i}", f"subject_{i % (n_bel // 4 + 1)}", rng.choice(("likes", "works_at", "owns")),
                         objects[i - start], created, created, _vec(np, nrng)))
        conn.executemany(
            "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
            "structural_importance, valid_from, ingested_at, embedding) "
            "VALUES (?, 'default', ?, ?, 'set', ?, 0.8, 1, ?, ?, ?)", rows)
        conn.commit()
    conn.execute("DELETE FROM memory_mirror_outbox")
    conn.commit()
    conn.close()
    return n_ep, n_bel


def _median(fn, runs: int = 5) -> float:
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


class _Embedder:
    dim = DIM

    def __init__(self) -> None:
        import numpy as np

        self._v = np.random.default_rng(7).standard_normal(DIM).astype(np.float32).tolist()

    def encode(self, text):
        return self._v


def _machine(pg_version: str | None) -> dict:
    import numpy as np
    import psutil
    import sqlite_vec

    return {
        "cpu": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 2 ** 30, 1),
        "os": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "sqlite_vec": getattr(sqlite_vec, "__version__", "?"),
        "numpy": np.__version__,
        "postgres": pg_version,
        "priority": "below normal (as the test runner runs)",
    }


def _mirror_half(db: Path, dsn: str, monkeypatch) -> dict:
    from kazma_core.memory import state_backend as sb

    backend = sb.PostgresStateBackend(dsn)
    if not backend.available:
        pytest.skip("Postgres not reachable")
    monkeypatch.setattr(sb, "get_state_backend", lambda: backend)
    monkeypatch.setattr(sb, "mirrors_to_postgres", lambda cfg=None: True)
    monkeypatch.setattr(sb, "_this_install_id", lambda: "d" * 32)
    out: dict = {}
    conn = sqlite3.connect(str(db))
    pg = backend._connect()
    try:
        backend._ensure(pg)
        pg.execute("TRUNCATE kazma_episodes, kazma_beliefs")
        pg.commit()
        # The mirror as a steady state: every row already there (a direct copy).
        cur = pg.cursor()
        ep_rows = [(r[0], "default", r[1], r[2], sb._pg_text(r[3]), sb._pg_text(r[4]), sb._pg_text(r[5]),
                    r[6], r[7], sb._stamp_region_meta({}))
                   for r in conn.execute("SELECT id, session_id, turn_number, user_text, assistant_text, "
                                         "summary_text, tier, created_at FROM episodes")]
        cur.executemany("INSERT INTO kazma_episodes (id, tenant_id, session_id, turn_number, user_text, "
                        "assistant_text, summary_text, tier, structural_importance, created_at, metadata_json) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)", ep_rows)
        bel_rows = [(r[0], "default", r[1], r[2], r[3], r[4], sb._stamp_region_meta({}))
                    for r in conn.execute("SELECT id, subject, predicate, object, valid_from FROM beliefs")]
        cur.executemany("INSERT INTO kazma_beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
                        "confidence, structural_importance, valid_from, metadata_json) "
                        "VALUES (%s, %s, %s, %s, 'set', %s, 0.8, 1, %s, %s)", bel_rows)
        pg.commit()
        out["postgres"] = pg.execute("SELECT version()").fetchone()[0].split(",")[0]
        # The copy holds every change (macro sleep's moves queued theirs).
        conn.execute("DELETE FROM memory_mirror_outbox")
        conn.commit()
    finally:
        pg.close()
    try:
        t0 = time.perf_counter()
        full = sb.sync_state_mirror(conn, full=True)
        out["mirror_full"] = time.perf_counter() - t0
        assert full["episodes_pushed"] == 0 and full["beliefs_pushed"] == 0, full
        out["mirror_incremental_0"] = _median(lambda: sb.sync_state_mirror(conn, full=False), runs=3)

        def changed_100():
            ids = [r[0] for r in conn.execute("SELECT id FROM episodes ORDER BY random() LIMIT 100")]
            conn.executemany("UPDATE episodes SET user_text = user_text || ' x' WHERE id = ?", [(i,) for i in ids])
            conn.commit()
            t0 = time.perf_counter()
            stats = sb.sync_state_mirror(conn, full=False)
            assert stats["episodes_pushed"] == 100, stats
            return time.perf_counter() - t0

        out["mirror_incremental_100"] = statistics.median(changed_100() for _ in range(3))
        # 100 pushes on a connection each (the old shape) against the pool.
        rows = [sb.episode_mirror_row(conn, f"ep{i}") for i in range(100)]
        t0 = time.perf_counter()
        for row in rows:
            backend.mirror_episode(row)
        out["push_100_pooled"] = time.perf_counter() - t0
        monkeypatch.setattr(backend, "_get_pool", lambda: None)
        t0 = time.perf_counter()
        for row in rows:
            backend.mirror_episode(row)
        out["push_100_connection_each"] = time.perf_counter() - t0
    finally:
        conn.close()
        backend.close()
    return out


def test_memory_at_scale(tmp_path, monkeypatch):
    sizes = _sizes()
    from kazma_core.memory import embedder as embedder_mod
    from kazma_core.memory import state_backend as sb
    from kazma_core.memory.macro_sleep import run_macro_sleep
    from kazma_core.memory.recall import _fts_match_query, recall
    from kazma_core.memory.vector_engine import VectorEngine

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    fake = _Embedder()
    monkeypatch.setattr(embedder_mod, "get_embedder", lambda: fake)
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    report: dict = {"live_memories": LIVE_MEMORIES, "live_facts_assumed": LIVE_FACTS, "sizes": {}}
    pg_version = None
    failures = []
    for factor in sizes:
        db = tmp_path / f"memory_state_{factor}x.db"
        t0 = time.perf_counter()
        n_ep, n_bel = _seed(db, factor)
        row: dict = {"memories": n_ep, "facts": n_bel, "seed_s": round(time.perf_counter() - t0, 1),
                     "db_mb": round(db.stat().st_size / 2 ** 20)}
        monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
        sb._last_sync.clear()
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            engine = VectorEngine(conn, model="")
            q = fake.encode("")
            row["vector_search"] = _median(lambda: engine.search(q, tier=None, limit=20))
            numpy_engine = VectorEngine(conn, model="")
            numpy_engine.has_sqlite_vec = False
            row["vector_search_numpy"] = _median(lambda: numpy_engine.search(q, tier=None, limit=20), runs=3)
            vocab = _vocabulary()
            # Words of middling frequency, and an Arabic pair from the head.
            match = _fts_match_query(f"{vocab[40]} {vocab[700]} {vocab[6000]}")
            row["keyword_search"] = _median(lambda: conn.execute(
                "SELECT rowid FROM episodes_fts WHERE episodes_fts MATCH ? ORDER BY bm25(episodes_fts) LIMIT 20",
                (match,)).fetchall())
            questions = [f"what about {vocab[40]} and {vocab[700]}", "متى موعد الميزانية للرحلة",
                         f"remind me of {vocab[6000]} {vocab[12000]}"]
            row["recall"] = statistics.median(
                _median(lambda qq=qq: recall(qq, conn=conn, local_only=True), runs=3) for qq in questions)
            t0 = time.perf_counter()
            moved = run_macro_sleep(conn, tenant_id="default")
            row["macro_sleep"] = time.perf_counter() - t0
            row["macro_sleep_moves"] = {k: v for k, v in moved.items() if isinstance(v, int)}
        finally:
            conn.close()
        if dsn:
            mirror = _mirror_half(db, dsn, monkeypatch)
            pg_version = mirror.pop("postgres", pg_version)
            row.update(mirror)
        for key, limit in LIMITS.get(factor, {}).items():
            if key in row and row[key] > limit:
                failures.append(f"{factor}x {key}: {row[key]:.3f} s over the published {limit} s")
        report["sizes"][f"{factor}x"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row.items()}
        db.unlink()
    report["machine"] = _machine(pg_version)
    out = os.environ.get("KAZMA_TEST_MEMORY_SCALE_OUT")
    if out:
        Path(out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    assert not failures, failures
