"""The LongMemEval harness (``scripts/longmemeval_bench.py``, the memory
framework audit's part E) on a small history of our own in LongMemEval's
shape: no dataset text is in the repository.

* A session becomes rounds as a chat turn reaches memory: each user message
  with the reply after it, the evidence flag carried.
* The sample is the same on every machine, each type in proportion.
* A question asked of its own history finds the round that holds the answer
  among unrelated rounds; an abstention question is scored by what it shows.
* The committed result files hold ids and scores, never a dataset text.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def lme():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_bench
    finally:
        sys.path.pop(0)
    return longmemeval_bench


def _item(qid: str, qtype: str, question: str, evidence: str, others: list[str]) -> dict:
    sessions, ids, dates = [], [], []
    for n, text in enumerate([evidence, *others]):
        sessions.append([{"role": "user", "content": text, **({"has_answer": True} if n == 0 else {})},
                         {"role": "assistant", "content": "Noted, thanks for telling me."}])
        ids.append(f"s{n}")
        dates.append(f"2023/05/{10 + n:02d} (Wed) 09:{n:02d}")
    return {"question_id": qid, "question_type": qtype, "question": question, "answer": "x",
            "question_date": "2023/06/01 (Thu) 10:00", "haystack_sessions": sessions,
            "haystack_session_ids": ids, "haystack_dates": dates, "answer_session_ids": ["s0"]}


def test_a_session_becomes_rounds(lme):
    session = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b", "has_answer": True},
               {"role": "user", "content": "c"}, {"role": "user", "content": "d"},
               {"role": "assistant", "content": "e"}]
    assert lme.rounds(session) == [
        {"user": "a", "assistant": "b", "evidence": True},
        {"user": "c", "assistant": "", "evidence": False},
        {"user": "d", "assistant": "e", "evidence": False},
    ]
    assert lme.rounds([{"role": "assistant", "content": "hi"}]) == [{"user": "", "assistant": "hi", "evidence": False}]


def test_the_date_is_read_in_utc(lme):
    assert lme.parse_date("2023/05/20 (Sat) 02:21") == 1684549260.0


def test_the_sample_is_stratified_and_the_same_everywhere(lme):
    items = [{"question_id": f"{t[:2]}{i}{'_abs' if i % 10 == 0 else ''}", "question_type": t}
             for t, n in (("multi-session", 60), ("temporal-reasoning", 30), ("knowledge-update", 10))
             for i in range(n)]
    ids = lme.draw_sample(items, size=20)
    assert ids == lme.draw_sample(list(reversed(items)), size=20)
    by_type = {t: sum(1 for q in ids if q.startswith(t[:2])) for t in ("multi-session", "temporal-reasoning",
                                                                       "knowledge-update")}
    assert by_type == {"multi-session": 12, "temporal-reasoning": 6, "knowledge-update": 2}
    assert sum(q.endswith("_abs") for q in ids) == 2


def test_the_committed_split_holds_the_sample_and_keeps_report_apart(lme):
    """Dev is the sample and 100 more; report is the other 300, disjoint,
    with the abstention questions the sample did not take (the memory plan's
    W0: report is never used to choose anything)."""
    split = json.loads((REPO / "tests" / "fixtures" / "longmemeval" / "split.json").read_text(encoding="utf-8"))
    sample = lme.question_ids("sample")
    dev, report = split["dev"], split["report"]
    assert len(dev) == len(set(dev)) == 200 and len(report) == len(set(report)) == 300
    assert dev[: len(sample)] == sample and not set(dev) & set(report)
    assert split["dev_counts"]["abstention"] == 6 and split["report_counts"]["abstention"] == 24


def test_the_split_draws_dev_from_answerable_questions_only(lme):
    items = [{"question_id": f"{t[:2]}{i}{'_abs' if i % 10 == 0 else ''}", "question_type": t}
             for t, n in (("multi-session", 60), ("temporal-reasoning", 30), ("knowledge-update", 10))
             for i in range(n)]
    sample = lme.draw_sample(items, size=20)
    halves = lme.draw_split(items, sample, size=40)
    assert halves == lme.draw_split(list(reversed(items)), sample, size=40)
    assert len(halves["dev"]) == 40 and len(halves["report"]) == 60
    added = halves["dev"][20:]
    assert not any(q.endswith("_abs") for q in added)
    assert sum(q.endswith("_abs") for q in halves["report"]) == 10 - sum(q.endswith("_abs") for q in sample)


def test_a_report_run_is_refused_without_a_reason_and_logged_with_one(lme, monkeypatch, tmp_path):
    looks = tmp_path / "report_looks.json"
    monkeypatch.setattr(lme, "LOOKS", looks)
    with pytest.raises(SystemExit):
        lme.log_look("report", "  ", "retrieve")
    assert not looks.exists()
    lme.log_look("dev", None, "retrieve")  # dev is not logged
    assert not looks.exists()
    lme.log_look("report", "W9: the published number", "answer --model deepseek-flash")
    logged = json.loads(looks.read_text(encoding="utf-8"))["looks"]
    assert [(e["reason"], e["command"]) for e in logged] == [("W9: the published number",
                                                              "answer --model deepseek-flash")]


def test_a_question_finds_its_evidence_among_unrelated_rounds(lme, monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    item = _item("q1", "single-session-user", "Which degree did I graduate with?",
                 "I graduated with a degree in Business Administration last year.",
                 ["How do I boil an egg?", "Recommend a film about space.", "What is the capital of Peru?"])
    result = lme.ask(item, lme.history_rows(item))
    assert result["evidence_turn"] and result["answer_session"] and result["memories"] == 4
    summary = lme.summarise([result])
    assert summary["overall"]["evidence_turn"] == 1.0


def test_control_without_the_evidence_round_it_is_not_found(lme, monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    item = _item("q2", "single-session-user", "Which degree did I graduate with?",
                 "I graduated with a degree in Business Administration last year.",
                 ["How do I boil an egg?", "Recommend a film about space.", "What is the capital of Peru?"])
    rows = [r for r in lme.history_rows(item) if not r["evidence"]]
    result = lme.ask(item, rows)
    assert result["evidence_turn"] is False and result["answer_session"] is False


def test_result_files_hold_ids_and_scores_only():
    folder = REPO / "tests" / "fixtures" / "longmemeval"
    for path in folder.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        # No field of the dataset's text: question, answer, sessions, content.
        assert not {"question", "answer", "haystack_sessions", "content"} & _keys(data), path.name
        assert len(text) < 200_000, path.name


def _keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def _page():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import benchmarks_page
    finally:
        sys.path.pop(0)
    return benchmarks_page


def _answers(overall: dict) -> dict:
    return {"sample": 1, "reader": "m", "overall": overall, "types": {}}


def test_the_page_refuses_an_end_to_end_run_without_its_baselines(tmp_path):
    """The audit's part E: a score with no full-context and oracle run on
    the same reader proves nothing, so the page will not print one."""
    page = _page()
    (tmp_path / "answers.json").write_text(json.dumps(_answers({"kazma": 0.5})), encoding="utf-8")
    with pytest.raises(SystemExit):
        page._longmemeval(tmp_path)
    # Control: with both baselines the run is printed.
    (tmp_path / "answers.json").write_text(json.dumps(_answers(
        {"kazma": 0.5, "full": 0.6, "oracle": 0.8, "prompt_tokens_mean": {"kazma": 900, "full": 100000,
                                                                         "oracle": 9000}})), encoding="utf-8")
    text = "\n".join(page._longmemeval(tmp_path))
    assert "Whole history" in text and "80.0" in text


def test_the_judge_prompt_is_longmemevals_for_each_kind(lme):
    item = {"question_id": "q", "question_type": "temporal-reasoning", "question": "When?", "answer": "May"}
    assert "off-by-one" in lme.judge_prompt(item, "in May")
    assert "updated answer" in lme.judge_prompt({**item, "question_type": "knowledge-update"}, "x")
    assert "Rubric" in lme.judge_prompt({**item, "question_type": "single-session-preference"}, "x")
    assert "unanswerable" in lme.judge_prompt({**item, "question_id": "q_abs"}, "x")
    assert "Correct Answer: May" in lme.judge_prompt({**item, "question_type": "multi-session"}, "x")


def test_the_whole_history_is_cut_from_its_oldest_session(lme):
    item = _item("q", "single-session-user", "?", "the answer is here", ["a" * 500, "b" * 500])
    whole, dropped = lme.history_string(item)
    assert dropped == 0 and whole.index("the answer") < whole.index("aaa")
    cut, dropped = lme.history_string(item, max_chars=len(whole) - 10)
    assert dropped == 1 and "the answer" not in cut and "bbb" in cut
    only, _ = lme.history_string(item, only={"s0"})
    assert "the answer" in only and "aaa" not in only


def test_translation_parts_and_replies(lme):
    messages = [{"role": "user", "content": "x" * 3000}, {"role": "assistant", "content": "y" * 3000},
                {"role": "user", "content": "z"}]
    assert lme._chunks(messages) == [[0], [1, 2]]
    assert lme._json_reply('```json\n{"messages": ["a", "b"]}\n```') == {"messages": ["a", "b"]}
    with pytest.raises(ValueError):
        lme._json_reply("no json here")


def test_the_parallel_set_leaves_out_abstention_and_preference(lme):
    oracle = {q: {"question_type": t} for q, t in (("a", "multi-session"), ("b_abs", "multi-session"),
                                                    ("c", "single-session-preference"), ("d", "temporal-reasoning"))}
    assert lme.parallel_ids(["a", "b_abs", "c", "d"], oracle) == ["a", "d"]
