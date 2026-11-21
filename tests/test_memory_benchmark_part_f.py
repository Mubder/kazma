"""Part F's benchmark categories, each beside recall without its change
(framework audit F9, F2 step 3; dataset v6).

Dataset v6 added four categories: ``dense`` (thirty Arabic wedding turns,
forty near-identical ride logs), ``follow_up`` (a question that goes on from
the message before it, and stored follow-ups), ``list`` (a count or a list)
and ``temporal`` (a period, what was true before). The lock gates their
scores (``tests/test_memory_benchmark.py``); here each change part F made is
taken out and the questions it is for must lose answers -- or the category
does not measure the change:

* the follow-up rewrite (``question_shape.standalone_query``) -> ``follow_up``;
* the request for every memory (``question_shape.asks_for_all``) -> ``list``;
* the question's time (``recall._time_ask``) -> ``temporal``;
* one-digit numbers as words (``content_terms(digits=True)``) -> ``dense``.

Run on the recorded vectors (``vectors.npz`` holds the raw follow-ups too:
``memory_bench.py vectors`` records the control's run).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np  # noqa: F401 -- the recorded vectors need it
import pytest
from kazma_core.memory import benchmark

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "memory_bench"
CATEGORIES = ("dense", "follow_up", "list", "temporal")


def _dataset() -> dict:
    data = benchmark.load_dataset()
    return {**data, "questions": [q for q in data["questions"] if q["category"] in CATEGORIES],
            "second": [q for q in data.get("second") or [] if q["category"] in CATEGORIES]}


def _hits(report: dict) -> dict[str, set[str]]:
    """Category -> the questions answered (both sets)."""
    out: dict[str, set[str]] = {}
    for r in benchmark._all_results(report):
        if r["hit"] and r.get("order") is not False:
            out.setdefault(r["category"], set()).add(r["id"])
    return out


def _run(mp: pytest.MonkeyPatch) -> dict[str, set[str]]:
    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    mp.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    return _hits(benchmark.run_benchmark(_dataset(), keep_results=True))


@pytest.fixture(scope="module")
def answered() -> dict[str, set[str]]:
    mp = pytest.MonkeyPatch()
    try:
        return _run(mp)
    finally:
        mp.undo()


def _without(change) -> dict[str, set[str]]:
    mp = pytest.MonkeyPatch()
    try:
        change(mp)
        return _run(mp)
    finally:
        mp.undo()


def _lost(answered: dict[str, set[str]], control: dict[str, set[str]], category: str) -> set[str]:
    return answered.get(category, set()) - control.get(category, set())


def test_the_raw_follow_up_loses_follow_up_answers(answered):
    control = _without(lambda mp: mp.setattr(
        "kazma_core.memory.question_shape.standalone_query", lambda q, _earlier: q))
    assert _lost(answered, control, "follow_up"), (answered.get("follow_up"), control.get("follow_up"))


def test_a_list_read_as_one_question_loses_list_answers(answered):
    control = _without(lambda mp: mp.setattr(
        "kazma_core.memory.question_shape.asks_for_all", lambda _q: False))
    assert _lost(answered, control, "list"), (answered.get("list"), control.get("list"))


def test_recall_that_does_not_read_the_time_loses_temporal_answers(answered):
    control = _without(lambda mp: mp.setattr(
        "kazma_core.memory.recall._time_ask", lambda *_a, **_k: None))
    assert _lost(answered, control, "temporal"), (answered.get("temporal"), control.get("temporal"))


def test_dropping_one_digit_numbers_loses_a_dense_answer(answered):
    from kazma_core.memory import query_terms

    plain = query_terms.content_terms
    control = _without(lambda mp: mp.setattr(
        query_terms, "content_terms", lambda text, *, digits=False: plain(text)))
    assert _lost(answered, control, "dense") >= {"q1011"}, (answered.get("dense"), control.get("dense"))


def test_without_joining_stored_follow_ups_their_questions_lose_answers(answered):
    """The stored follow-ups ("Yes, book it for Saturday at 10."): their
    vector is made from words that say nothing of what they answer."""
    control = _without(lambda mp: mp.setattr(
        "kazma_core.memory.recall._with_neighbours", lambda _conn, hits, *_a, **_k: hits))
    assert _lost(answered, control, "follow_up"), (answered.get("follow_up"), control.get("follow_up"))


# ── v6 only added: a sealed set or a study written for v5 still holds ────


def test_the_dataset_holds_the_memories_of_the_version_it_names():
    data = benchmark.load_dataset()
    held = data["ids_hold_since"]
    assert benchmark.memory_fingerprint(data, as_of=held["version"]) == held["sha256"]


def test_a_changed_memory_of_that_version_breaks_the_claim():
    """Negative control: one v5 turn reworded."""
    data = benchmark.load_dataset()
    session = next(s for s in data["sessions"] if not s.get("new_in"))
    session["turns"][0]["user"] += " (reworded)"
    held = data["ids_hold_since"]
    assert benchmark.memory_fingerprint(data, as_of=held["version"]) != held["sha256"]


def test_v5_as_of_v6_is_v5_and_the_new_parts_are_v6s():
    data = benchmark.load_dataset()
    v5 = benchmark.dataset_as_of(data, 5)
    assert v5["version"] == 5 and not {q["category"] for q in v5["questions"] + v5["second"]} & set(CATEGORIES)
    assert len(v5["sessions"]) < len(data["sessions"]) and len(v5["questions"]) < len(data["questions"])
    assert benchmark.memory_fingerprint(v5) == data["ids_hold_since"]["sha256"]


def _sealed_for(version: int) -> dict:
    return {"version": 1, "dataset_version": version, "written_by": "test", "written_on": "2026-11-21",
            "questions": []}


def test_a_sealed_set_written_for_v5_is_not_refused_for_its_version():
    data = benchmark.load_dataset()
    problems = benchmark.sealed_problems(_sealed_for(5), data)
    assert not [p for p in problems if p.startswith("written for dataset")], problems


def test_without_the_claim_a_v5_sealed_set_is_refused():
    """Negative control: the dataset without ``ids_hold_since``."""
    data = {k: v for k, v in benchmark.load_dataset().items() if k != "ids_hold_since"}
    problems = benchmark.sealed_problems(_sealed_for(5), data)
    assert any(p.startswith("written for dataset v5") for p in problems), problems
    assert any(p.startswith("written for dataset v4") for p in benchmark.sealed_problems(
        _sealed_for(4), benchmark.load_dataset())), "v4 was never claimed"
