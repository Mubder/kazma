"""Relevance: a cross-encoder decides what recall shows (the memory plan's W3 + W2).

A memory near the question by meaning is not always about it: recall's
evidence (meaning above the question's background, plus the question's
words) shows the nearest turns and facts as "possibly related". The
reranker (``memory/reranker.py``) reads the question beside each of the
best candidates and beside the passage that brought a memory
(``memory/passages.py``: what its own vector misses), and its calibrated
probability decides what is shown.

Held here through the product's writer and the real ``recall``, in English
and Arabic, with a scripted reranker that knows which memory answers (the
model itself is measured by ``scripts/memory_threshold_sweep.py --rerank``
on recorded scores; CI cannot load it):

* off (the default), recall is today's, whatever a reranker would say;
* on, a memory near the question that does not answer is not shown, and the
  answer is (the control: today's rule shows the near one);
* a memory a passage brought is read by that passage, kept by it, and the
  block shows its stretch (W1b);
* the budget ends: what was not read keeps today's rule, and the recall says
  it was partial;
* no model on disk: today's rule;
* the Postgres-primary path judges as the local path does.
"""

from __future__ import annotations

import math
import re
import sqlite3
from types import SimpleNamespace

import pytest
from kazma_core.memory import passages, reranker

_CONCEPTS = {
    0: ("lentil", "lentils", "legume", "legumes", "عدس", "البقوليات", "بقوليات", "العدس"),
    1: ("soup", "broth", "شوربة", "مرق", "الشوربة", "المرق"),
    2: ("cook", "cooking", "dinner", "make", "أطبخ", "عشاء", "العشاء", "أسوي"),
    3: ("weather", "rain", "طقس", "مطر"),
    4: ("cumin", "spice", "كمون", "بهار"),
}
_DIM = 16


def _concept_vector(text: str) -> list[float]:
    vec = [0.0] * _DIM
    for word in re.findall(r"\w+", (text or "").lower()):
        dim = next((d for d, words in _CONCEPTS.items() if word in words), None)
        if dim is None:
            vec[5 + sum(ord(c) for c in word) % (_DIM - 5)] += 0.02
        else:
            vec[dim] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class _ConceptEmbedder:
    dim = _DIM

    def encode(self, text: str) -> list[float]:
        return _concept_vector(text)


class _ScriptedReranker:
    """Scores a text by the concepts it shares with the question: a text that
    holds the question's food concepts (lentils, broth) answers; one that
    holds only "cook"/"make" is near and does not."""

    model = "scripted-test"

    def __init__(self, *, reads: int | None = None) -> None:
        self.reads = reads
        self.asked: list[tuple[str, str]] = []

    def score(self, query: str, texts, *, deadline: float):
        out = []
        q = _concept_vector(query)
        for i, text in enumerate(texts):
            if self.reads is not None and i >= self.reads:
                out.append(None)
                continue
            self.asked.append((query, text))
            t = _concept_vector(text)
            food = sum(q[d] * t[d] for d in (0, 1))  # the concepts that answer
            out.append(8.0 * food - 3.0)
        return out


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    embedder = _ConceptEmbedder()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "concepts-test")
    scorer = _ScriptedReranker()
    monkeypatch.setattr("kazma_core.memory.reranker.get_reranker", lambda: scorer)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, scorer=scorer)
    dual_write._reset_mirror()
    db.close()


def _write(question: str, answer: str, *, session: str) -> str:
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id=session, turn_number=1, user_text=question, assistant_text=answer,
                         source="chat", tenant_id="default")
    assert eid
    return eid


_NEAR = {
    # A turn near the question by meaning ("cook", "dinner") that does not
    # answer it, and the turn that does.
    "en": ("What do you cook for dinner on busy nights?", "Something quick: eggs and toast, or leftovers.",
           "What should I cook tonight?", "Cook a red lentil soup with cumin.",
           "What lentil broth should I cook tonight?"),
    "ar": ("شنو أطبخ عشاء لما أكون مشغول؟", "شي سريع: بيض وتوست، أو بواقي الأكل.",
           "شنو أطبخ الليلة؟", "اطبخ شوربة عدس حمرا مع كمون.",
           "أي مرق عدس أطبخ الليلة؟"),
}


def _near_store(language: str) -> tuple[str, str]:
    near_q, near_a, q, a, _asked = _NEAR[language]
    near = _write(near_q, near_a, session="near")
    answer = _write(q, a, session="answer")
    return near, answer


def _recall(language: str, text: str | None = None):
    from kazma_core.memory.recall import format_recall_block, recall

    result = recall(text or _NEAR[language][4], limit=5, tenant_id="default", local_only=True)
    return result, format_recall_block(result)


def test_off_by_default() -> None:
    assert reranker.in_use() is False
    assert passages.in_use() is False


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_reranker_shows_the_answer_and_not_the_near_turn(mem, language) -> None:
    near, answer = _near_store(language)
    with reranker.used():
        result, _block = _recall(language)
    shown = [h.id for h in result.episodes]
    assert answer in shown and near not in shown
    assert result.rerank == "whole"
    hit = next(h for h in result.episodes if h.id == answer)
    assert 0.0 <= hit.metadata["relevance"] <= 1.0


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_today_s_rule_shows_the_near_turn(mem, language) -> None:
    near, answer = _near_store(language)
    result, _block = _recall(language)  # reranker off: today's recall
    shown = [h.id for h in result.episodes]
    assert near in shown and answer in shown
    assert result.rerank == ""
    assert not mem.scorer.asked  # off: never asked


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_without_the_floor_the_near_turn_is_shown(mem, monkeypatch, language) -> None:
    from kazma_core.memory import recall as recall_mod

    monkeypatch.setattr(recall_mod, "_RERANK_EPISODE_FLOOR", 0.0)
    near, answer = _near_store(language)
    with reranker.used():
        result, _block = _recall(language)
    assert near in [h.id for h in result.episodes]


def test_no_model_on_disk_keeps_today_s_rule(mem, monkeypatch) -> None:
    near, answer = _near_store("en")
    monkeypatch.setattr("kazma_core.memory.reranker.get_reranker", lambda: None)
    with reranker.used():
        result, _block = _recall("en")
    assert {near, answer} <= {h.id for h in result.episodes}
    assert result.rerank == ""


def test_the_budget_ending_leaves_the_unread_to_today_s_rule(mem, monkeypatch) -> None:
    near, answer = _near_store("en")
    monkeypatch.setattr("kazma_core.memory.reranker.get_reranker", lambda: _ScriptedReranker(reads=0))
    with reranker.used():
        result, _block = _recall("en")
    assert result.rerank == "partial"
    assert {near, answer} <= {h.id for h in result.episodes}  # read nothing: today's rule decided
    assert all("relevance" not in h.metadata for h in result.episodes)


# ── a memory a passage brought ───────────────────────────────────────────

_FILLER = {
    "en": " ".join(f"Point {i}: plan the week and keep the list short." for i in range(60)),
    "ar": " ".join(f"النقطة {i}: خطط للأسبوع وخل القائمة قصيرة." for i in range(60)),
}
_DEEP = {
    "en": ("What should I make tonight?", "Make a red lentil soup with plenty of cumin, simmered slowly.",
           "What was that legume broth you suggested?", "lentil soup"),
    "ar": ("شنو أسوي الليلة؟", "سوِّ شوربة عدس حمرا مع كمون كثير، على نار هادية.",
           "شنو كان المرق اللي اقترحته بالبقوليات؟", "شوربة عدس"),
}
_OTHERS = {
    "en": [("Will it rain tomorrow?", "Light rain is expected after noon."),
           ("Remind me about the dentist.", "Booked for Monday at nine."),
           ("How was the match?", "The home side won two to one.")],
    "ar": [("بيمطر باجر؟", "متوقع مطر خفيف بعد الظهر."),
           ("ذكرني بموعد الدكتور.", "محجوز يوم الاثنين الساعة تسع."),
           ("شلون كانت المباراة؟", "فاز الفريق المضيف اثنين واحد.")],
}


def _deep_store(language: str) -> str:
    question, answer, _asked, _words = _DEEP[language]
    for i, (q, a) in enumerate(_OTHERS[language]):
        _write(q, a, session=f"other{i}")
    # The answer the question asks about lies deep in a long answer.
    return _write(question, f"{_FILLER[language]} {answer} {_FILLER[language]}", session="cook")


@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_memory_a_passage_brought_is_read_by_it_and_shown_at_it(mem, language) -> None:
    with passages.used(), reranker.used():
        eid = _deep_store(language)
        result, block = _recall(language, _DEEP[language][2])
    assert eid in [h.id for h in result.episodes]
    hit = next(h for h in result.episodes if h.id == eid)
    assert hit.metadata.get("passage", ("",))[0] == "answer"
    # The reranker read the passage's stretch, and the block shows it (W1b).
    assert any(_DEEP[language][3] in text for _q, text in mem.scorer.asked)
    assert _DEEP[language][3] in block


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_without_the_anchor_the_line_shows_the_answers_start(mem, monkeypatch, language) -> None:
    from kazma_core.memory import recall as recall_mod

    real = recall_mod._rank_episodes_by_evidence

    def without_anchor(*args, **kwargs):
        ranked = real(*args, **kwargs)
        for hit in ranked:
            hit.metadata.pop("passage", None)
        return ranked

    monkeypatch.setattr(recall_mod, "_rank_episodes_by_evidence", without_anchor)
    with passages.used(), reranker.used():
        eid = _deep_store(language)
        result, block = _recall(language, _DEEP[language][2])
    assert eid in [h.id for h in result.episodes]
    assert _DEEP[language][3] not in block


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_passages_alone_no_longer_lift_a_memory(mem, language) -> None:
    """Passages bring candidates for the reranker; they never lift a memory's
    meaning (counted that way they lost answers: W5). With the reranker off
    the memory only its passage holds is not found."""
    with passages.used():
        eid = _deep_store(language)
        result, _block = _recall(language, _DEEP[language][2])
    assert eid not in [h.id for h in result.episodes]


@pytest.mark.parametrize("on", [True, False], ids=["rerank", "control_off"])
@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_postgres_primary_path_judges_as_the_local_path_does(mem, monkeypatch, language, on) -> None:
    from kazma_core.memory import state_backend as sb
    from kazma_core.memory.backends import LocalSqliteVectorBackend
    from kazma_core.memory.recall import recall

    from tests.test_memory_primary_parity import _Mirror

    with passages.used(), reranker.used(on):
        eid = _deep_store(language)
        local, _block = _recall(language, _DEEP[language][2])
        monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: True)
        monkeypatch.setattr(sb, "get_state_backend", lambda: _Mirror(mem.db))
        monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend",
                            lambda conn_=None, **_k: LocalSqliteVectorBackend(mem.db))
        primary = recall(_DEEP[language][2], limit=5, tenant_id="default")
    assert [h.id for h in primary.episodes] == [h.id for h in local.episodes]
    assert (eid in [h.id for h in primary.episodes]) is on
    if on:
        hit = next(h for h in primary.episodes if h.id == eid)
        assert hit.metadata.get("passage", ("",))[0] == "answer"


# ── calibration and the recorded scores ──────────────────────────────────


def test_calibrated_probability_is_monotonic_and_bounded(monkeypatch) -> None:
    monkeypatch.setitem(reranker.CALIBRATION, ("m", "episode"), (-1.2, 10.0, 0.4))
    values = [reranker.calibrated("m", "episode", x, 0.1) for x in (-50.0, -2.0, 0.0, 2.0, 50.0)]
    assert values == sorted(values) and 0.0 <= values[0] < values[-1] <= 1.0
    # More evidence, the same score: more likely an answer.
    assert reranker.calibrated("m", "episode", 0.0, 0.3) > reranker.calibrated("m", "episode", 0.0, 0.1)
    assert reranker.calibrated("other", "episode", 0.0) == 0.5  # no entry: the score as a logit


def test_recorded_scores_replay_and_an_unrecorded_pair_is_refused(tmp_path) -> None:
    from kazma_core.memory.benchmark import RecordingReranker, ReplayReranker

    recorder = RecordingReranker(_ScriptedReranker())
    got = recorder.score("legume broth?", ["lentil soup", "eggs and toast"], deadline=0.0)
    path = tmp_path / "scores.npz"
    assert recorder.save(path) == 2
    replay = ReplayReranker(path)
    assert replay.score("legume broth?", ["eggs and toast", "lentil soup"], deadline=0.0) == pytest.approx(
        [got[1], got[0]])
    with pytest.raises(KeyError, match="rerank-scores"):
        replay.score("legume broth?", ["a text never recorded"], deadline=0.0)


def test_a_model_that_cannot_load_costs_no_recall_a_load(monkeypatch) -> None:
    clock = [1000.0]
    tries = []

    def failing(self):
        tries.append(1)
        raise OSError("not in the cache")

    monkeypatch.setattr(reranker, "_LOADED", None)
    monkeypatch.setattr(reranker, "_FAILED_AT", None)
    monkeypatch.setattr(reranker.Reranker, "_load", failing)
    monkeypatch.setattr(reranker.time, "monotonic", lambda: clock[0])
    assert reranker.get_reranker() is None and reranker.get_reranker() is None
    assert len(tries) == 1
    clock[0] += reranker._RETRY_S + 1
    assert reranker.get_reranker() is None
    assert len(tries) == 2


def test_the_model_reads_until_its_budget_ends_a_batch_at_a_time(monkeypatch) -> None:
    import sys
    import types

    clock = [0.0]

    class _Model:
        def predict(self, pairs, **_kw):
            clock[0] += 1.0  # each batch takes a second
            return [float(len(text)) for _q, text in pairs]

    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(nn=types.SimpleNamespace(Identity=object)))
    rr = reranker.Reranker("m", clock=lambda: clock[0])
    rr._ce = _Model()
    texts = [f"text {'x' * i}" for i in range(10)]
    # No budget: every text read.
    assert None not in rr.score("q", texts, deadline=float("inf"))
    # Two seconds: two batches of BATCH read, the rest left (None), in order.
    clock[0] = 0.0
    got = rr.score("q", texts, deadline=2.0)
    read = 2 * reranker.BATCH
    assert all(x is not None for x in got[:read]) and all(x is None for x in got[read:])
    # A budget already spent reads nothing, and leaves the model free.
    assert rr.score("q", texts, deadline=clock[0] - 1) == [None] * len(texts)
    assert rr._lock.acquire(blocking=False)
    rr._lock.release()


def test_the_model_is_pinned_and_never_runs_remote_code(monkeypatch) -> None:
    import sys
    import types

    seen: dict[str, object] = {}

    class _CrossEncoder:
        def __init__(self, name, **kwargs):
            seen.update(kwargs, name=name)

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=_CrossEncoder))
    reranker.Reranker(reranker.MODEL)._load()
    assert seen["name"] == reranker.MODEL and seen["revision"] == reranker.MODEL_REVISION
    assert seen["trust_remote_code"] is False and seen["local_files_only"] is True
