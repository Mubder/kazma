"""Recall injects what the question is about, and nothing when nothing is (Stage 2, R1/R2).

Rank fusion scored a memory by its place in each channel: the fifteenth
keyword hit on "what" or "my" counted almost as much as the memory whose
meaning matched, the keyword channel matched substrings ("out" in "about"),
and recall always returned its top five. On the retrieval benchmark
(``tests/test_memory_benchmark.py``) a quarter of paraphrased questions found
their answer and no question without one got an empty result.

These tests hold the replacement -- content-word search
(``memory/query_terms.py``) and evidence ranking (``recall._rank_by_evidence``:
meaning above the question's background plus word coverage) -- on EVERY
recall path, each rule with a negative control that switches the rule off
and sees the failure come back. Vectors are built from orthogonal axes with
an exact cosine to the question, so each case states how related a memory is
instead of hoping a model agrees.
"""

from __future__ import annotations

import math
import sqlite3
import struct
from typing import Any

import pytest
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.query_terms import content_terms, coverage, mentions, search_terms
from kazma_core.memory.recall import RecallResult, format_recall_block, recall
from kazma_core.memory.schema_v2 import ensure_primary_schema

DIM = 64
MODEL = "relevance-test-model"
Q_AXIS = 0  # the question's own direction
OFF_AXIS = DIM - 1  # a question about something no memory mentions
T0 = 1_780_000_000.0

PASSPORT_Q = "when does my passport run out"
UNRELATED_Q = "what is my blood type"


def _axis(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


def _toward(cos: float, axis: int) -> list[float]:
    """A unit vector at exactly *cos* to the question, the rest on its own axis."""
    v = [0.0] * DIM
    v[Q_AXIS] = cos
    v[axis] = math.sqrt(max(0.0, 1.0 - cos * cos))
    return v


class _Embedder:
    """The question vectors; memories carry their own stored vectors."""

    dim = DIM

    def __init__(self) -> None:
        self.table = {PASSPORT_Q: _axis(Q_AXIS), UNRELATED_Q: _axis(OFF_AXIS)}

    def encode(self, text: str) -> list[float]:
        return list(self.table.get(text) or _axis(OFF_AXIS - 1))


class _Store:
    """One set of memories, served to every recall path.

    The local path reads the SQLite rows; the Postgres-primary path reads the
    same rows through a fake mirror (the real mirror's word search: content
    words, whole-word confirm, newest first) and a fake vector index (exact
    cosine over the same vectors).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.rows: dict[str, dict[str, Any]] = {}

    def add(self, eid: str, text: str, vec: list[float] | None, *, age_days: float = 10.0,
            tier: str = "episodic", local: bool = True) -> None:
        created = T0 - age_days * 86400
        self.rows[eid] = {"id": eid, "user_text": text, "assistant_text": "",
                          "summary_text": "", "tier": tier, "created_at": created,
                          "vec": vec}
        if local:
            self.conn.execute(
                "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, "
                "tier, created_at, metadata_json, embedding_model_version, embedding) "
                "VALUES (?, 'default', 's', 1, ?, ?, ?, '{}', ?, ?)",
                (eid, text, tier, created, MODEL,
                 struct.pack(f"{DIM}f", *vec) if vec is not None else None),
            )
            self.conn.commit()

    # ── the Postgres side ──
    def _cos(self, qvec: list[float], vec: list[float]) -> float:
        return sum(a * b for a, b in zip(qvec, vec)) / (
            (math.sqrt(sum(a * a for a in qvec)) or 1.0) * (math.sqrt(sum(b * b for b in vec)) or 1.0)
        )

    def mirror(self) -> Any:
        store = self

        class _Mirror:
            name = "postgres"
            available = True

            def search_episodes(self, query, *, tenant_id="default", limit=10):
                terms = search_terms(query)[:8]
                hits = [r for r in store.rows.values() if terms and mentions(r["user_text"], terms)]
                hits.sort(key=lambda r: r["created_at"], reverse=True)
                return [{k: v for k, v in r.items() if k != "vec"} for r in hits[:limit]]

            def fetch_episodes(self, ids, *, tenant_id="default"):
                return [{k: v for k, v in store.rows[i].items() if k != "vec"}
                        for i in ids if i in store.rows]

            def search_beliefs(self, query, *, tenant_id="default", limit=10):
                return []

            def fetch_beliefs(self, ids, *, tenant_id="default"):
                return []

        return _Mirror()

    def index(self) -> Any:
        store = self

        class _Index:
            name = "pgvector"
            available = True
            write_ready = True

            def search(self, qvec, *, tenant_id="default", tier=None, limit=10, kind=None):
                if kind == "belief":
                    return []
                scored = sorted(((eid, store._cos(qvec, r["vec"])) for eid, r in store.rows.items()),
                                key=lambda x: x[1], reverse=True)
                return scored[:limit]

            def similarities(self, qvec, ids, *, tenant_id="default", kind=None):
                return {i: store._cos(qvec, store.rows[i]["vec"]) for i in ids if i in store.rows}

        return _Index()


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    embedder = _Embedder()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: MODEL)
    monkeypatch.setattr("kazma_core.memory.state_backend.is_state_primary", lambda cfg=None: False)
    from kazma_core.memory.backends import LocalSqliteVectorBackend

    monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend",
                        lambda conn_=None: LocalSqliteVectorBackend(conn_ or conn))
    yield _Store(conn)
    conn.close()


def _passport_world(store: _Store) -> None:
    """A memory that answers the passport question, among chat that does not.

    The filler holds the old traps: "what", "is", "my" (every stopword the
    keyword channel used to OR) and "about" (the substring "out").
    """
    store.add("e_passport", "Passport renewal: valid until March 2031", _toward(0.85, 1))
    for i in range(14):
        store.add(f"e_filler{i}", f"What is my plan for trip {i}? I told you about the museum",
                  _toward(0.5, i + 2))


PATHS = ("local", "postgres_primary")


def _recall(store: _Store, monkeypatch, path: str, query: str, **kw: Any) -> RecallResult:
    if path == "local":
        return recall(query, conn=store.conn, local_only=True, **kw)
    monkeypatch.setattr("kazma_core.memory.state_backend.is_state_primary", lambda cfg=None: True)
    mirror, index = store.mirror(), store.index()
    monkeypatch.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: mirror)
    monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend", lambda conn=None: index)
    return recall(query, **kw)


# ── Every recall path: the question's memory, and nothing when there is none ──


@pytest.mark.parametrize("path", PATHS)
def test_each_path_finds_the_answer_and_skips_the_traps(store, monkeypatch, path):
    _passport_world(store)
    hits = _recall(store, monkeypatch, path, PASSPORT_Q).episodes
    assert [h.id for h in hits] == ["e_passport"]
    assert hits[0].metadata["strength"] == "strong"


@pytest.mark.parametrize("path", PATHS)
def test_each_path_injects_nothing_when_nothing_is_about_the_question(store, monkeypatch, path):
    _passport_world(store)
    assert _recall(store, monkeypatch, path, UNRELATED_Q).episodes == []


@pytest.mark.parametrize("path", PATHS)
def test_negative_control_rank_fusion_brings_the_junk_back(store, monkeypatch, path):
    """Both paths rank through ``_rank_by_evidence``: replace it with what rank
    fusion did -- every candidate, in channel order -- and each path answers
    an unrelated question with filler again."""
    _passport_world(store)

    def _every_candidate(query, candidates, background, *, kind="episode"):
        return [recall_mod.RecallHit(id=c.id, content=c.content, score=1.0 / (60 + i),
                                     kind=kind, source="dense")
                for i, c in enumerate(candidates)]

    monkeypatch.setattr(recall_mod, "_rank_by_evidence", _every_candidate)
    assert _recall(store, monkeypatch, path, UNRELATED_Q).episodes != []


# ── The evidence rules ────────────────────────────────────────────────────


def test_among_close_matches_the_newer_statement_comes_first(store, monkeypatch):
    store.add("e_old", "Moving plans settled", _toward(0.80, 1), age_days=40)
    store.add("e_new", "Moving plans changed", _toward(0.79, 2), age_days=2)
    for i in range(14):
        store.add(f"e_filler{i}", f"Unrelated note {i}", _toward(0.5, i + 3))
    assert [h.id for h in recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes] == [
        "e_new", "e_old"]

    monkeypatch.setattr(recall_mod, "_RECENCY_WEIGHT", 0.0)  # negative control
    assert [h.id for h in recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes] == [
        "e_old", "e_new"]


def test_a_small_store_still_finds_its_one_memory(store, monkeypatch):
    """With one memory there is no background to measure: it used to be
    estimated from the memory itself (lift 0), so a new install never
    recalled anything."""
    store.add("e_only", "Passport renewal booked", _toward(0.8, 1))
    assert [h.id for h in recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes] == ["e_only"]

    monkeypatch.setattr(recall_mod, "_question_background",  # negative control: no prior
                        lambda scores: max(list(scores), default=0.0))
    assert recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes == []


def test_a_thin_match_is_shown_as_possibly_related(store):
    store.add("e_thin", "Travel documents folder", _toward(0.57, 1))
    for i in range(14):
        store.add(f"e_filler{i}", f"Unrelated note {i}", _toward(0.5, i + 2))
    hits = recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes
    assert [h.id for h in hits] == ["e_thin"]
    assert hits[0].metadata["strength"] == "weak"
    block = format_recall_block(RecallResult([], hits), explain=False)
    assert "(possibly related) Travel documents folder" in block

    strong = recall_mod.RecallHit(id="x", content="Strong one", score=0.3,
                                  metadata={"strength": "strong"})
    assert "(possibly related)" not in format_recall_block(RecallResult([], [strong]), explain=False)


def test_a_keyword_candidate_is_judged_by_its_meaning_too(store):
    """A memory only the words found gets its similarity from its stored
    vector: "run out" words with an unrelated meaning stay out."""
    store.add("e_passport", "Passport renewal: valid until March 2031", _toward(0.85, 1))
    store.add("e_words", "I ran out of coffee and went for a run", _toward(0.3, 2))
    for i in range(14):
        store.add(f"e_filler{i}", f"Unrelated note {i}", _toward(0.5, i + 3))
    assert [h.id for h in recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes] == [
        "e_passport"]


def test_with_no_vectors_the_words_decide(store, monkeypatch):
    """No candidate has a comparable vector (no embedder, or not embedded yet):
    words alone decide, and at least half of the question must be there. The
    evidence floors assume a meaning lift; on words alone they asked a fact to
    hold every word of the question."""
    store.add("e_two_of_three", "Did the passport renewal run late?", None)
    store.add("e_one_of_three", "Passport photos taken", None)
    store.add("e_none", "Groceries for the week", None)
    assert [h.id for h in recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes] == [
        "e_two_of_three"]

    monkeypatch.setattr(recall_mod, "_WORDS_ONLY_FLOOR", 1.0)  # negative control: every word
    assert recall(PASSPORT_Q, conn=store.conn, local_only=True).episodes == []


# ── The mirror top-up (local recall with a Postgres mirror) ────────────────


def _mirror_on(store: _Store, monkeypatch) -> None:
    mirror = store.mirror()
    monkeypatch.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: mirror)


def test_the_mirror_does_not_re_add_what_local_recall_rejected(store, monkeypatch):
    _passport_world(store)
    # Holds two of the question's three words, and nothing of its meaning.
    store.add("e_morning_run", "Out for a run by the river", _toward(0.5, 20))
    _mirror_on(store, monkeypatch)
    assert [h.id for h in recall(PASSPORT_Q, conn=store.conn).episodes] == ["e_passport"]

    # Negative control: the same row held only by the mirror IS a top-up.
    store.conn.execute("DELETE FROM episodes WHERE id = 'e_morning_run'")
    store.conn.commit()
    hits = recall(PASSPORT_Q, conn=store.conn).episodes
    assert [h.id for h in hits] == ["e_passport", "e_morning_run"]
    assert hits[1].metadata["strength"] == "weak"
    assert hits[1].metadata["remote_state"] is True


def test_a_remote_only_memory_needs_half_the_question_words(store, monkeypatch):
    _passport_world(store)
    store.add("e_remote_thin", "Passport photos for the form", _toward(0.5, 21), local=False)
    store.add("e_remote_full", "My passport will run out in spring", _toward(0.5, 22), local=False)
    _mirror_on(store, monkeypatch)
    hits = recall(PASSPORT_Q, conn=store.conn).episodes
    assert [h.id for h in hits] == ["e_passport", "e_remote_full"]
    assert hits[0].score > hits[1].score


# ── Content words ─────────────────────────────────────────────────────────


def test_content_terms_drop_stopwords_and_fold():
    assert content_terms("What is my dog's name?") == ["dog", "name"]
    assert content_terms("vaccines glass bus this") == ["vaccine", "glass", "bus"]
    assert content_terms("القهوة") == content_terms("قهوة")  # the article
    assert "شنو" not in " ".join(content_terms("شنو اسم كلبي؟"))
    assert content_terms("what is it") == []


def test_word_matching_is_by_whole_word_prefix():
    assert not mentions("I told you about it", ["out"])  # the substring trap
    assert mentions("the bill ran out", ["out"])
    assert mentions("two vaccines", ["vaccine"])
    assert coverage(["dog", "name"], "My dog is called Rex, that's his name") == 1.0
    assert coverage(["dog", "name"], "my cat") == 0.0


def test_the_keyword_index_is_asked_for_content_words_only():
    assert recall_mod._fts_match_query("What is my dog's name?") == '"dog"* OR "name"*'
    assert recall_mod._fts_match_query("what is it") == ""


# ── The vector lookups every path now relies on ───────────────────────────


def test_similarities_are_only_for_comparable_vectors(store):
    from kazma_core.memory.vector_engine import VectorEngine

    store.add("e_same", "same model", _toward(0.8, 1))
    store.add("e_other", "other model", _toward(0.8, 2))
    store.conn.execute("UPDATE episodes SET embedding_model_version = 'another-model' "
                       "WHERE id = 'e_other'")
    store.conn.commit()
    sims = VectorEngine(store.conn).similarities(_axis(Q_AXIS), ["e_same", "e_other", "e_none"])
    assert set(sims) == {"e_same"}  # another model's vector is unknown, not zero
    assert sims["e_same"] == pytest.approx(0.8, abs=1e-5)

    store.conn.execute("UPDATE episodes SET embedding_model_version = ? WHERE id = 'e_other'",
                       (MODEL,))  # negative control: the same row, this model
    store.conn.commit()
    assert set(VectorEngine(store.conn).similarities(_axis(Q_AXIS), ["e_other"])) == {"e_other"}


def test_the_hybrid_index_fills_from_local_what_the_remote_lacks():
    from kazma_core.memory.backends import HybridVectorBackend

    class _Part:
        def __init__(self, answers, up=True, broken=False):
            self.answers, self.available, self.broken = answers, up, broken

        def similarities(self, qvec, ids, *, tenant_id="default", kind=None):
            if self.broken:
                raise RuntimeError("down")
            return {i: self.answers[i] for i in ids if i in self.answers}

    hybrid = HybridVectorBackend(_Part({"a": 0.9}), _Part({"a": 0.1, "b": 0.7}))
    assert hybrid.similarities([1.0], ["a", "b", "c"]) == {"a": 0.9, "b": 0.7}
    broken = HybridVectorBackend(_Part({}, broken=True), _Part({"a": 0.1}))
    assert broken.similarities([1.0], ["a"]) == {"a": 0.1}


def test_pgvector_scores_exactly_the_named_rows(monkeypatch):
    import time

    from kazma_core.memory import backends
    from kazma_core.memory.backends import PgvectorBackend

    captured: dict[str, Any] = {}

    class _Cur:
        def execute(self, sql, params=None):
            captured["sql"], captured["params"] = sql, params

        def fetchall(self):
            return [("e1", 0.61), ("e2", None)]

        def close(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cur()

        def close(self):
            return None

    dsn = "postgresql://localhost/kazma-relevance"
    be = PgvectorBackend(dsn=dsn, dimension=3)
    monkeypatch.setattr(be, "_connect", lambda: _Conn())
    monkeypatch.setattr(be, "_ensure_table", lambda conn: None)
    monkeypatch.setitem(backends._REMOTE_VECTOR_STATE, ("pgvector", dsn),
                        (time.monotonic(), "installed"))
    assert be.similarities([0.1, 0.2, 0.3], ["e1", "e2", "e1"], tenant_id="t",
                           kind="episode") == {"e1": 0.61}
    assert "id = ANY(%s)" in captured["sql"] and "tenant_id = %s" in captured["sql"]
    assert captured["params"][1:3] == ["t", ["e1", "e2"]]
    assert "episode" in captured["params"]


def test_qdrant_scores_exactly_the_named_points(monkeypatch):
    import httpx
    from kazma_core.memory.backends import QdrantVectorBackend

    sent: dict[str, Any] = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"result": [{"id": "p1", "score": 0.7, "payload": {"episode_id": "e1"}},
                               {"id": "p9", "score": 0.9, "payload": {"episode_id": "zz"}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            sent["url"], sent["body"] = url, json
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    be = QdrantVectorBackend(url="http://qdrant.test:6333", collection="c")
    assert be.similarities([0.1], ["e1", "e2"], tenant_id="t") == {"e1": 0.7}
    must = sent["body"]["filter"]["must"]
    assert {"key": "episode_id", "match": {"any": ["e1", "e2"]}} in must
    assert {"key": "tenant_id", "match": {"value": "t"}} in must
    assert sent["body"]["limit"] == 2
