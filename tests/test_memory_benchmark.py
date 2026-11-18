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

# A plain import (benchmark audit R11): with ``importorskip`` a broken numpy
# skipped every memory benchmark gate, and a skip is green.
import numpy as np  # noqa: F401 -- the recorded vectors need it
import pytest
from kazma_core.memory import benchmark

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


def _locked_extraction() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8"))["extraction"]


def _locked_parts() -> dict:
    """The parts of the lock beside the recall scores, as committed."""
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    return {"extraction": locked["extraction"], "writes": locked["writes"]}


def test_the_lock_never_lowers_the_ratchet(tmp_path, monkeypatch):
    """``lock`` refuses a lower score, and ``--rebaseline`` needs a new dataset."""
    script = _build_script()
    locked = tmp_path / "thresholds.json"
    version = benchmark.load_dataset()["version"]
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    scores = {"single.hit_rate": 0.9, "abstain.hit_rate": 0.5}
    monkeypatch.setattr(benchmark, "gated_scores", lambda _report: dict(scores))
    monkeypatch.setattr(benchmark, "run_benchmark", lambda **_kw: {})
    parts = _locked_parts()

    def write(version_, values):
        locked.write_text(json.dumps({"dataset_version": version_, "scores": values, **parts}),
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
        "single.mrr": 0.8, "noise.precision": 0.7, "abstain.hit_rate": 0.4},
        **_locked_parts()}), encoding="utf-8")
    assert script._cmd_lock(rebaseline=False) == 1  # no reason given: refused
    assert script._cmd_lock(rebaseline=False, trade="rank turns above facts") == 0
    trade = json.loads(locked.read_text(encoding="utf-8"))["trades"][-1]
    assert trade["reason"] == "rank turns above facts"
    assert trade["lowered"] == {"noise.precision": [0.7, 0.67]} and "single.mrr" in trade["raised"]


# ── extraction ────────────────────────────────────────────────────────────
#
# Scored by value since 2026-11 (benchmark audit R1): a fact kept under any
# name is found; ``named`` is the share under a listed name. The model's pass
# is one recording, locked by its identity (R3): the same recording compares
# exactly, a new one is locked only on purpose, a recording of another prompt
# is refused, and no recording is a failure.


def _recording() -> dict:
    path = FIXTURES / "extraction_llm.json"
    assert path.is_file(), ("no recording of the model's extraction pass: a missing recording is a "
                            "failure, never a skipped score")
    return json.loads(path.read_text(encoding="utf-8"))


def _turns() -> list[dict]:
    return json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8"))["turns"]


def test_the_extraction_dataset_is_what_the_builder_builds():
    built = _build_script().build_extraction()
    assert json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8")) == json.loads(
        json.dumps(built, ensure_ascii=False))


def test_extraction_only_improves():
    locked = _locked_extraction()
    report = benchmark.run_extraction_benchmark(llm_replies=_recording())
    assert report["version"] == locked["version"], (
        "the extraction dataset changed: python scripts/memory_bench.py lock --rebaseline")
    assert report["llm_recording"]["sha256"] == locked["recording"]["sha256"], (
        "the recording of the model's pass changed: python scripts/memory_bench.py lock --new-recording \"why\"")
    dropped, rose = _compare(benchmark.extraction_scores(report), locked["scores"])
    assert not dropped, "Extraction got worse:\n  " + "\n  ".join(dropped)
    assert not rose, "Extraction improved -- lock it in: python scripts/memory_bench.py lock\n  " + "\n  ".join(rose)
    # Every language is measured by both passes, each with turns that should keep nothing.
    assert set(report["heuristic"]) == set(report["llm"]) == {"en", "ar", "mixed"}
    assert all(row["quiet_turns"] >= 1 for row in report["heuristic"].values())
    assert {key.split(".")[0] for key in locked["scores"]} == {"heuristic", "llm"}


def test_the_extraction_gate_sees_an_extractor_that_keeps_nothing(monkeypatch):
    """Negative control: an extractor that keeps nothing loses what it found."""
    monkeypatch.setattr("kazma_core.memory.belief_extractor.extract_beliefs_heuristic", lambda _t: [])
    dropped, _ = _compare(benchmark.extraction_scores(benchmark.run_extraction_benchmark()),
                          _locked_extraction()["scores"])
    assert any(d.startswith("heuristic.en.found") for d in dropped), dropped
    # With no recording given, every score of the model's pass is missing, and missing is a drop.
    assert any(d.startswith("llm.") for d in dropped), dropped


def test_found_reads_the_value_and_named_reads_the_name():
    """An extractor that renames every predicate keeps ``found`` and loses
    ``named``; one that drops every third fact loses ``found``. The reading
    before 2026-11 (``recall``, a fact counts only under a listed name) falls
    in both cases, so it could not tell a lost fact from a renamed one."""
    turns = _turns()
    kept = benchmark.replayed_extraction(turns, _recording())
    renamed = {tid: [{**f, "predicate": "zz_" + f["predicate"]} for f in facts] for tid, facts in kept.items()}
    seen = iter(range(10_000))
    dropped = {tid: [f for f in facts if next(seen) % 3 != 2] for tid, facts in kept.items()}
    base = benchmark._score_extraction(turns, kept)
    after_rename = benchmark._score_extraction(turns, renamed)
    after_drop = benchmark._score_extraction(turns, dropped)
    for lang in ("en", "ar", "mixed"):
        assert after_rename[lang]["found"] == base[lang]["found"] == 1.0, lang
        assert after_rename[lang]["named"] < base[lang]["named"], lang
        # The control: the old reading falls under a rename as it does under a loss.
        assert after_rename[lang]["recall"] < base[lang]["recall"], lang
    assert any(after_drop[lang]["found"] < base[lang]["found"] for lang in ("en", "ar", "mixed"))
    assert any(after_drop[lang]["recall"] < base[lang]["recall"] for lang in ("en", "ar", "mixed"))


def test_a_relation_stated_from_the_other_side_is_found():
    """"lulwa child_of user" keeps "user daughter Lulwa" (turn m05, as the
    model wrote it); the same words about another person do not."""
    turn = next(t for t in _turns() if t["id"] == "m05")
    row = benchmark._score_extraction([turn], {"m05": [
        {"subject": "lulwa", "predicate": "child_of", "object": "user"},
        {"subject": "lulwa", "predicate": "studies_at", "object": "bayan_school"}]})["mixed"]
    assert row["gold_found"] == 2 and row["kept_unwanted"] == 0
    row = benchmark._score_extraction([turn], {"m05": [
        {"subject": "noura", "predicate": "child_of", "object": "user"}]})["mixed"]
    assert row["gold_found"] == 0 and row["kept_unwanted"] == 1


def test_a_fact_about_someone_else_filed_under_the_user_is_unwanted():
    """"My daughter Lulwa studies at Bayan School" must not become "the user
    studies at Bayan School". By value alone it would count as found: the
    turn's gold also allows "user daughter_school Bayan School". The turn's
    ``avoid`` list makes it unwanted, and it finds nothing."""
    turn = next(t for t in _turns() if t["id"] == "m05")
    wrong = {"m05": [{"subject": "user", "predicate": "studies_at", "object": "bayan_school"}]}
    row = benchmark._score_extraction([turn], wrong)["mixed"]
    assert row["kept_unwanted"] == 1 and row["right"] == 0.0 and row["gold_found"] == 0
    # Control: the same turn without its avoid list counts the false fact as found and right.
    plain = {k: v for k, v in turn.items() if k != "avoid"}
    row = benchmark._score_extraction([plain], wrong)["mixed"]
    assert row["kept_unwanted"] == 0 and row["gold_found"] == 1


def test_a_true_fact_nobody_asked_for_is_not_wrong():
    turn = next(t for t in _turns() if t["id"] == "m07")
    got = {"m07": [{"subject": "user", "predicate": "works_at", "object": "zain"},
                   {"subject": "user", "predicate": "previously_worked_at", "object": "ooredoo"}]}
    assert benchmark._score_extraction([turn], got)["mixed"]["right"] == 1.0
    plain = {k: v for k, v in turn.items() if k != "extras"}
    assert benchmark._score_extraction([plain], got)["mixed"]["right"] == 0.5


def test_a_changed_extraction_prompt_needs_a_new_recording(monkeypatch):
    """Negative control of the recording's identity: with the prompt replaced
    by "Keep nothing", every deep-pass score used to stay the same
    (benchmark audit F4). Now the run refuses."""
    replies = _recording()
    monkeypatch.setattr("kazma_core.memory.belief_extractor._EXTRACT_SYSTEM", "Keep nothing.")
    with pytest.raises(benchmark.StaleRecordingError, match="prompt changed"):
        benchmark.run_extraction_benchmark(llm_replies=replies)
    # Fool the check before the run: the replay still sees the prompt it is sent.
    monkeypatch.setattr(benchmark, "extraction_prompt_sha256", lambda: replies["prompt_sha256"])
    with pytest.raises(benchmark.StaleRecordingError, match="another system prompt"):
        benchmark.run_extraction_benchmark(llm_replies=replies)


def test_a_recording_must_say_what_it_answered():
    replies = {k: v for k, v in _recording().items() if k != "prompt_sha256"}
    with pytest.raises(benchmark.StaleRecordingError, match="does not say"):
        benchmark.run_extraction_benchmark(llm_replies=replies)


def test_no_recording_cannot_be_locked(tmp_path, monkeypatch):
    """Before 2026-11 a lock with the recording gone wrote a floor with no
    model scores in it, and the gate passed (benchmark audit F13)."""
    script = _build_script()
    monkeypatch.setattr(script, "LLM_REPLIES", tmp_path / "missing.json")
    part, problems = script._extraction_lock(_locked_extraction())
    assert part == {} and any("no recording" in p for p in problems)


def test_a_second_recording_is_locked_only_on_purpose(tmp_path, monkeypatch):
    """A new recording replaces the model's scores exactly, and only when asked
    with a reason; the old rule (the higher of the two, score by score) made a
    floor no recording reaches (benchmark audit F3)."""
    script = _build_script()
    recording = _recording()
    a01 = next(t for t in _turns() if t["id"] == "a01")
    second = json.loads(json.dumps(recording))
    second["replies"][benchmark._text_key(benchmark._llm_blob(a01["user"]))] = '{"beliefs": []}'
    path = tmp_path / "extraction_llm.json"
    path.write_text(json.dumps(second, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(script, "LLM_REPLIES", path)
    old = _locked_extraction()

    part, problems = script._extraction_lock(old)
    assert any("changed" in p for p in problems), problems
    part, problems = script._extraction_lock(old, new_recording="asked again")
    assert not problems, problems
    new_score = part["scores"]["llm.ar.found"]
    assert new_score < old["scores"]["llm.ar.found"]
    assert part["recording"]["sha256"] != old["recording"]["sha256"]
    assert part["recordings"][-1]["reason"] == "asked again"
    assert part["recordings"][-1]["replaced"]["scores"]["llm.ar.found"] == old["scores"]["llm.ar.found"]
    # Control: the rule before kept the higher of the two.
    old_rule = {k: max(v, old["scores"].get(k, -1.0)) for k, v in part["scores"].items()}
    assert old_rule["llm.ar.found"] > new_score

    # The same recording with a lower locked score (a scorer change) is refused, flag or not.
    monkeypatch.setattr(script, "LLM_REPLIES", FIXTURES / "extraction_llm.json")
    raised = json.loads(json.dumps(old))
    raised["scores"]["llm.en.right"] = 1.5
    _part, problems = script._extraction_lock(raised)
    assert any("llm.en.right" in p and "same recording" in p for p in problems), problems
    # And the quick pass is a ratchet whatever happens to the recording.
    raised = json.loads(json.dumps(old))
    raised["scores"]["heuristic.en.found"] = 0.99
    monkeypatch.setattr(script, "LLM_REPLIES", path)
    _part, problems = script._extraction_lock(raised, new_recording="asked again")
    assert any(p.startswith("heuristic.en.found") for p in problems), problems


def test_a_new_extraction_dataset_is_rebaselined(tmp_path, monkeypatch):
    script = _build_script()
    locked = tmp_path / "thresholds.json"
    version = benchmark.load_dataset()["version"]
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    monkeypatch.setattr(benchmark, "gated_scores", lambda _r: {"single.hit_rate": 0.9})
    monkeypatch.setattr(benchmark, "run_benchmark", lambda **_kw: {})
    older = {**_locked_extraction(), "version": 1}
    locked.write_text(json.dumps({"dataset_version": version, "scores": {"single.hit_rate": 0.9},
                                  **_locked_parts(), "extraction": older}), encoding="utf-8")
    assert script._cmd_lock(rebaseline=False) == 1
    assert script._cmd_lock(rebaseline=True) == 0
    written = json.loads(locked.read_text(encoding="utf-8"))
    assert written["extraction"]["version"] == _locked_extraction()["version"]
    assert written["scores"] == {"single.hit_rate": 0.9}  # the recall part was not reset
    assert script._cmd_lock(rebaseline=True) == 1  # nothing new: nothing to rebaseline


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
    replies = {"model": "stand-in", "prompt_sha256": benchmark.extraction_prompt_sha256(),
               "replies": {benchmark._text_key(benchmark._llm_blob(turn["user"])): reply}}
    one = {"version": data["version"], "turns": [turn]}
    report = benchmark.run_extraction_benchmark(one, llm_replies=replies)
    assert report["llm"]["ar"]["found"] == 1.0 and report["llm"]["ar"]["right"] == 1.0
    assert report["heuristic"]["ar"]["found"] == 1.0  # the quick pass reads Arabic (arabic_facts)
    other = {"version": data["version"], "turns": [next(t for t in data["turns"] if t["id"] == "e01")]}
    with pytest.raises(KeyError, match="memory_extraction_record"):
        benchmark.run_extraction_benchmark(other, llm_replies=replies)


# ── write scenarios (the memory framework audit, F1 and F8) ───────────────
#
# A few turns through the write path, the quick pass then the deep pass, and
# the facts that must be current afterwards. They are locked as they are,
# failures included: a fix makes a scenario pass and is locked in, and a
# scenario that passed never fails again.


@pytest.fixture(scope="module")
def writes_report():
    return benchmark.run_write_scenarios(llm_replies=_recording())


def _locked_writes() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8"))["writes"]


def test_the_write_scenarios_are_what_the_builder_builds():
    built = _build_script().build_writes()
    assert json.loads(benchmark.WRITES_PATH.read_text(encoding="utf-8")) == json.loads(
        json.dumps(built, ensure_ascii=False))


def test_write_scenarios_only_improve(writes_report):
    locked = _locked_writes()
    assert writes_report["version"] == locked["version"], (
        "the write scenarios changed: python scripts/memory_bench.py lock --rebaseline")
    assert writes_report["llm_recording"]["sha256"] == locked["recording"]["sha256"], (
        "the recording changed: python scripts/memory_bench.py lock --new-recording \"why\"")
    why = {r["id"]: r["problems"] for r in writes_report["results"]}
    newly = sorted(set(writes_report["failing"]) - set(locked["failing"]))
    fixed = sorted(set(locked["failing"]) - set(writes_report["failing"]))
    dropped, rose = _compare(benchmark.write_scores(writes_report), locked["scores"])
    assert not newly and not dropped, "A write scenario that passed now fails:\n  " + "\n  ".join(
        [f"{i}: {why[i]}" for i in newly] + dropped)
    assert not fixed and not rose, ("A write scenario passes now -- lock it in: python scripts/memory_bench.py "
                                    "lock\n  " + "\n  ".join(fixed + rose))


def test_every_write_category_is_asked_in_english_and_arabic():
    data = json.loads(benchmark.WRITES_PATH.read_text(encoding="utf-8"))
    langs: dict[str, set[str]] = {}
    for scenario in data["scenarios"]:
        langs.setdefault(scenario["category"], set()).add(scenario["lang"])
    assert set(langs) == {"update", "kept_as_said", "short", "someone_else"}
    assert all({"en", "ar"} <= found for found in langs.values()), langs


def test_the_write_scenarios_see_a_gate_that_lets_anything_replace_the_user(monkeypatch, writes_report):
    """Negative control: with the trust gate open, the model's mistake about
    the user's sister replaces the user's own home, and the scenario that
    holds the gate fails."""
    assert "other-en-sister-moved" not in writes_report["failing"]
    monkeypatch.setattr("kazma_core.memory.provenance.may_overrule", lambda *_a, **_k: True)
    open_gate = benchmark.run_write_scenarios(llm_replies=_recording())
    assert "other-en-sister-moved" in open_gate["failing"]


def test_a_write_scenario_says_why_it_failed(writes_report):
    for result in writes_report["results"]:
        assert result["passed"] == (not result["problems"]), result
    moved = next(r for r in writes_report["results"] if r["id"] == "update-en-moved")
    if not moved["passed"]:
        assert any("Rotterdam" in p for p in moved["problems"]), moved


def test_the_lock_refuses_a_write_scenario_that_stops_passing():
    script = _build_script()
    old = json.loads(json.dumps(_locked_writes()))
    _part, problems = script._writes_lock(old)
    assert not problems, problems
    # A lock that holds one scenario fewer as failing than today's run: refused.
    assert old["failing"], "every scenario passes: drop this case"
    old["failing"] = old["failing"][1:]
    _part, problems = script._writes_lock(old)
    assert any("now fail" in p for p in problems), problems
