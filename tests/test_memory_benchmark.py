"""Recall quality, measured, and only allowed to improve (Stage 2, R7).

``kazma_core.memory.benchmark`` seeds a private database with the benchmark's
1,109 chat turns the way the product writes them, asks its questions through
the real ``recall()``, and scores each category. CI cannot load the real
embedder (bge-m3), so it replays the vectors bge-m3 returned for exactly
these texts (``tests/fixtures/memory_bench/vectors.npz``, recorded by
``python scripts/memory_bench.py vectors``): real semantics, no model.

The scores are a ratchet (``thresholds.json``): a drop fails, and so does an
improvement nobody locked in -- a gate that sits below what the code does
cannot see the next regression. The ratchet names the dataset version it was
measured on; a new dataset (v2 added the facts extraction stores) is locked
with ``lock --rebaseline``, which the script refuses on the same version.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
THRESHOLDS = FIXTURES / "thresholds.json"
#: Scores are exact (recorded vectors, fixed clock); a change this small is noise.
SLACK = 0.0001


def _build_script():
    spec = importlib.util.spec_from_file_location("_memory_bench", REPO / "scripts" / "memory_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report():
    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    mp = pytest.MonkeyPatch()
    mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    mp.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    try:
        yield benchmark.run_benchmark(keep_results=True)
    finally:
        mp.undo()


def _compare(scores: dict[str, float], floor: dict[str, float]) -> tuple[list[str], list[str]]:
    dropped = [f"{k}: {scores.get(k)} < {v}" for k, v in floor.items()
               if scores.get(k, -1.0) < v - SLACK]
    rose = [f"{k}: {v} > {floor[k]}" for k, v in scores.items()
            if k in floor and v > floor[k] + SLACK]
    return dropped, rose


def test_the_dataset_is_what_the_builder_builds():
    """Nobody edits the JSON by hand: the builder is the source."""
    built = _build_script().build()
    assert benchmark.load_dataset() == json.loads(json.dumps(built, ensure_ascii=False))
    cats: dict[str, int] = {}
    for q in built["questions"]:
        cats[q["category"]] = cats.get(q["category"], 0) + 1
    assert all(n >= 3 for n in cats.values()), cats


def _floor() -> dict[str, float]:
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    version = benchmark.load_dataset()["version"]
    assert locked.get("dataset_version") == version, (
        f"thresholds.json measures dataset v{locked.get('dataset_version')}, the dataset is "
        f"v{version}: record its vectors, then python scripts/memory_bench.py lock --rebaseline"
    )
    return locked["scores"]


def test_recall_quality_only_improves(report):
    dropped, rose = _compare(benchmark.gated_scores(report), _floor())
    assert not dropped, "Recall got worse on the benchmark:\n  " + "\n  ".join(dropped)
    assert not rose, (
        "Recall improved -- lock it in: python scripts/memory_bench.py lock\n  "
        + "\n  ".join(rose)
    )


def test_the_gate_catches_a_recall_that_finds_nothing(monkeypatch, report):
    """Negative control: an empty recall passes abstention and fails the rest."""
    from kazma_core.memory.recall import RecallResult

    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    monkeypatch.setattr("kazma_core.memory.recall.recall", lambda *a, **k: RecallResult([], []))
    broken = benchmark.run_benchmark()
    dropped, _ = _compare(benchmark.gated_scores(broken), _floor())
    assert broken["categories"]["abstain"]["hit_rate"] == 1.0
    assert any(d.startswith("single.hit_rate") for d in dropped), dropped


def test_every_question_was_scored(report):
    assert report["overall"]["questions"] == len(benchmark.load_dataset()["questions"])
    assert report["memories"] > 1000


def test_the_lock_never_lowers_the_ratchet(tmp_path, monkeypatch):
    """``lock`` refuses a lower score, and ``--rebaseline`` needs a new dataset."""
    script = _build_script()
    locked = tmp_path / "thresholds.json"
    version = benchmark.load_dataset()["version"]
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    scores = {"single.hit_rate": 0.9, "abstain.hit_rate": 0.5}
    monkeypatch.setattr(benchmark, "gated_scores", lambda _report: dict(scores))
    monkeypatch.setattr(benchmark, "run_benchmark", lambda **_kw: {})

    def write(version_, values):
        locked.write_text(json.dumps({"dataset_version": version_, "scores": values}),
                          encoding="utf-8")

    write(version, {"single.hit_rate": 0.95, "abstain.hit_rate": 0.5})
    assert script._cmd_lock(rebaseline=False) == 1  # 0.9 < 0.95: refused
    assert json.loads(locked.read_text(encoding="utf-8"))["scores"]["single.hit_rate"] == 0.95
    assert script._cmd_lock(rebaseline=True) == 1  # same dataset: no rebaseline

    write(version, {"single.hit_rate": 0.8, "abstain.hit_rate": 0.5})  # control: a rise
    assert script._cmd_lock(rebaseline=False) == 0
    assert json.loads(locked.read_text(encoding="utf-8"))["scores"]["single.hit_rate"] == 0.9

    write(version - 1, {"single.hit_rate": 0.99})  # an older dataset's ratchet
    assert script._cmd_lock(rebaseline=False) == 1
    assert script._cmd_lock(rebaseline=True) == 0
    written = json.loads(locked.read_text(encoding="utf-8"))
    assert {k: written[k] for k in ("dataset_version", "scores")} == {
        "dataset_version": version, "scores": scores}
    assert written["targets"] == script.TARGETS and written["trades"] == []


# ── held out (Memory audit M24) ───────────────────────────────────────────


def test_the_held_out_questions_are_scored_apart(report):
    data = benchmark.load_dataset()
    assert report["heldout"]["overall"]["questions"] == len(data["heldout"]) >= 40
    gated = benchmark.gated_scores(report)
    assert "heldout.overall.mrr" in gated and "heldout.mixed.hit_rate" in gated
    # Never the same question twice: a held-out copy of a tuning question
    # would score the tuning, not the generalization.
    assert not {q["text"] for q in data["questions"]} & {q["text"] for q in data["heldout"]}


def test_the_builder_refuses_a_held_out_question_that_repeats_a_tuning_one(monkeypatch):
    script = _build_script()
    monkeypatch.setattr(script, "HELDOUT", [*script.HELDOUT, ("h999", script.QUESTIONS[0][1], "single",
                                                              script.QUESTIONS[0][3], [])])
    with pytest.raises(ValueError, match="repeats a tuning question"):
        script.build()


# ── the abstention target and the trade rule ──────────────────────────────


def test_abstention_has_an_explicit_target_above_its_floor():
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    for key, target in locked["targets"].items():
        assert key.endswith("abstain.hit_rate") and target > locked["scores"][key]


@pytest.mark.parametrize(("old", "new", "why"), [
    ({"single.mrr": 0.8, "abstain.hit_rate": 0.4}, {"single.mrr": 0.86, "abstain.hit_rate": 0.38},
     "abstention is never traded"),
    ({"single.mrr": 0.8, "noise.precision": 0.7}, {"single.mrr": 0.9, "noise.precision": 0.6},
     "falls by"),
    ({"single.mrr": 0.8, "noise.precision": 0.7}, {"single.mrr": 0.82, "noise.precision": 0.68},
     "nothing rises"),
])
def test_the_trade_rule_refuses(old, new, why):
    problems = _build_script().trade_problems(old, new)
    assert any(why in p for p in problems), problems


def test_a_trade_inside_the_rule_is_recorded(tmp_path, monkeypatch):
    """Negative control of the refusals: a small fall paid for by a larger
    rise is accepted, and written down with its reason."""
    script = _build_script()
    locked = tmp_path / "thresholds.json"
    version = benchmark.load_dataset()["version"]
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    monkeypatch.setattr(benchmark, "gated_scores",
                        lambda _r: {"single.mrr": 0.9, "noise.precision": 0.67, "abstain.hit_rate": 0.4})
    monkeypatch.setattr(benchmark, "run_benchmark", lambda **_kw: {})
    locked.write_text(json.dumps({"dataset_version": version, "scores": {
        "single.mrr": 0.8, "noise.precision": 0.7, "abstain.hit_rate": 0.4}}), encoding="utf-8")
    assert script._cmd_lock(rebaseline=False) == 1  # no reason given: refused
    assert script._cmd_lock(rebaseline=False, trade="rank turns above facts") == 0
    trade = json.loads(locked.read_text(encoding="utf-8"))["trades"][-1]
    assert trade["reason"] == "rank turns above facts"
    assert trade["lowered"] == {"noise.precision": [0.7, 0.67]} and "single.mrr" in trade["raised"]


# ── extraction ────────────────────────────────────────────────────────────


def test_extraction_only_improves():
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))["extraction"]
    replies = FIXTURES / "extraction_llm.json"
    report = benchmark.run_extraction_benchmark(
        llm_replies=json.loads(replies.read_text(encoding="utf-8")) if replies.is_file() else None)
    assert report["version"] == locked["version"]
    dropped, rose = _compare(benchmark.extraction_scores(report), locked["scores"])
    assert not dropped, "Extraction got worse:\n  " + "\n  ".join(dropped)
    assert not rose, "Extraction improved -- lock it in: python scripts/memory_bench.py lock\n  " + "\n  ".join(rose)
    # Every language is measured, each with turns that should keep nothing.
    assert set(report["heuristic"]) == {"en", "ar", "mixed"}
    assert all(row["quiet_turns"] >= 1 for row in report["heuristic"].values())


def test_the_extraction_gate_sees_an_extractor_that_keeps_nothing(monkeypatch):
    """Negative control: an extractor that keeps nothing loses its recall."""
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))["extraction"]
    monkeypatch.setattr("kazma_core.memory.belief_extractor.extract_beliefs_heuristic", lambda _t: [])
    dropped, _ = _compare(benchmark.extraction_scores(benchmark.run_extraction_benchmark()), locked["scores"])
    assert any(d.startswith("heuristic.en.recall") for d in dropped), dropped


def test_a_slugged_value_matches_the_words_it_stands_for(monkeypatch):
    """The extraction prompt slugs values ("Kuwait City" -> ``kuwait_city``)
    and search reads an underscore as a space: the first live recording
    (2026-10-15) scored 9 right facts wrong because the comparison did not."""
    cases = [("lives_in", "Kuwait City", "kuwait_city"),
             ("prefers", "الشاي بالنعناع", "الشاي_بالنعناع"),
             ("works_at", "Gulf Bank", "gulf_bank")]
    for predicate, gold, got in cases:
        alt = [{"subject": "user", "predicates": [predicate], "object": gold}]
        assert benchmark._fact_matches(alt, {"subject": "user", "predicate": predicate, "object": got}), got
    # A different value still does not match.
    alt = [{"subject": "user", "predicates": ["lives_in"], "object": "Kuwait City"}]
    assert not benchmark._fact_matches(alt, {"subject": "user", "predicate": "lives_in", "object": "dubai"})

    # Negative control: the comparison before the fix (no underscore rule).
    from kazma_core.documents.arabic import fold_for_search

    monkeypatch.setattr(benchmark, "_folded", lambda t: " ".join(fold_for_search(str(t or "")).lower().split()))
    alt = [{"subject": "user", "predicates": ["lives_in"], "object": "Kuwait City"}]
    assert not benchmark._fact_matches(alt, {"subject": "user", "predicate": "lives_in", "object": "kuwait_city"})


def test_the_models_pass_replays_recorded_replies():
    """The deep pass is scored from the model's own recorded replies; a turn
    nobody recorded is an error, never a guess."""
    data = json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8"))
    turn = next(t for t in data["turns"] if t["id"] == "a01")
    reply = json.dumps({"beliefs": [
        {"subject": "user", "predicate": "name", "predicate_type": "functional", "object": "ليلى"},
        {"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "دبي"}]},
        ensure_ascii=False)
    replies = {"model": "stand-in", "replies": {benchmark._text_key(benchmark._llm_blob(turn["user"])): reply}}
    one = {"version": data["version"], "turns": [turn]}
    report = benchmark.run_extraction_benchmark(one, llm_replies=replies)
    assert report["llm"]["ar"]["recall"] == 1.0 and report["llm"]["ar"]["precision"] == 1.0
    assert report["heuristic"]["ar"]["recall"] == 1.0  # the quick pass reads Arabic (arabic_facts)
    other = {"version": data["version"], "turns": [next(t for t in data["turns"] if t["id"] == "e01")]}
    with pytest.raises(KeyError, match="memory_extraction_record"):
        benchmark.run_extraction_benchmark(other, llm_replies=replies)
