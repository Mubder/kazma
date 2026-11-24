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


def test_the_free_proxies_tell_a_cut_answer_from_a_shown_one(lme, monkeypatch, tmp_path):
    """The memory plan's W0: "the answer's text is in the block" beside "in
    the rounds shown". An answer at the end of a long message, far from the
    question's words, is shown (its round is recalled) and cut from the block
    (the block cuts a long memory around the question's words, W1a, and here
    those are at its start); a short one is in both."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    others = ["How do I boil an egg?", "Recommend a film about space.", "What is the capital of Peru?"]
    short = {**_item("q3", "single-session-user", "Which degree did I graduate with?",
                     "I graduated with a degree in Business Administration last year.", others),
             "answer": "Business Administration"}
    filler = "We also talked about the weather and the long drive to work that morning. " * 40
    long = {**_item("q4", "single-session-user", "Which degree did I graduate with?",
                    "About my degree: " + filler + "I graduated in Business Administration.", others),
            "answer": "Business Administration"}
    shown, cut = lme.ask(short, lme.history_rows(short)), lme.ask(long, lme.history_rows(long))
    assert shown["answer_in_shown"] and shown["answer_in_block"]
    assert cut["answer_in_shown"] and cut["answer_in_block"] is False
    summary = lme.summarise([shown, cut])["overall"]
    assert summary["worded"] == 2 and summary["answer_in_shown"] == 1.0 and summary["answer_in_block"] == 0.5
    # A bare number, a count, is no proxy: it is written in most rounds.
    assert lme.answer_words({**short, "answer": "3"}) == "" and lme.ask(
        {**short, "answer": "3"}, lme.history_rows(short))["answer_in_block"] is None


class _WordVectors:
    """A stand-in embedder: one dimension a word of a small vocabulary."""

    VOCAB = ("degree", "graduated", "business", "egg", "boil", "film", "space", "capital", "peru")

    def encode(self, text: str) -> list[float]:
        words = text.casefold().replace("?", " ").replace(".", " ").split()
        return [float(sum(w.startswith(v) for w in words)) + 1e-6 for v in self.VOCAB]


def test_the_plain_baselines_rank_the_rounds_without_kazmas_ranking(lme):
    """The plan's W0 baselines: bge-m3's nearest rounds by plain cosine, and
    BM25's, each read whole, oldest first."""
    item = _item("q5", "single-session-user", "Which degree did I graduate with?",
                 "I graduated with a degree in Business Administration last year.",
                 ["How do I boil an egg?", "Recommend a film about space.", "What is the capital of Peru?"])
    rows = lme.history_rows(item)
    for how in ("bm25", "bge"):
        top = lme.plain_top(item["question"], rows, how, embedder=_WordVectors(), k=1)
        assert top[0]["evidence"], how
    # Control: a question sharing no word with the evidence round does not
    # find it first.
    assert not lme.plain_top("Where is the capital of Peru?", rows, "bm25", k=1)[0]["evidence"]
    scores = lme.plain_scores(item, rows, _WordVectors())
    assert scores["plain_bm25_evidence"] and scores["plain_bge_every_session"]
    whole = lme.read_whole(list(reversed(rows[:2])))
    assert whole.index("Business Administration") < whole.index("boil an egg")


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


def test_a_history_cut_to_fit_is_never_called_whole(tmp_path):
    """The run of 21 Nov cut every history at 440,000 characters: the page
    names that column "History cut to fit" (the plan's W0); an uncut run
    keeps "Whole history"."""
    page = _page()
    run = _answers({"kazma": 0.5, "full": 0.6, "oracle": 0.8, "prompt_tokens_mean": {"full": 100000}})
    (tmp_path / "answers.json").write_text(json.dumps({**run, "full_max_chars": 440000}), encoding="utf-8")
    text = "\n".join(page._longmemeval(tmp_path))
    assert "History cut to fit" in text and "Whole history" not in text and "440,000" in text
    (tmp_path / "answers.json").write_text(json.dumps({**run, "full_max_chars": None}), encoding="utf-8")
    text = "\n".join(page._longmemeval(tmp_path))
    assert "Whole history" in text and "cut to fit" not in text


def test_the_whole_history_is_sent_uncut(lme):
    """The reader's window holds the longest history (115,818 tokens, 22 Nov):
    the full-context baseline is not cut."""
    assert lme.FULL_MAX_CHARS is None


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


# ── The instrument checked (the memory plan's W0) ───────────────────────────


def _chat(lme, reasoning: str, answer: dict):
    import httpx

    sent: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=answer)

    chat = lme.Chat(model="m", base_url="https://api.example", key="k", reasoning=reasoning)
    chat.client = httpx.Client(transport=httpx.MockTransport(reply))
    return chat, sent


_ANSWER = {"choices": [{"message": {"content": "In May."}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 10, "completion_tokens": 40,
                     "completion_tokens_details": {"reasoning_tokens": 30}}}


def test_the_reader_reasons_at_the_shipped_level(lme):
    chat, sent = _chat(lme, "low", _ANSWER)
    text, usage = chat.ask("q", max_tokens=4000)
    assert text == "In May." and usage["reasoning_tokens"] == 30
    assert sent[0]["reasoning_effort"] == "low" and "thinking" not in sent[0]
    # Control: the judge asks with reasoning off, as before.
    chat, sent = _chat(lme, "off", _ANSWER)
    chat.ask("q", max_tokens=10)
    assert sent[0]["thinking"] == {"type": "disabled"} and "reasoning_effort" not in sent[0]
    with pytest.raises(ValueError):
        lme.Chat(model="m", base_url="https://api.example", key="k", reasoning="max")


def test_a_dropped_connection_is_tried_again_and_never_ends_the_run(lme, monkeypatch):
    """Qwen dropped a connection in a judge call (23 Nov, RemoteProtocolError)
    and the whole 200-question run ended. A dropped connection is tried again;
    past the tries it is a refused call (asked again on the next run).
    Control: a client that raises is what the run met."""
    import httpx

    monkeypatch.setattr(lme.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json=_ANSWER)

    chat = lme.Chat(model="m", base_url="https://api.example", key="k", reasoning="off")
    chat.client = httpx.Client(transport=httpx.MockTransport(flaky))
    assert chat.ask("q", max_tokens=10)[0] == "In May." and calls["n"] == 2

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    chat.client = httpx.Client(transport=httpx.MockTransport(down))
    assert chat.ask("q", max_tokens=10) == ("", {"error": "transport"})
    with pytest.raises(httpx.RemoteProtocolError):
        chat.client.post("https://api.example/chat/completions", json={})


def test_an_answer_cut_at_its_limit_is_said(lme):
    cut = {**_ANSWER, "choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    chat, _ = _chat(lme, "low", cut)
    _text, usage = chat.ask("q", max_tokens=4000)
    assert usage["cut_at_limit"] == 1


def _row(qid: str, verdicts: dict[str, bool], second: dict[str, bool] | None = None) -> dict:
    row = {"id": qid, "type": "multi-session"}
    for c, v in verdicts.items():
        row[c] = v
        if second is not None:
            row[f"{c}_judge2"] = second[c]
    return row


def test_the_second_judge_is_compared_with_the_first(lme):
    rows = [_row("a", {"kazma": True, "full": False}, {"kazma": True, "full": True}),
            _row("b", {"kazma": False, "full": False}, {"kazma": False, "full": False})]
    agree = lme.judges_agree(rows)
    assert agree["kazma"] == 1.0 and agree["full"] == 0.5 and agree["overall"] == 0.75 and agree["n"] == 4
    # Control: with no second judge there is nothing to compare.
    assert lme.judges_agree([_row("a", {"kazma": True})]) == {}


def test_the_second_judge_is_compared_on_every_condition_it_read(lme):
    """Kazma's turn and another build's block are judged twice too: the
    agreement names them (it read only the six base conditions)."""
    rows = [_row("a", {lme.TURN: True, "kazma_before": True}, {lme.TURN: False, "kazma_before": True})]
    agree = lme.judges_agree(rows)
    assert agree == {lme.TURN: 0.0, "kazma_before": 1.0, "overall": 0.5, "n": 2}
    # Control: the base conditions alone find nothing in this run.
    assert not [c for c in lme.CONDITIONS if f"{c}_judge2" in rows[0]]


def test_the_noise_run_counts_the_answers_that_flip(lme):
    a = {"results": [_row("a", {"kazma": True}), _row("b", {"kazma": False}), _row("c", {"kazma": True})]}
    b = {"results": [_row("a", {"kazma": True}), _row("b", {"kazma": True}), _row("c", {"kazma": False})]}
    report = lme.noise(a, b)
    assert report["questions"] == 3
    assert report["conditions"]["kazma"]["flips"] == 2
    assert report["conditions"]["kazma"]["flipped"] == ["b", "c"]
    # Control: a run compared with itself flips nothing.
    assert lme.noise(a, a)["conditions"]["kazma"]["flips"] == 0


def test_the_noise_run_counts_another_builds_block_too(lme):
    """The block another build made (``--blocks``) flips like any condition:
    W1a's before-and-after is read against both sides' noise."""
    a = {"conditions": ["kazma"], "blocks_from": {"kazma_before": "abc"},
         "results": [_row("a", {"kazma": True, "kazma_before": True}),
                     _row("b", {"kazma": True, "kazma_before": False})]}
    b = {"conditions": ["kazma"], "blocks_from": {"kazma_before": "abc"},
         "results": [_row("a", {"kazma": True, "kazma_before": False}),
                     _row("b", {"kazma": True, "kazma_before": False})]}
    report = lme.noise(a, b)
    assert report["conditions"]["kazma_before"]["flipped"] == ["a"]
    # Control: a run that did not ask for that block compares none.
    assert "kazma_before" not in lme.noise(a, {**b, "blocks_from": {}})["conditions"]


def test_the_answers_read_by_hand_start_with_the_judges_disagreements(lme):
    rows = [_row(f"q{n}", {"kazma": True, "full": n % 7 != 0}, {"kazma": True, "full": True}) for n in range(40)]
    picked = lme.pick_model_read(rows, size=10)
    split = [p for p in picked if p["condition"] == "full" and int(p["id"][1:]) % 7 == 0]
    assert len(picked) == 10 and len(split) == 6  # every disagreement (q0, q7, ..., q35), then the rest
    assert picked == lme.pick_model_read(rows, size=10)  # the same pick on every run
    labels = [{"judge": True, "judge2": False, "read": False}, {"judge": True, "judge2": True, "read": True}]
    summary = lme.model_read_summary(labels)
    assert summary["judge"] == 0.5 and summary["judge2"] == 1.0 and summary["judge2_on_disagreements"] == 1.0


def test_the_reading_sheet_stays_outside_the_repository(lme, tmp_path):
    assert lme._cmd_reading_sheet(tmp_path, tmp_path / "a.json", tmp_path / "c.json",
                                  REPO / "tests" / "fixtures" / "longmemeval" / "sheet.md") == 2
    assert not (REPO / "tests" / "fixtures" / "longmemeval" / "sheet.md").exists()


def test_a_cache_with_nothing_new_is_not_rewritten(lme, tmp_path):
    """Several runs read the vector cache at once: a run that embeds nothing
    leaves the file alone (rewritten under a reader, Windows refuses the
    replace)."""
    pytest.importorskip("numpy")

    class _Words:
        dim = 3

        def encode_batch(self, texts):
            return [[1.0, float(len(t)), 0.0] for t in texts]

    path = tmp_path / "vectors.npz"
    first = lme.CachedEmbedder(_Words(), path, "m")
    assert first.fill(["a", "bb"]) == 2 and path.is_file()
    stamp = path.stat().st_mtime_ns
    again = lme.CachedEmbedder(_Words(), path, "m")
    assert again.fill(["a", "bb"]) == 0
    assert path.stat().st_mtime_ns == stamp


class _Scripted:
    """A model that answers from a list: text, or a refusal (HTTP 402)."""

    def __init__(self, replies, model="judge"):
        self.replies = list(replies)
        self.asked = 0
        self.model = model

    def ask(self, prompt, *, max_tokens):
        self.asked += 1
        reply = self.replies.pop(0)
        return ("", {"error": reply}) if isinstance(reply, int) else (reply, {"prompt_tokens": 1})


def test_a_refused_call_is_asked_again_never_kept_as_wrong(lme):
    """The account ran out of balance mid-run (22 Nov, HTTP 402): a refused
    reader or judge call is asked again on the next run. Kept, a refused
    judge call read as "no" for good."""
    item = {"question_id": "q", "question_type": "single-session-user", "question": "?", "answer": "May"}
    entry: dict = {}
    reader, judge = _Scripted([402, "In May."]), _Scripted([402, "yes"])
    lme.answer_entry(entry, item, "prompt", reader=reader, judge=judge)
    assert "hypothesis" not in entry and entry["reader_error"] == 402 and judge.asked == 0
    lme.answer_entry(entry, item, "prompt", reader=reader, judge=judge)
    assert entry["hypothesis"] == "In May." and lme.verdict_of(entry, "judge") is None
    lme.answer_entry(entry, item, "prompt", reader=reader, judge=judge)
    assert lme.verdict_of(entry, "judge") == "yes" and reader.asked == 2 and judge.asked == 2
    # Another prompt is another question: asked afresh.
    lme.answer_entry(entry, item, "another prompt", reader=_Scripted(["June"]), judge=_Scripted(["no"]))
    assert entry["hypothesis"] == "June" and lme.verdict_of(entry, "judge") == "no"


def test_the_plain_baselines_are_also_measured_at_kazmas_size(lme):
    """Ten rounds always show ten (main session, 22 Nov): each baseline is
    also cut to Kazma's own size -- the rounds that fit its block's token
    budget, and as many rounds as Kazma showed -- with the tokens shown.
    Control: at Kazma's count of none, a baseline shows nothing."""
    item = _item("q6", "single-session-user", "Which degree did I graduate with?",
                 "I graduated with a degree in Business Administration last year.",
                 ["How do I boil an egg? " * 40, "Recommend a film about space. " * 40,
                  "What is the capital of Peru? " * 40])
    rows = lme.history_rows(item)
    one = lme.shown_tokens(lme.read_whole(rows[:1]))
    scores = lme.plain_scores(item, rows, _WordVectors(), budgets={"p50": one}, same_count=2)
    for how in ("bge", "bm25"):
        assert scores[f"plain_{how}_shown"] == len(rows) and scores[f"plain_{how}_tokens"] > one
        assert scores[f"plain_{how}_p50_shown"] == 1 and scores[f"plain_{how}_p50_evidence"]
        assert scores[f"plain_{how}_p50_tokens"] <= one
        assert scores[f"plain_{how}_same_shown"] == 2
    none = lme.plain_scores(item, rows, _WordVectors(), same_count=0)
    assert none["plain_bm25_same_shown"] == 0 and none["plain_bm25_same_tokens"] == 0
    assert not none["plain_bm25_same_evidence"]
    # A budget below one round still shows the best round, as a block would.
    assert len(lme.within_budget(rows, 1)) == 1
    assert lme.size_budgets([{"block_tokens": n} for n in (0, 10, 20, 30, 1000)]) == {"p50": 20, "p95": 1000}


def test_the_sizes_and_the_no_answer_side_are_summarised(lme):
    def row(qid: str, abstention: bool, shown: int, tokens: int, plain_tokens: int) -> dict:
        return {"id": qid, "type": "multi-session", "abstention": abstention, "shown": shown, "precision": 1.0,
                "evidence_turn": not abstention, "answer_session": True, "every_answer_session": True,
                "block_tokens": tokens, "latency_ms": 1.0, "plain_bge_evidence": True,
                "plain_bge_every_session": True, "plain_bge_shown": 10, "plain_bge_tokens": plain_tokens,
                "plain_bge_same_evidence": True, "plain_bge_same_every_session": True,
                "plain_bge_same_shown": shown, "plain_bge_same_tokens": tokens}

    rows = [row("a", False, 3, 600, 3000), row("b", False, 4, 700, 3200), row("c_abs", True, 0, 0, 2900)]
    overall = lme.summarise(rows)["overall"]
    assert overall["plain_bge_same_evidence"] == 1.0
    assert overall["sizes"]["kazma"]["tokens_p50"] == 600 and overall["sizes"]["plain_bge"]["shown_mean"] == 10
    assert overall["abstention_sizes"]["kazma"]["nothing"] == 1.0
    assert overall["abstention_sizes"]["plain_bge"]["nothing"] == 0.0


def test_a_result_file_keeps_each_row_on_one_line(lme):
    row = {"id": "a", "shown": 3, "plain_bge_evidence": True, "plain_bge_every_session": False,
           "plain_bge_shown": 10, "plain_bge_tokens": 5000, "plain_bge_same_evidence": True,
           "plain_bge_same_every_session": True, "plain_bge_same_shown": 3, "plain_bge_same_tokens": 900}
    compact = lme.compact_row(row)
    assert compact == {"id": "a", "shown": 3, "plain": {"bge_10": [True, False, 10, 5000],
                                                        "bge_same": [True, True, 3, 900]}}
    text = lme.dump_result({"what": "x", "overall": {"n": 2}, "results": [compact, compact]})
    assert json.loads(text)["results"] == [compact, compact]
    assert text.count("\n") < 12  # one line a row, not one a value


# ── Another provider (Qwen), its judges, and a change judged in one run ────


_QWEN_ANSWER = {"choices": [{"message": {"content": "In May.", "reasoning_content": "thinking..."},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11909, "completion_tokens": 120,
                          "prompt_tokens_details": {"cached_tokens": 11520, "text_tokens": 11909},
                          "completion_tokens_details": {"reasoning_tokens": 93, "text_tokens": 120}}}


def test_the_default_level_sends_nothing_and_qwens_cache_is_counted(lme):
    """Kazma sends a provider whose reasoning switch it does not know nothing
    (Qwen then thinks by its own default): ``default`` does the same. Qwen
    says the cache's share the OpenAI way; it is counted under one name."""
    chat, sent = _chat(lme, "default", _QWEN_ANSWER)
    text, usage = chat.ask("q", max_tokens=lme.READER_TOKENS["default"])
    assert "reasoning_effort" not in sent[0] and "thinking" not in sent[0]
    assert sent[0]["max_tokens"] == 16384 and text == "In May."
    assert usage["prompt_cache_hit_tokens"] == 11520 and usage["reasoning_tokens"] == 93
    # DeepSeek's own field, when there, is kept as it is.
    chat, _ = _chat(lme, "off", {**_ANSWER, "usage": {**_ANSWER["usage"], "prompt_cache_hit_tokens": 7}})
    assert chat.ask("q", max_tokens=10)[1]["prompt_cache_hit_tokens"] == 7


def test_blocks_from_another_checkout_cover_every_question(lme, tmp_path):
    path = tmp_path / "blocks.json"
    path.write_text(json.dumps({"commit": "064dfec3b", "blocks": {"q1": "block one", "q2": "block two"}}),
                    encoding="utf-8")
    held = lme.load_blocks(path, ["q1", "q2"])
    assert held == {"commit": "064dfec3b", "blocks": {"q1": "block one", "q2": "block two"}}
    with pytest.raises(SystemExit):
        lme.load_blocks(path, ["q1", "q3"])


def test_a_change_is_judged_question_by_question_and_against_the_noise(lme):
    rows = [_row("a", {"kazma": True, "kazma_before": False}), _row("b", {"kazma": False, "kazma_before": True}),
            _row("c", {"kazma": True, "kazma_before": True}), _row("d", {"kazma": True, "kazma_before": False})]
    pair = lme.pair_changes(rows, "kazma", "kazma_before")
    assert (pair["gained"], pair["lost"], pair["net"]) == (2, 1, 1)
    assert pair["gained_ids"] == ["a", "d"] and pair["lost_ids"] == ["b"]
    # The noise run reads every condition both runs answered, the blocks'
    # and the turn's too.
    a = {"conditions": ["kazma", "kazma_before", "kazma_turn"], "results": rows}
    b = {"conditions": ["kazma", "kazma_before"], "results": [{**r, "kazma_before": not r["kazma_before"]}
                                                              for r in rows]}
    out = lme.noise(a, b)
    assert set(out["conditions"]) == {"kazma", "kazma_before"}
    assert out["conditions"]["kazma_before"]["flips"] == 4 and out["conditions"]["kazma"]["flips"] == 0


def test_the_blocks_are_built_by_the_checkout_named(tmp_path):
    """The export loads the named checkout's harness and memory code, and
    refuses to write the dataset's text into a checkout."""
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_blocks
    finally:
        sys.path.pop(0)
    bench = longmemeval_blocks.use_checkout(REPO)
    assert Path(bench.__file__).resolve().parent == REPO / "scripts"
    assert longmemeval_blocks.main(["--checkout", str(REPO), "--data", str(tmp_path),
                                    "--out", str(REPO / "blocks.json")]) == 2
    assert not (REPO / "blocks.json").exists()


# ── Verdicts kept by the model that gave them (the judges swapped, 22 Nov) ──


def _old_judge_entry(entry, item, *, judge, second=None, lme):
    """The judging as it was before 22 Nov: verdicts kept by place."""
    if "verdict" not in entry:
        verdict, usage = judge.ask(lme.judge_prompt(item, entry["hypothesis"]), max_tokens=10)
        entry.update({"verdict": verdict, "judge_usage": usage})
    if second is not None and "verdict2" not in entry:
        verdict, usage = second.ask(lme.judge_prompt(item, entry["hypothesis"]), max_tokens=10)
        entry.update({"verdict2": verdict, "judge2_usage": usage})


_ITEM = {"question_id": "q", "question_type": "single-session-user", "question": "?", "answer": "May"}


def test_a_verdict_stays_with_the_model_that_gave_it(lme):
    """The main session swapped the judges (22 Nov): the stronger judge
    first, from the cached verdicts. Kept by model, the run with the judges
    in the other order asks nothing again and reads each judge's own verdict.
    Control: kept by place, the swapped run asks nothing either, and hands
    the old first judge's verdict to the new first judge."""
    plus, flash = _Scripted(["no"], model="plus"), _Scripted(["yes"], model="flash")
    entry = {"hypothesis": "In May, and it rained."}
    lme.judge_entry(entry, _ITEM, judge=plus, second=flash)
    lme.judge_entry(entry, _ITEM, judge=flash, second=plus)
    assert (plus.asked, flash.asked) == (1, 1)
    columns = lme.condition_columns(entry, "kazma", "flash", "plus")
    assert columns["kazma"] is True and columns["kazma_judge2"] is False
    # Control: the old storage gives flash plus's "no".
    old = {"hypothesis": "In May, and it rained."}
    plus, flash = _Scripted(["no"], model="plus"), _Scripted(["yes"], model="flash")
    _old_judge_entry(old, _ITEM, judge=plus, second=flash, lme=lme)
    _old_judge_entry(old, _ITEM, judge=flash, second=plus, lme=lme)
    assert (plus.asked, flash.asked) == (1, 1) and old["verdict"] == "no"


def test_a_cache_kept_by_place_is_refused_until_its_judges_are_named(lme):
    entry = {"hypothesis": "May", "verdict": "yes", "judge_usage": {"prompt_tokens": 3},
             "verdict2": "no", "judge2_usage": {"prompt_tokens": 4}}
    cache = {"q": {"kazma": entry, "full": {"reader_error": 402}}}
    with pytest.raises(ValueError, match="tag-judges"):
        lme.judge_entry(entry, _ITEM, judge=_Scripted([], model="plus"))
    assert lme._untagged_in(cache) == 1
    assert lme.tag_judges(cache, "plus", "flash") == 2
    assert lme._untagged_in(cache) == 0 and "verdict" not in entry and "judge_usage" not in entry
    assert lme.verdict_of(entry, "plus") == "yes" and lme.verdict_of(entry, "flash") == "no"
    assert entry["judged"]["flash"]["usage"] == {"prompt_tokens": 4}
    # A model already holding another verdict for the answer is refused.
    entry["verdict"] = "no"
    with pytest.raises(ValueError, match="already holds"):
        lme.tag_judges(cache, "plus", "flash")
    with pytest.raises(ValueError, match="two models"):
        lme.tag_judges({}, "plus", "plus")


def _judged_run(lme):
    """A small run as ``answer`` writes it: two questions, two histories,
    judged by plus first and flash second."""
    verdicts = {("a", "kazma"): ("no", "yes"), ("a", "full"): ("yes", "yes"),
                ("b", "kazma"): ("yes", "no"), ("b", "full"): ("no", "yes")}
    cache = {qid: {c: {"prompt_sha": "x", "hypothesis": "h", "reader_usage": {"prompt_tokens": 100},
                       "judged": {"plus": {"verdict": verdicts[qid, c][0], "usage": {"prompt_tokens": 5}},
                                  "flash": {"verdict": verdicts[qid, c][1], "usage": {"prompt_tokens": 6}}}}
                   for c in ("kazma", "full")} for qid in ("a", "b")}
    conditions = ("kazma", "full")
    results = []
    for qid in ("a", "b"):
        row = {"id": qid, "type": "multi-session", "abstention": False, "kazma_shown": 3,
               "full_sessions_dropped": 0}
        for c in conditions:
            row.update(lme.condition_columns(cache[qid][c], c, "plus", "flash"))
        results.append(row)
    summary = lme.run_summary(results, conditions, judge2="flash")
    run = {"split": "dev", "reader": "plus", "judge": "plus", "judge2": "flash", "conditions": list(conditions),
           "blocks_from": {}, "made_on": "2026-11-22", "usage": lme.run_usage(cache, ["a", "b"], conditions,
                                                                             ("plus", "flash")),
           **summary, "results": results}
    return run, cache


def test_rejudge_reorders_the_judges_from_the_cache_alone(lme):
    """``rejudge`` rebuilds a result file with the judges in another order,
    from the cache, asking no model. In the same order it gives the file
    back exactly (the control that it rebuilds what ``answer`` wrote)."""
    run, cache = _judged_run(lme)
    assert json.dumps(lme.rejudge(run, cache, "plus", "flash")) == json.dumps(run)
    swapped = lme.rejudge(run, cache, "flash", "plus")
    assert (swapped["judge"], swapped["judge2"]) == ("flash", "plus")
    assert swapped["rejudged"]["judges_before"] == ["plus", "flash"]
    assert swapped["overall"]["kazma"] == 0.5 and swapped["overall"]["full"] == 1.0
    assert run["overall"]["kazma"] == 0.5 and run["overall"]["full"] == 0.5
    row = swapped["results"][0]
    assert row["kazma"] is True and row["kazma_judge2"] is False and row["kazma_shown"] == 3
    assert swapped["usage"] == run["usage"] and swapped["judges_agree"]["overall"] == run["judges_agree"]["overall"]
    # A judge with no verdict on an answer is refused, never counted wrong.
    with pytest.raises(ValueError, match="other holds no verdict on 4"):
        lme.rejudge(run, cache, "other", "plus")
    del cache["b"]["full"]["judged"]["flash"]
    with pytest.raises(ValueError, match="flash holds no verdict on 1"):
        lme.rejudge(run, cache, "flash", "plus")
    # A judge asked for the verdict it lacks (``judge``) fills it.
    flash = _Scripted(["no"], model="flash")
    lme.judge_entry(cache["b"]["full"], _ITEM, judge=flash, second=_Scripted([], model="plus"))
    assert flash.asked == 1 and lme.rejudge(run, cache, "flash", "plus")["overall"]["full"] == 0.5


def test_the_reading_s_finding_is_kept_beside_its_labels(lme, tmp_path, monkeypatch):
    run, _cache = _judged_run(lme)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(run), encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({f"{p['id']}|{p['condition']}": True
                                  for p in lme.pick_model_read(run["results"])}), encoding="utf-8")
    out = tmp_path / "model_read.json"
    monkeypatch.setattr(lme, "MODEL_READ", out)
    assert lme._cmd_model_read(answers, labels, "The first judge refuses answers that add a detail.") == 0
    held = json.loads(out.read_text(encoding="utf-8"))
    assert held["found"] == "The first judge refuses answers that add a detail." and held["judge"] == "plus"
    assert lme._cmd_model_read(answers, labels) == 0 and "found" not in json.loads(out.read_text(encoding="utf-8"))
