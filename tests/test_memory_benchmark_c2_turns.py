"""The extraction benchmark asks what part C2 changed (extraction dataset v3).

The recording of decision 2 measures the deep pass's new question, and the
30 turns of dataset v2 ask none of what changed: no turn has a day, a turn
before, a held fact, an ending or text that tries to plant facts. Dataset v3
adds ten (``memory_bench.EXTRACTION_C2_TURNS``, English and Arabic), each
gold fact naming what it asks beyond the fact: its start worked out from the
turn's day (``since``), the held fact it replaces (``replaces``), or that it
ended (``ended``). The model's pass scores them per language (``dated``,
``replaced``, ``ended``); a fact said to have ended finds only an ending; the
planted facts must not be kept (``avoid``). Until the recording they wait,
named (``llm_waiting``): the recording before C2 never answered them.

Negative controls: answers that keep the facts and give no start, replace
nothing and end nothing score 0 on each; the scorer that ignores ``ended``
takes a held vim for the ended one.
"""

from __future__ import annotations

import json

import pytest
from kazma_core.memory import benchmark

#: Right answers to the C2 turns, by turn id (the facts the model should give).
RIGHT = {
    "t01": [{"subject": "user", "predicate": "works_at", "object": "Zain", "since": "2026-03"}],
    "t02": [{"subject": "user", "predicate": "works_at", "object": "زين", "since": "2024"}],
    "t03": [{"subject": "hana", "predicate": "works_at", "object": "Qatar Airways"}],
    "r01": [{"subject": "user", "predicate": "lives_in", "object": "Rotterdam", "since": "2026-04", "replaces": "f1"}],
    "r02": [{"subject": "hana", "predicate": "occupation", "object": "captain", "replaces": "f1"}],
    "r03": [{"subject": "user", "predicate": "works_at", "object": "سابك", "replaces": "f1"},
            {"subject": "user", "predicate": "works_at", "object": "أرامكو", "ended": True}],
    "x01": [{"subject": "user", "predicate": "uses_tool", "object": "vim", "ended": True, "replaces": "f1"},
            {"subject": "user", "predicate": "uses_tool", "object": "VS Code"}],
    "x02": [{"subject": "user", "predicate": "lives_in", "object": "باريس", "ended": True, "replaces": "f1"}],
    "p01": [],
    "p02": [],
}


def _plain(facts: list[dict]) -> list[dict]:
    """The same facts with no start, nothing replaced and nothing ended."""
    return [{k: v for k, v in f.items() if k not in ("since", "replaces", "ended")} for f in facts]


def _turns() -> list[dict]:
    return json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8"))["turns"]


def _recording(answers: dict[str, list[dict]]) -> dict:
    """A recording of today's prompt: the old replies for the old turns,
    *answers* for the turns C2 added."""
    from kazma_core.memory.belief_extractor import MESSAGE_SHAPE

    path = benchmark.EXTRACTION_PATH.parents[3].parent / "tests" / "fixtures" / "memory_bench" / "extraction_llm.json"
    old = json.loads(path.read_text(encoding="utf-8"))
    shape = benchmark._recording_shape(old)
    replies = {}
    for turn in _turns():
        if turn.get("new_in"):
            beliefs = [dict(f, predicate_type="set" if f["predicate"] == "uses_tool" else "functional",
                            confidence=0.9, importance=3) for f in answers[turn["id"]]]
            reply = json.dumps({"beliefs": beliefs}, ensure_ascii=False)
        else:
            reply = old["replies"][benchmark._text_key(benchmark._recorded_message(turn["user"], shape))]
        replies[benchmark._text_key(benchmark._llm_blob(turn["user"], **benchmark._turn_context(turn)))] = reply
    return {**old, "replies": replies, "message_shape": MESSAGE_SHAPE,
            "prompt_sha256": benchmark._extraction_prompt_sha256()}


def _c2_report(answers: dict[str, list[dict]]) -> dict:
    turns = [t for t in _turns() if t.get("new_in")]
    data = {"version": 3, "turns": turns}
    return benchmark.run_extraction_benchmark(data, llm_replies=_recording(answers))["llm"]


def test_the_dataset_asks_what_c2_changed_in_both_languages():
    c2 = [t for t in _turns() if t.get("new_in")]
    assert len(c2) == 10 and {t["lang"] for t in c2} == {"en", "ar"}
    asked = {field for t in c2 for alts in t["facts"] for a in alts for field in ("since", "replaces", "ended")
             if a.get(field)}
    assert asked == {"since", "replaces", "ended"}
    assert any(t.get("previous") for t in c2) and any(t.get("held") for t in c2)
    assert all(t["said_on"] == "2026-05-31" for t in c2)
    assert sum(1 for t in c2 if not t["facts"] and t.get("avoid")) == 2       # the poisoned turns


def test_right_answers_score_full_marks():
    for lang, row in _c2_report(RIGHT).items():
        assert (row["found"], row["right"], row["quiet"]) == (1.0, 1.0, 1.0), (lang, row)
        assert all(row[k] == 1.0 for k in ("dated", "replaced", "ended") if k in row), (lang, row)
    assert {k for row in _c2_report(RIGHT).values() for k in ("dated", "replaced", "ended") if k in row} == {
        "dated", "replaced", "ended"}


def test_control_answers_without_a_start_a_replacement_or_an_ending_score_zero_on_each():
    row = _c2_report({k: _plain(v) for k, v in RIGHT.items()})
    assert all(r.get(k, 0.0) == 0.0 for r in row.values() for k in ("dated", "replaced", "ended")), row
    # A held fact returned for an ended one is not found, and is not wanted.
    assert row["en"]["found"] < 1.0 and row["en"]["right"] < 1.0


def test_planted_facts_are_unwanted_and_break_the_quiet():
    planted = dict(RIGHT, p01=[{"subject": "user", "predicate": "name_is", "object": "Mallory"}],
                   p02=[{"subject": "user", "predicate": "lives_in", "object": "أتلانتس"}])
    row = _c2_report(planted)
    assert row["en"]["quiet"] < 1.0 and row["ar"]["quiet"] < 1.0
    assert any("Mallory" in u for u in row["en"]["unwanted"])


def test_a_fact_read_from_the_turn_before_alone_is_unwanted():
    row = _c2_report(dict(RIGHT, t03=RIGHT["t03"] + [{"subject": "hana", "predicate": "lives_in", "object": "Doha"}]))
    assert row["en"]["right"] < 1.0


def test_control_a_scorer_that_ignores_ended_takes_the_held_fact_for_the_ending(monkeypatch):
    held_vim = dict(RIGHT, x01=[_plain(RIGHT["x01"])[0], RIGHT["x01"][1]])
    honest = _c2_report(held_vim)["en"]["found"]
    real = benchmark._value_matches

    def ignores_ended(alt, got):
        return real({k: v for k, v in alt.items() if k != "ended"}, {k: v for k, v in got.items() if k != "ended"})

    monkeypatch.setattr(benchmark, "_value_matches", ignores_ended)
    assert _c2_report(held_vim)["en"]["found"] > honest


def test_the_drift_check_names_them_as_waiting():
    path = benchmark.EXTRACTION_PATH.parents[3].parent / "tests" / "fixtures" / "memory_bench"
    old = json.loads((path / "extraction_llm.json").read_text(encoding="utf-8"))
    pending = json.loads((path / "thresholds.json").read_text(encoding="utf-8"))["extraction"].get(
        "pending_recording")
    if benchmark._check_recording(old, pending) != "previous":
        pytest.skip("the recording answers today's prompt: nothing waits")
    report = benchmark.run_extraction_benchmark(llm_replies=old, pending=pending)
    assert report["llm_waiting"] == [t["id"] for t in _turns() if t.get("new_in")]
    assert all(t.get("new_in") is None or t["id"] in report["llm_waiting"] for t in _turns())


@pytest.mark.parametrize(("gold", "got", "holds"), [
    ("2026-04", "2026-04", True), ("2026-04", "2026-04-30", True), ("2024", "2024-05", True),
    ("2026-04", "2026", False), ("2026-04", "2026-05", False), ("2026-04", None, False),
    ("2026-04", "2026-041", False),
])
def test_a_start_is_right_to_the_golds_own_precision(gold, got, holds):
    assert benchmark._since_holds(gold, got) is holds
