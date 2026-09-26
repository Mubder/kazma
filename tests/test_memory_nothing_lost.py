"""Every memory findable -- docs/plans/MEMORY_NOTHING_LOST_PLAN.md, stage 1.

Measured on the live install on 2026-09-26: meaning search compared episodes
within an arbitrary ``LIMIT`` slice (the 60 newest of 300 were never
searched), facts within the 400 "most important", fact meaning search ran
only when keyword search came up short, archiving deleted a memory's text and
recall never searched the archived tier, and a vector that went missing or
came from another model waited for someone to click Rebuild.

Each behavioural test below runs where the old code failed -- the answer is
the newest, least important or archived memory among a thousand -- and
carries its negative control: the old rule, kept here in miniature, misses
it. The class gates at the end enumerate the product source.
"""

from __future__ import annotations

import ast
import asyncio
import math
import random
import re
import sqlite3
import struct
import time
import zlib
from pathlib import Path

import pytest

from kazma_core.memory.schema_v2 import ensure_primary_schema
from kazma_core.memory.vector_engine import RECALLABLE_TIERS, VectorEngine

REPO = Path(__file__).resolve().parents[1]
DIM = 32


def _unit(seed: int) -> list[float]:
    rnd = random.Random(seed)
    vec = [rnd.gauss(0.0, 1.0) for _ in range(DIM)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _away_from(target: list[float], seed: int) -> list[float]:
    """A unit vector pointing away from *target*: never near it by meaning."""
    vec = [a - 1.5 * b for a, b in zip(_unit(seed), target)]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _near(target: list[float], seed: int, spread: float) -> list[float]:
    """A unit vector close to *target*; a larger *spread* is further away."""
    vec = [a + spread * b for a, b in zip(target, _unit(seed))]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec]


def _blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class _Embedder:
    """A fixed vector per known text, a seeded one for anything else."""

    dim = DIM

    def __init__(self, known: dict[str, list[float]] | None = None) -> None:
        self.known = dict(known or {})
        self.texts: list[str] = []

    def encode(self, text: str) -> list[float]:
        self.texts.append(text)
        return list(self.known.get(text) or _unit(zlib.crc32(text.encode("utf-8"))))


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    # An explicit path wins over the data dir (paths.primary_memory_db).
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    yield db
    db.close()


def _episode(
    db, eid, *, vec=None, tier="episodic", created=None, user="q", answer="a",
    tenant="default", version=None, summary="", accessed=None,
):
    db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
        "assistant_text, summary_text, tier, structural_importance, created_at, "
        "last_accessed, embedding, embedding_model_version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            eid, tenant, "s", 0, user, answer, summary, tier, 1,
            time.time() if created is None else created, accessed,
            _blob(vec) if vec else None, version,
        ),
    )


def _belief(
    db, bid, *, vec=None, importance=3, predicate="p", obj="o", tenant="default",
    version=None, confidence=0.9,
):
    now = time.time()
    db.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "confidence, structural_importance, source_trust_weight, valid_from, ingested_at, "
        "embedding, embedding_model_version) VALUES (?,?,?,?,'set',?,?,?,1.0,?,?,?,?)",
        (bid, tenant, "user", predicate, obj, confidence, importance, now, now,
         _blob(vec) if vec else None, version),
    )


# ── A. Exact meaning search over every memory ─────────────────────────────


def _old_episode_candidates(db, limit: int) -> list[str]:
    """The pre-2026-09-26 candidate fetch: ``LIMIT limit*16``, no ORDER BY."""
    return [
        r[0]
        for r in db.execute(
            "SELECT id FROM episodes WHERE tenant_id = 'default' "
            "AND tier IN ('working','recall','episodic') AND embedding IS NOT NULL LIMIT ?",
            (limit * 16,),
        )
    ]


def test_meaning_search_reaches_the_newest_of_a_thousand_episodes(conn):
    for i in range(1000):
        _episode(conn, f"e{i:04d}", vec=_unit(i), created=1_000_000 + i)
    conn.commit()
    assert "e0999" not in _old_episode_candidates(conn, 15)  # recall asked for limit*3
    hits = VectorEngine(conn, model="").search(_unit(999), tier=list(RECALLABLE_TIERS), limit=5)
    assert hits[0][0] == "e0999"
    assert hits[0][1] == pytest.approx(1.0, abs=1e-5)


def test_meaning_search_reaches_the_least_important_of_1200_beliefs(conn):
    for i in range(1200):
        _belief(conn, f"b{i:04d}", vec=_unit(10_000 + i), importance=5, predicate=f"p{i}")
    _belief(conn, "target", vec=_unit(99), importance=1)
    conn.commit()
    old_pool = {
        r[0]
        for r in conn.execute(
            "SELECT id FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL "
            "AND tenant_id = 'default' AND embedding IS NOT NULL "
            "ORDER BY structural_importance DESC, confidence DESC LIMIT 400"
        )
    }
    assert "target" not in old_pool  # the old 400-belief cap
    assert VectorEngine(conn, model="").search_beliefs(_unit(99), limit=3)[0][0] == "target"


def test_the_numpy_path_ranks_exactly_as_sqlite_vec(conn):
    pytest.importorskip("sqlite_vec")
    for i in range(300):
        _episode(conn, f"e{i}", vec=_unit(i))
    conn.commit()
    engine = VectorEngine(conn, model="")
    assert engine.has_sqlite_vec and engine.has_numpy
    query = _unit(4242)
    by_sql = engine.search(query, tier=None, limit=20)
    engine.has_sqlite_vec = False
    by_numpy = engine.search(query, tier=None, limit=20)
    assert [i for i, _ in by_sql] == [i for i, _ in by_numpy]
    for (_, a), (_, b) in zip(by_sql, by_numpy):
        assert a == pytest.approx(b, abs=1e-4)


def test_only_vectors_of_the_query_model_and_size_are_ranked(conn):
    query = _unit(1)
    _episode(conn, "same_model", vec=query, version="BAAI/bge-m3")
    _episode(conn, "legacy_unstamped", vec=query, version=None)
    _episode(conn, "blank_stamp", vec=query, version="")
    _episode(conn, "other_model", vec=query, version="text-embedding-3-small")
    _episode(conn, "other_size", vec=query + [0.0] * 8, version="BAAI/bge-m3")
    conn.commit()
    engine = VectorEngine(conn, model="BAAI/bge-m3")
    for use_sqlite_vec in (engine.has_sqlite_vec, False):
        engine.has_sqlite_vec = use_sqlite_vec
        ids = {i for i, _ in engine.search(query, tier=None, limit=10)}
        assert ids == {"same_model", "legacy_unstamped", "blank_stamp"}


def test_a_zero_vector_never_ranks(conn):
    """sqlite-vec gives a zero vector a NULL distance, and NULLs sort FIRST."""
    _episode(conn, "zero", vec=[0.0] * DIM)
    _episode(conn, "real", vec=_unit(3))
    conn.commit()
    engine = VectorEngine(conn, model="")
    for use_sqlite_vec in (engine.has_sqlite_vec, False):
        engine.has_sqlite_vec = use_sqlite_vec
        assert [i for i, _ in engine.search(_unit(3), tier=None, limit=5)] == ["real"]


def test_meaning_search_never_writes(conn):
    """It used to create and drop a temporary vec0 table on the shared connection."""
    for i in range(50):
        _episode(conn, f"e{i}", vec=_unit(i))
        _belief(conn, f"b{i}", vec=_unit(500 + i), predicate=f"p{i}")
    conn.commit()
    before = conn.total_changes
    engine = VectorEngine(conn, model="")
    engine.search(_unit(1), tier=None, limit=5)
    engine.search_beliefs(_unit(1), limit=5)
    assert conn.total_changes == before
    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM sqlite_temp_master").fetchone()[0] == 0


def test_the_local_index_answers_belief_searches_with_beliefs(conn, monkeypatch):
    from kazma_core.memory.backends import LocalSqliteVectorBackend

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m1")
    _episode(conn, "an_episode", vec=_unit(8))
    _belief(conn, "a_belief", vec=None)
    conn.commit()
    local = LocalSqliteVectorBackend(conn)
    assert local.upsert("a_belief", _unit(8), meta={"kind": "belief"})
    row = conn.execute(
        "SELECT length(embedding), embedding_model_version FROM beliefs WHERE id='a_belief'"
    ).fetchone()
    assert tuple(row) == (DIM * 4, "m1")
    assert [i for i, _ in local.search(_unit(8), kind="belief", limit=5)] == ["a_belief"]
    assert [i for i, _ in local.search(_unit(8), tier=None, kind="episode", limit=5)] == [
        "an_episode"
    ]


# ── B. Facts: meaning search always runs; relevance decides ───────────────


def test_the_fact_a_question_means_is_recalled_past_keyword_noise(conn, monkeypatch):
    """Six facts share the question's word and none answers it: enough for
    keyword search alone to fill the result, which is when the old code
    stopped looking. The answer shares no word with the question."""
    from kazma_core.memory.recall import _belief_fts, _recall_beliefs

    question = "what coffee do I drink"
    meaning = _unit(7)
    embedder = _Embedder({question: meaning})
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    noise = ["coffee table walnut", "coffee shop on 5th", "coffee mug blue",
             "coffee grinder broken", "coffee filter size 4", "coffee cup set"]
    for i, obj in enumerate(noise):
        _belief(conn, f"kw{i}", vec=_away_from(meaning, 100 + i), importance=5,
                predicate=f"owns_{i}", obj=obj)
    for i in range(300):
        _belief(conn, f"n{i}", vec=_away_from(meaning, 1000 + i), predicate=f"n{i}",
                obj=f"thing {i}")
    _belief(conn, "target", vec=meaning, importance=1, predicate="drinks",
            obj="espresso every morning")
    conn.commit()

    keyword_only = _belief_fts(conn, question, "default", 15)
    assert len(keyword_only) >= 5  # the old code skipped meaning search here...
    assert "target" not in {r["id"] for r in keyword_only}  # ...and never saw the answer

    assert "target" in [h.id for h in _recall_beliefs(conn, question, "default", 5)]


def test_relevance_outranks_standing_and_standing_breaks_ties():
    from kazma_core.memory.recall import _belief_rank_score

    relevant = {"id": "rel", "structural_importance": 1, "confidence": 0.5,
                "source_trust_weight": 0.5}
    important = {"id": "imp", "structural_importance": 5, "confidence": 1.0,
                 "source_trust_weight": 1.0}

    def old(row):  # the pre-2026-09-26 score: standing alone
        return row["structural_importance"] * row["confidence"] * row["source_trust_weight"]

    assert old(important) > old(relevant)  # negative control
    ranks = {"fts": {"rel": 0, "imp": 3}}
    assert _belief_rank_score(relevant, ranks) > _belief_rank_score(important, ranks)
    ranks = {"fts": {"rel": 0, "imp": 40}, "dense": {"imp": 0}}
    assert _belief_rank_score(important, ranks) > _belief_rank_score(relevant, ranks)
    tie = {"fts": {"rel": 0, "imp": 1}, "dense": {"imp": 0, "rel": 1}}
    assert _belief_rank_score(important, tie) > _belief_rank_score(relevant, tie)


# ── C. Archive is cold storage: kept, recalled, revived ───────────────────


def test_an_archived_memory_is_recalled_behind_an_equally_matching_active_one(
    conn, monkeypatch
):
    from kazma_core.memory.recall import recall

    question = "when does the harbour ferry leave"
    meaning = _unit(5)
    embedder = _Embedder({question: meaning})
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    old = time.time() - 86400 * 90
    _episode(conn, "archived", vec=meaning, tier="archived", created=old,
             user="the harbour ferry leaves at 7:15", answer="Noted: 7:15.")
    conn.commit()
    got = recall(question, conn=conn, limit=5)
    assert [h.id for h in got.episodes] == ["archived"]
    assert got.episodes[0].metadata.get("archived") is True

    _episode(conn, "active", vec=meaning, tier="episodic",
             user="the harbour ferry now leaves at 7:40", answer="Updated: 7:40.")
    conn.commit()
    ids = [h.id for h in recall(question, conn=conn, limit=5).episodes]
    assert ids.index("active") < ids.index("archived")


def test_a_recalled_archived_memory_returns_to_episodic(conn, monkeypatch):
    from kazma_core.memory.config import DEFAULT_MEMORY_CFG
    from kazma_core.memory.macro_sleep import run_macro_sleep
    from kazma_core.memory.recall import recall

    question = "what is the wifi password at the cabin"
    meaning = _unit(6)
    embedder = _Embedder({question: meaning})
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    old = time.time() - 86400 * 90
    _episode(conn, "used", vec=meaning, tier="archived", created=old, accessed=old,
             user="cabin wifi password is pinecone42", answer="Saved.")
    _episode(conn, "idle", vec=_away_from(meaning, 9), tier="archived", created=old,
             accessed=old, user="the boat needs new oars", answer="Noted.")
    # Closer by meaning than "idle", sharing no word with the question: the
    # result fills up before the unrelated archived memory could be surfaced.
    chores = ["garden hose is green", "bike tyres want 60 psi", "bins go out on tuesday",
              "the kettle descaler is under the sink", "spare keys hang by the door",
              "the smoke alarm battery was replaced in may"]
    for i, text in enumerate(chores):
        _episode(conn, f"near{i}", vec=_near(meaning, 20 + i, 0.6 + 0.1 * i), user=text)
    conn.commit()
    surfaced = [h.id for h in recall(question, conn=conn, limit=5).episodes]
    assert "used" in surfaced and "idle" not in surfaced

    stats = run_macro_sleep(conn, cfg=DEFAULT_MEMORY_CFG)
    tiers = dict(conn.execute("SELECT id, tier FROM episodes WHERE id IN ('used','idle')").fetchall())
    assert tiers == {"used": "episodic", "idle": "archived"}
    assert stats["revived_archived"] == 1
    # Revived, it is not archived again by the same rule until it goes stale.
    run_macro_sleep(conn, cfg=DEFAULT_MEMORY_CFG)
    assert conn.execute("SELECT tier FROM episodes WHERE id='used'").fetchone()[0] == "episodic"


def test_archiving_keeps_the_vector(conn):
    from kazma_core.memory.config import DEFAULT_MEMORY_CFG
    from kazma_core.memory.macro_sleep import run_macro_sleep

    old = time.time() - 86400 * 90
    _episode(conn, "stale", vec=_unit(4), created=old, accessed=old)
    conn.commit()
    run_macro_sleep(conn, cfg=DEFAULT_MEMORY_CFG)
    row = conn.execute("SELECT tier, embedding FROM episodes WHERE id='stale'").fetchone()
    assert row["tier"] == "archived" and bytes(row["embedding"]) == _blob(_unit(4))


# ── F. Vector repair on the maintenance cadence ───────────────────────────


@pytest.fixture()
def fresh_dims(monkeypatch):
    import kazma_core.memory.reembed as reembed

    monkeypatch.setattr(reembed, "_MODEL_DIMS", {})
    return reembed


def test_repair_reencodes_missing_wrong_size_and_other_model_vectors(conn, fresh_dims):
    _episode(conn, "missing", user="alpha")
    _episode(conn, "short", vec=[1.0] * 8, user="beta", version="m-new")
    _episode(conn, "old_model", vec=_unit(1), user="gamma", version="m-old")
    _episode(conn, "archived", tier="archived", user="delta")
    _episode(conn, "fine", vec=_unit(2), user="epsilon", version="m-new")
    _belief(conn, "b_missing", obj="zeta")
    conn.commit()
    emb = _Embedder()
    out = fresh_dims.repair_unsearchable_vectors(conn, embedder=emb, model="m-new")
    assert out == {"episodes": 4, "beliefs": 1, "remaining": 0}
    rows = {
        r["id"]: (r["n"], r["v"])
        for r in conn.execute(
            "SELECT id, length(embedding) AS n, embedding_model_version AS v FROM episodes"
        )
    }
    assert all(rows[i] == (DIM * 4, "m-new") for i in ("missing", "short", "old_model", "archived"))
    fine = conn.execute("SELECT embedding FROM episodes WHERE id='fine'").fetchone()[0]
    assert bytes(fine) == _blob(_unit(2))
    assert VectorEngine(conn, model="m-new").search(emb.encode("gamma"), tier=None, limit=1)[0][0] == "old_model"


def test_repair_is_bounded_and_newest_first(conn, fresh_dims):
    for i in range(6):
        _episode(conn, f"e{i}", user=f"text {i}", created=1000 + i)
    conn.commit()
    out = fresh_dims.repair_unsearchable_vectors(
        conn, embedder=_Embedder(), model="m", time_budget_s=0
    )
    assert out == {"episodes": 0, "beliefs": 0, "remaining": 6}
    embedder = _Embedder()
    fresh_dims.repair_unsearchable_vectors(conn, embedder=embedder, model="m", batch=2)
    assert embedder.texts == ["dimension probe"] + [f"text {i}" for i in range(5, -1, -1)]
    assert conn.execute("SELECT count(*) FROM episodes WHERE embedding IS NULL").fetchone()[0] == 0


def test_repair_with_nothing_to_do_never_loads_the_model(conn, fresh_dims, monkeypatch):
    """Every 15 minutes: an idle pass must not load a 2 GB model or pay for a call."""
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_dim", lambda: DIM)
    _episode(conn, "ok", vec=_unit(1), version="m")
    conn.commit()

    def refuse():
        raise AssertionError("the embedder was loaded with nothing to repair")

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", refuse)
    assert fresh_dims.repair_unsearchable_vectors(conn) == {
        "episodes": 0, "beliefs": 0, "remaining": 0,
    }


def test_repair_pass_runs_on_the_primary_database(conn, fresh_dims, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_dim", lambda: DIM)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Embedder())
    _episode(conn, "missing", user="alpha")
    conn.commit()
    assert fresh_dims.run_vector_repair_pass() == {"episodes": 1, "beliefs": 0, "remaining": 0}


def test_reconsolidation_repairs_through_the_same_function(conn, fresh_dims, monkeypatch):
    from kazma_core.memory import global_reconsolidation

    calls = []
    monkeypatch.setattr(
        fresh_dims, "repair_unsearchable_vectors",
        lambda c, **k: calls.append(k) or {"episodes": 2, "beliefs": 1, "remaining": 0},
    )
    stats = global_reconsolidation.run_global_reconsolidation(conn, reembed_limit=7)
    assert (stats["episodes_embedded"], stats["beliefs_embedded"]) == (2, 1)
    assert calls == [{"tenant_id": "default", "batch": 7}]


def test_the_memory_passes_run_on_the_maintenance_cadence(monkeypatch):
    import kazma_core.memory.reembed as reembed
    import kazma_core.memory.rehydrate as rehydrate
    import kazma_core.memory.turn_reconcile as turn_reconcile
    from kazma_core.memory import worker_bootstrap as wb

    ran: list[str] = []
    monkeypatch.setattr(reembed, "run_vector_repair_pass", lambda: ran.append("repair"))
    monkeypatch.setattr(rehydrate, "run_rehydrate_pass", lambda: ran.append("recover"))
    monkeypatch.setattr(turn_reconcile, "run_turn_reconcile_pass", lambda: ran.append("reconcile"))
    labels = ("memory vector repair", "memory recovery", "memory turn reconcile")
    sweeps = dict(wb._MAINTENANCE_SWEEPS)
    monkeypatch.setattr(wb, "_MAINTENANCE_SWEEPS", tuple((k, sweeps[k]) for k in labels))
    asyncio.run(wb._run_maintenance_sweeps())
    assert ran == ["repair", "recover", "reconcile"]


# ── G. Memory health says what is not yet findable ────────────────────────


def test_memory_health_names_what_is_not_yet_findable():
    from kazma_core.memory.health import _findability_component

    waiting = _findability_component({
        "vectors": {"episodes": {"total": 10, "pending": 2}, "beliefs": {"total": 5, "pending": 0}},
        "erased": {"pending": 3, "unrecovered": 1, "restored": 70, "restored_question_only": 1},
    })
    assert (waiting["id"], waiting["status"]) == ("memory_findability", "warn")
    assert "2 of 15 memories wait" in waiting["detail"]
    assert "3 memories erased by the old archive rule wait" in waiting["detail"]

    settled = _findability_component({
        "vectors": {"episodes": {"total": 10, "pending": 0}, "beliefs": {"total": 5, "pending": 0}},
        "erased": {"pending": 0, "unrecovered": 7, "restored": 69, "restored_question_only": 0},
    })
    assert settled["status"] == "ok"
    assert "All 15 memories searchable" in settled["detail"]
    assert "69 erased memories recovered" in settled["detail"]
    assert "7 kept only as their short stub" in settled["detail"]


def test_the_v2_snapshot_counts_findability(conn, fresh_dims, monkeypatch):
    from kazma_core.memory.v2_health import build_v2_health

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_dim", lambda: DIM)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    _episode(conn, "ok", vec=_unit(1), version="m")
    _episode(conn, "missing", user="alpha")
    conn.commit()
    snap = build_v2_health()
    vec = snap["findability"]["vectors"]
    assert vec["episodes"] == {"total": 2, "searchable": 1, "pending": 1}
    assert snap["findability"]["erased"]["pending"] == 0


def test_the_dashboard_groups_the_findability_row():
    js = (REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "memory_console.js").read_text(
        encoding="utf-8"
    )
    assert "'memory_findability'" in js


# ── Class gates ───────────────────────────────────────────────────────────


def _string_constants(source: str) -> list[str]:
    """Every string a module holds, f-string parts joined."""
    out: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(
                v.value if isinstance(v, ast.Constant) else "{}" for v in node.values
            ))
    return out


def _capped_fetches(source: str) -> list[str]:
    """SQL in *source* that caps rows with LIMIT without ordering by distance."""
    return [
        s for s in _string_constants(source)
        if re.search(r"\bSELECT\b.+\bFROM\b", s, re.IGNORECASE | re.DOTALL)
        and re.search(r"\bLIMIT\b", s)
        and "ORDER BY d" not in s
    ]


def test_no_meaning_search_takes_an_unordered_slice():
    """A LIMIT before the distance ordering searches a slice, not the memory."""
    source = (REPO / "kazma-core" / "kazma_core" / "memory" / "vector_engine.py").read_text(
        encoding="utf-8"
    )
    assert _capped_fetches(source) == []
    old = 'sql = "SELECT id, embedding FROM episodes WHERE tenant_id = ? LIMIT ?"'
    assert _capped_fetches(old) != []  # negative control


_PRODUCT_DIRS = ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                 "kazma-tui/kazma_tui")
_TIER_WORDS = {"working", "episodic", "recall"}


def _tier_lists_without_archived(source: str) -> list[str]:
    """Hand-written episode tier lists that leave the archived tier out."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            words = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            if len(words & _TIER_WORDS) >= 2 and "archived" not in words:
                found.append(ast.unparse(node))
    for text in _string_constants(source):
        for match in re.finditer(r"tier\s+IN\s*\(([^)]*)\)", text, re.IGNORECASE):
            words = set(re.findall(r"'(\w+)'", match.group(1)))
            if len(words & _TIER_WORDS) >= 2 and "archived" not in words:
                found.append(match.group(0))
    return found


def test_every_tier_list_includes_the_archived_tier():
    """Recall reaches archived memories through RECALLABLE_TIERS; a list
    written by hand is how the tier fell out of every search path."""
    offenders: list[str] = []
    for rel in _PRODUCT_DIRS:
        for path in sorted((REPO / rel).rglob("*.py")):
            for found in _tier_lists_without_archived(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.relative_to(REPO)}: {found}")
    assert offenders == []
    assert _tier_lists_without_archived(
        'where = "AND tier IN (\'working\',\'episodic\',\'recall\')"'
    )  # negative control, SQL
    assert _tier_lists_without_archived('tier=["working", "recall", "episodic"]')  # and Python


def _text_erasing_updates(source: str) -> list[str]:
    return [
        s for s in _string_constants(source)
        if re.search(r"UPDATE\s+episodes\b", s, re.IGNORECASE)
        and re.search(r"\b(user_text|assistant_text)\s*=\s*NULL\b", s, re.IGNORECASE)
    ]


def test_nothing_sets_a_memory_s_text_to_null():
    """Archiving erased 76 memories on the live install by doing exactly this."""
    offenders = [
        f"{path.relative_to(REPO)}"
        for rel in _PRODUCT_DIRS
        for path in sorted((REPO / rel).rglob("*.py"))
        if _text_erasing_updates(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
    old = (
        '"UPDATE episodes SET tier=\'archived\', summary_text=?, '
        'user_text=NULL, assistant_text=NULL WHERE id=?"'
    )
    assert _text_erasing_updates(old)  # negative control: the old archive statement
