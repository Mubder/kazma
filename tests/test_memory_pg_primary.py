"""Postgres-primary memory keeps what the user took back, and finds what was
asked (Memory audit M06, MEM-L02 and MEM-R11).

With Postgres as the primary store, recall reads the shared mirror. Three
gaps there:

* a forget whose mirror write failed (a blip, the database away) left the
  mirror's copy whole, and nothing ever repaired it: the sync pass skipped
  primary mode ("postgres is the primary store"), so recall kept answering
  with a memory the user had forgotten;
* the mirror's keyword channel read every tier, a forgotten one included;
* it took the newest rows holding ANY of the question's words (``ORDER BY
  created_at DESC LIMIT n``), so an older memory only its words would find
  fell out of the slice once newer rows shared one word.

Held here on a real Postgres (``@pytest.mark.postgres``: needs
``KAZMA_DATABASE_URL`` + ``KAZMA_TEST_ALLOW_REAL_DB=1``, a disposable database),
each with its negative control: the forget with the mirror write failing,
recall leaving it out from the local truth, one sync pass repairing the
mirror; the tier filter; the relevance-ordered channel against the old
recency slice, for memories and facts.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid

import pytest

from kazma_core.memory import state_backend as sb

pytestmark = pytest.mark.postgres

WORDS = "zanzibar quokka festival"


@pytest.fixture()
def pg():
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn:
        pytest.skip("no Postgres configured")
    backend = sb.PostgresStateBackend(dsn)
    if not backend.available:
        pytest.skip("Postgres not reachable")
    tenant = f"m06_{uuid.uuid4().hex[:10]}"
    yield backend, tenant
    conn = backend._connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM kazma_episodes WHERE tenant_id = %s", (tenant,))
        cur.execute("DELETE FROM kazma_beliefs WHERE tenant_id = %s", (tenant,))
        conn.commit()
    finally:
        conn.close()
        backend.close()  # its connection pool (Memory audit M31)


@pytest.fixture()
def local(tmp_path, monkeypatch, pg):
    """This install's memory database, with Postgres as the primary store."""
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    backend, tenant = pg
    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr(sb, "get_state_backend", lambda: backend)
    monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: True)
    monkeypatch.setattr(sb, "_this_install_id", lambda: "c" * 32)
    sb._last_sync.clear()
    dual_write._reset_mirror()
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    yield conn, backend, tenant
    dual_write._reset_mirror()
    conn.close()


def _local_episode(conn, eid: str, tenant: str, question: str, *, turn: int = 1, age: float = 0.0) -> None:
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json) VALUES (?, ?, 'chat-m06', ?, ?, 'Noted.', 'episodic', ?, '{}')",
        (eid, tenant, turn, question, time.time() - age),
    )
    conn.commit()


def _remote_episode(backend, tenant: str, eid: str, question: str, *, tier: str = "episodic",
                    created: float) -> None:
    conn = backend._connect()
    try:
        backend._ensure(conn)
        conn.cursor().execute(
            "INSERT INTO kazma_episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
            "summary_text, tier, structural_importance, created_at, metadata_json) "
            "VALUES (%s, %s, 'other-chat', 1, %s, 'Noted.', NULL, %s, 1, %s, '{}')",
            (eid, tenant, question, tier, created),
        )
        conn.commit()
    finally:
        conn.close()


def _remote_tier(backend, eid: str) -> tuple[str, str]:
    conn = backend._connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT tier, COALESCE(user_text, '') FROM kazma_episodes WHERE id = %s", (eid,))
        return tuple(cur.fetchone())
    finally:
        conn.close()


def _recalled(query: str, tenant: str) -> set[str]:
    from kazma_core.memory.recall import recall

    return {h.id for h in recall(query, tenant_id=tenant, limit=5).episodes}


# ── a forget the mirror missed ────────────────────────────────────────────


def test_a_forget_the_mirror_missed_is_left_out_and_repaired_by_one_sync(local, monkeypatch):
    from kazma_core.memory import forget, recall

    conn, backend, tenant = local
    eid = f"e_m06_{uuid.uuid4().hex[:8]}"
    _local_episode(conn, eid, tenant, f"Remember the {WORDS} in March")
    assert sb.sync_state_mirror(conn)["episodes_pushed"] >= 1
    assert eid in _recalled(WORDS, tenant)  # the mirror serves it

    # The forget's mirror write fails (the database blipped).
    real = sb.PostgresStateBackend.mirror_episode
    monkeypatch.setattr(sb.PostgresStateBackend, "mirror_episode", lambda self, row: False)
    assert forget.forget_episode(eid, tenant_id=tenant, conn=conn)["ok"]
    monkeypatch.setattr(sb.PostgresStateBackend, "mirror_episode", real)
    assert _remote_tier(backend, eid)[0] == "episodic"  # the mirror still holds it whole

    # Recall leaves it out: the local store is the truth for what was forgotten.
    assert eid not in _recalled(WORDS, tenant)
    with monkeypatch.context() as m:  # negative control: without the local check it comes back
        m.setattr(recall, "_locally_forgotten", lambda rows, *, tenant_id: set())
        assert eid in _recalled(WORDS, tenant)

    # One sync pass, in primary mode, repairs the mirror.
    stats = sb.sync_state_mirror(conn)
    assert "skipped" not in stats and stats["episodes_pushed"] >= 1
    assert _remote_tier(backend, eid) == ("forgotten", "")
    assert backend.search_episodes(WORDS, tenant_id=tenant) == []
    assert backend.fetch_episodes([eid], tenant_id=tenant) == []
    assert sb.sync_state_mirror(conn)["episodes_pushed"] == 0  # converged


def test_a_turn_the_ledger_refuses_is_left_out_whoever_wrote_it(local, monkeypatch):
    """Another replica's copy of a turn this install forgot: the mirror holds
    it under an id this database never had; the forget ledger names the turn."""
    from kazma_core.memory import forget, recall

    conn, backend, tenant = local
    question = f"What did I say about the {WORDS}?"
    mine = f"e_m06_{uuid.uuid4().hex[:8]}"
    _local_episode(conn, mine, tenant, question)
    assert forget.forget_episode(mine, tenant_id=tenant, conn=conn)["ok"]
    theirs = f"e_m06_r_{uuid.uuid4().hex[:8]}"
    rc = backend._connect()
    try:
        rc.cursor().execute(
            "INSERT INTO kazma_episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
            "summary_text, tier, structural_importance, created_at, metadata_json) "
            "VALUES (%s, %s, 'chat-m06', 1, %s, 'Noted.', NULL, 'episodic', 1, %s, %s)",
            (theirs, tenant, question, time.time(), json.dumps({"install": "d" * 32})),
        )
        rc.commit()
    finally:
        rc.close()
    assert theirs in {r["id"] for r in backend.search_episodes(WORDS, tenant_id=tenant)}
    assert theirs not in _recalled(WORDS, tenant)
    with monkeypatch.context() as m:  # negative control: the ledger is what keeps it out
        m.setattr(recall, "_locally_forgotten", lambda rows, *, tenant_id: set())
        assert theirs in _recalled(WORDS, tenant)


# ── the tier filter ───────────────────────────────────────────────────────


def test_a_forgotten_or_unrecallable_tier_is_never_read(pg):
    backend, tenant = pg
    now = time.time()
    for tier in ("forgotten", "rebuild", "retired"):
        _remote_episode(backend, tenant, f"e_m06_{tier}_{uuid.uuid4().hex[:6]}", f"the {WORDS}", tier=tier,
                        created=now)
    kept = f"e_m06_ok_{uuid.uuid4().hex[:6]}"
    _remote_episode(backend, tenant, kept, f"the {WORDS}", created=now - 60)
    assert [r["id"] for r in backend.search_episodes(WORDS, tenant_id=tenant)] == [kept]
    # Negative control: the same rows without the tier filter.
    conn = backend._connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM kazma_episodes WHERE tenant_id = %s AND LOWER(user_text) LIKE %s",
                    (tenant, "%quokka%"))
        assert cur.fetchone()[0] == 4
    finally:
        conn.close()


# ── the keyword channel ranks by the question's words ─────────────────────


def test_an_older_memory_holding_every_word_beats_newer_ones_holding_one(pg):
    backend, tenant = pg
    now = time.time()
    old = f"e_m06_old_{uuid.uuid4().hex[:6]}"
    _remote_episode(backend, tenant, old, f"Book the {WORDS} tickets", created=now - 86400 * 90)
    for i in range(60):
        _remote_episode(backend, tenant, f"e_m06_new_{i}_{uuid.uuid4().hex[:6]}",
                        f"the local festival number {i}", created=now - i)
    got = [r["id"] for r in backend.search_episodes(WORDS, tenant_id=tenant, limit=2)]
    assert got[0] == old
    # Negative control: the old query took the 10 newest rows holding any word.
    conn = backend._connect()
    try:
        cur = conn.cursor()
        pats = [f"%{w}%" for w in WORDS.split()]
        cur.execute(
            "SELECT id FROM kazma_episodes WHERE tenant_id = %s AND (LOWER(user_text) LIKE %s "
            "OR LOWER(user_text) LIKE %s OR LOWER(user_text) LIKE %s) ORDER BY created_at DESC LIMIT %s",
            (tenant, *pats, 10),
        )
        assert old not in {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def test_a_fact_holding_every_word_beats_more_important_ones_holding_one(pg):
    backend, tenant = pg
    conn = backend._connect()
    try:
        backend._ensure(conn)
        cur = conn.cursor()
        sql = ("INSERT INTO kazma_beliefs (id, tenant_id, subject, predicate, object, predicate_type, confidence, "
               "structural_importance, source_trust_weight, valid_from, metadata_json) "
               "VALUES (%s, %s, %s, %s, %s, 'set', %s, %s, 1, %s, '{}')")
        target = f"b_m06_{uuid.uuid4().hex[:6]}"
        cur.execute(sql, (target, tenant, "user", "plans", f"the {WORDS}", 0.5, 1, time.time()))
        for i in range(60):
            cur.execute(sql, (f"b_m06_{i}_{uuid.uuid4().hex[:6]}", tenant, "user", "likes",
                              f"festival {i}", 0.99, 10, time.time()))
        conn.commit()
        assert [r["id"] for r in backend.search_beliefs(WORDS, tenant_id=tenant, limit=2)][0] == target
        # Negative control: the old order (importance x confidence) never reached it.
        pats = [f"%{w}%" for w in WORDS.split()]
        cur.execute(
            "SELECT id FROM kazma_beliefs WHERE tenant_id = %s AND (LOWER(object) LIKE %s OR LOWER(object) "
            "LIKE %s OR LOWER(object) LIKE %s) ORDER BY (structural_importance * confidence) DESC LIMIT %s",
            (tenant, *pats, 10),
        )
        assert target not in {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


# ── the benchmark, with Postgres as the primary store ─────────────────────

#: How far below the local run the Postgres-primary run may score, per
#: overall metric, measured on 2026-10-13 (dataset v4). The primary path ranks
#: on the same evidence (``recall._rank_by_evidence``) but its candidates are
#: the mirror's keyword matches and the vector index's nearest memories -- no
#: local full-text index and no graph walk for facts -- so it is close, not equal.
PRIMARY_DELTA = {"hit_rate": 0.01, "mrr": 0.01, "precision": 0.02}


def _bench(conn, refs, covers, dataset) -> dict:
    from kazma_core.memory import benchmark
    from kazma_core.memory.recall import recall

    results, latencies = [], []
    for q in dataset["questions"]:
        started = time.perf_counter()
        res = recall(q["text"], conn=conn, tenant_id=benchmark.BENCH_TENANT, limit=5)
        latencies.append(time.perf_counter() - started)
        injected = ([h.id for h in res.beliefs[:5]] + [h.id for h in res.episodes[:5]]
                    + [h.id for h in res.summaries])
        results.append(benchmark._score(q, refs, injected, covers))
    return benchmark._summarise(results, latencies)


def test_the_benchmark_with_postgres_primary_is_within_the_stated_delta(tmp_path, monkeypatch, pg):
    """The retrieval benchmark seeded once, pushed to the real mirror by the
    sync pass, asked twice: locally and with Postgres as the primary (the
    vector index answering with the same recorded vectors). Measured
    2026-10-13: hit rate 0.9253 and MRR 0.8444 both ways, precision 0.6443
    local and 0.6277 primary. Negative control: the primary run with its
    vector index down drops below the delta."""
    np = pytest.importorskip("numpy")  # noqa: F841 - the replayed vectors need it
    from pathlib import Path

    from kazma_core.memory import benchmark
    from kazma_core.memory.backends import LocalSqliteVectorBackend
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    backend, _tenant = pg
    fixtures = Path(__file__).resolve().parent / "fixtures" / "memory_bench"
    replay = benchmark.ReplayEmbedder(fixtures / "vectors.npz")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    db = tmp_path / "bench.db"
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(db))
    monkeypatch.setattr(sb, "_this_install_id", lambda: "c" * 32)
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        data = benchmark.load_dataset()
        refs = benchmark._seed(conn, data)
        covers = benchmark._covers(data, refs)
        monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: False)
        local = _bench(conn, refs, covers, data)

        monkeypatch.setattr(sb, "get_state_backend", lambda: backend)
        pushed = sb.sync_state_mirror(conn, limit=100_000)
        assert pushed["failed"] == 0 and pushed["episodes_behind"] == 0, pushed
        monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: True)
        monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend",
                            lambda conn_=None, **_k: LocalSqliteVectorBackend(conn))
        primary = _bench(conn, refs, covers, data)
        print("\nlocal  ", local["overall"], "\nprimary", primary["overall"])
        for key, delta in PRIMARY_DELTA.items():
            assert primary["overall"][key] >= local["overall"][key] - delta, (key, local, primary)

        # Negative control: the delta sees a degraded primary path -- the
        # vector index down, keywords alone. (The old recency-sliced keyword
        # channel scores the same as the new one on this dataset: the index
        # finds what the slice dropped. The slice is held by
        # test_an_older_memory_holding_every_word_beats_newer_ones_holding_one.)
        monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend", lambda conn_=None, **_k: None)
        degraded = _bench(conn, refs, covers, data)
        print("no index", degraded["overall"])
        assert any(degraded["overall"][k] < local["overall"][k] - d for k, d in PRIMARY_DELTA.items()), degraded
    finally:
        conn.close()
        pc = backend._connect()
        try:
            cur = pc.cursor()
            cur.execute("DELETE FROM kazma_episodes WHERE tenant_id = %s", (benchmark.BENCH_TENANT,))
            cur.execute("DELETE FROM kazma_beliefs WHERE tenant_id = %s", (benchmark.BENCH_TENANT,))
            pc.commit()
        finally:
            pc.close()
