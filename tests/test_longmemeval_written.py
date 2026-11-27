"""LongMemEval's memories written as Kazma writes them (the memory plan's W6,
step 5; ``scripts/longmemeval_written.py``), on a small history of our own in
LongMemEval's shape, with a scripted model: no dataset text, no model call.

* One seeding for Kazma's conditions: the question's written store holds the
  deep pass's facts, the summary of its ended week and the nightly merge, and
  the recall block shows them in the harness's block AND in Kazma's own turn.
  Controls: today's seeding (episodes alone) shows none of them; a week that
  had not ended at the question's time is not summarized (its model call is
  never made); the facts run's twin stays two facts without the merge.
* A store lacking a step refuses the run (``answer --written``); with every
  step the run reads the written stores, keeps today's block as the labelled
  ``kazma_episodes`` row and says what each condition was written with.
* Weeks and months are read in UTC, the benchmark's zone.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
RUN, LABEL = "facts-run", "written-run"


@pytest.fixture(scope="module")
def scripts():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_turn
        import longmemeval_written
    finally:
        sys.path.pop(0)
    return longmemeval_written, longmemeval_turn


FUNDRAISER = ["Our pottery club is planning a spring fundraiser, can you help?",
              "For the fundraiser we want a raffle of handmade bowls.",
              "The fundraiser raffle tickets will cost five dollars each.",
              "We fixed the fundraiser raffle for the twentieth of May."]
RENOVATION = ["We are starting a kitchen renovation next month.",
              "The kitchen renovation needs new cabinets and tiles.",
              "Which tiles suit a kitchen renovation with oak cabinets?",
              "The kitchen renovation budget is twelve thousand dollars."]


ASKED = "What did our pottery club plan for the fundraiser raffle?"
#: The question the deep pass's event answers.
WHEN = "When did I go to the pottery exhibition?"


def _item(question: str = ASKED) -> dict:
    """Week 2023-W18 (1-7 May) holds the fundraiser chat and the pottery
    exhibition; the renovation chat is on 31 May, a week that had not ended
    when the question was asked (1 June)."""
    sessions = [
        ([("I went to a pottery exhibition at the craft centre last Saturday.", True)], "2023/05/02 (Tue) 09:00"),
        ([(text, False) for text in FUNDRAISER], "2023/05/03 (Wed) 18:00"),
        ([(text, False) for text in RENOVATION], "2023/05/31 (Wed) 09:00"),
    ]
    return {"question_id": "written_q", "question_type": "multi-session",
            "question": question, "answer": "handmade bowls",
            "question_date": "2023/06/01 (Thu) 10:00",
            "haystack_sessions": [[m for text, evidence in said for m in (
                {"role": "user", "content": text, **({"has_answer": True} if evidence else {})},
                {"role": "assistant", "content": "Noted, thanks for telling me."})] for said, _ in sessions],
            "haystack_session_ids": [f"s{n}" for n in range(len(sessions))],
            "haystack_dates": [date for _, date in sessions], "answer_session_ids": ["s1"]}


EVENT = {"subject": "user", "predicate": "attended", "predicate_type": "set",
         "object": "a pottery exhibition at the craft centre", "on": "2023-04-29", "confidence": 0.9}
SUMMARY = {"title": "Pottery club spring fundraiser",
           "summary": "The user's pottery club planned a spring fundraiser: a raffle of handmade bowls, "
                      "tickets five dollars each, drawn on 20 May.", "skip": False}


class Model:
    """A scripted provider in Kazma's shape: the deep pass's event for the
    pottery turn, a summary for the fundraiser's week; every message kept."""

    def __init__(self) -> None:
        self.config = type("C", (), {"model": "scripted"})()
        self.asked: list[str] = []

    async def chat(self, messages, **_kw):
        from kazma_core.llm_provider import LLMResponse

        said = "\n".join(str(m.get("content") or "") for m in messages)
        self.asked.append(said)
        if "memory of one topic" in said:
            reply = SUMMARY if "fundraiser" in said else {"title": "Kitchen renovation", "summary": "x", "skip": False}
        elif "longer stretch" in said:
            reply = {"title": "", "summary": "", "skip": True}
        else:
            reply = {"beliefs": [EVENT] if "pottery exhibition" in messages[-1]["content"] else []}
        return LLMResponse(content=json.dumps(reply), model="scripted",
                           usage={"prompt_tokens": 100, "completion_tokens": 10})


@pytest.fixture
def private(tmp_path, monkeypatch):
    import kazma_core.paths as paths
    from kazma_core.memory import recall, task_queue

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("KAZMA_OPS_ALERTS", "0")
    monkeypatch.delenv("KAZMA_MEMORY_STATE_DB", raising=False)
    # Restored afterwards: the scripts route these for the run's process.
    for module, name in ((paths, "primary_memory_db"), (paths, "memory_ops_db"),
                         (recall, "_memory_zone"), (task_queue, "enqueue_task")):
        monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    data = tmp_path / "data"
    for sub in ("stores", "done"):
        (data / "cache" / "facts-pilot" / RUN / sub).mkdir(parents=True)
    return data


def _facts(written, data: Path, model: Model) -> Path:
    """The facts run: the pilot's extract, through the product's deep pass."""
    pilot = written.pilot
    folder = data / "cache" / "facts-pilot" / RUN
    cached = pilot.CachedModel(model, folder / "replies.jsonl")
    registry = type("Registry", (), {"get_client": lambda self, *_a, **_k: cached})()
    pilot._route_memory_paths()
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry), \
            mock.patch("kazma_core.memory.belief_extractor._turn_zone", lambda: UTC):
        asyncio.run(pilot._extract([_item()], folder, cached, workers=1))
    return folder


def _twin(store: Path) -> None:
    """The facts run's event told twice, in other letters: the nightly merge's
    work (it keeps one)."""
    conn = sqlite3.connect(str(store))
    try:
        row = conn.execute("SELECT * FROM beliefs WHERE predicate = 'attended'").fetchone()
        names = [d[0] for d in conn.execute("SELECT * FROM beliefs LIMIT 0").description]
        twin = dict(zip(names, row, strict=True))
        twin.update(id=twin["id"] + "-twin", object=twin["object"].upper(), confidence=0.5)
        conn.execute(f"INSERT INTO beliefs ({','.join(twin)}) VALUES ({','.join('?' * len(twin))})",
                     list(twin.values()))
        conn.commit()
    finally:
        conn.close()


def _attended(store: Path) -> int:
    conn = sqlite3.connect(str(store))
    try:
        return conn.execute("SELECT COUNT(*) FROM beliefs WHERE predicate = 'attended' "
                            "AND valid_until IS NULL AND invalidated_at IS NULL").fetchone()[0]
    finally:
        conn.close()


def _write(written, data: Path, model: Model) -> dict:
    folder = written.written_folder(data, LABEL)
    for sub in ("stores", "done"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    cached = written.pilot.CachedModel(model, folder / "replies.jsonl")
    return asyncio.run(written._write([_item()], data / "cache" / "facts-pilot" / RUN, folder, cached, workers=1))


@pytest.fixture
def made(scripts, private):
    """The facts run (its event told twice), then the written run."""
    written, _turn = scripts
    model = Model()
    facts = _facts(written, private, model)
    _twin(facts / "stores" / "written_q.db")
    asked_for_facts = len(model.asked)
    counts = _write(written, private, model)
    return {"data": private, "facts": facts, "model": model, "counts": counts,
            "summaries_asked": model.asked[asked_for_facts:]}


def test_the_written_store_holds_the_facts_one_summary_and_the_merge(scripts, made):
    written, _turn = scripts
    bench = written.bench
    assert made["counts"] == {"written": 1, "held": 0, "unwritten": 0}
    store = written.written_folder(made["data"], LABEL) / "stores" / "written_q.db"
    done = json.loads((store.parent.parent / "done" / "written_q.json").read_text(encoding="utf-8"))
    steps = done["written_with"]
    assert steps["facts"]["run"] == RUN
    assert {k: steps["summaries"][k] for k in ("model", "weeks", "rollups", "tasks", "weeks_left")} == {
        "model": "scripted", "weeks": 1, "rollups": 0, "tasks": 1, "weeks_left": 0}
    assert done["weekly"] == 1 and steps["merge"]["merged"] == 1
    # The merge's control: the facts run keeps the twin.
    assert _attended(made["facts"] / "stores" / "written_q.db") == 2 and _attended(store) == 1
    rows = bench.history_rows(_item())
    block, _whole, shown = bench.kazma_history(_item(), rows, store)
    assert "Pottery club spring fundraiser" in block and shown["summaries"] == 1
    block, _whole, shown = bench.kazma_history(_item(WHEN), rows, store)
    assert "[on 29 Apr 2023] user attended a pottery exhibition at the craft centre" in block
    assert block.lower().count("attended a pottery exhibition") == 1 and shown["facts"] == 1
    # Control: today's seeding, the episodes alone.
    today, _whole, shown = bench.kazma_history(_item(), rows)
    assert "fundraiser" in today and "Pottery club spring fundraiser" not in today and shown["summaries"] == 0
    today, _whole, shown = bench.kazma_history(_item(WHEN), rows)
    assert "user attended" not in today and shown["facts"] == 0


def test_a_week_not_ended_at_the_questions_time_is_not_summarized(scripts, made):
    written, _turn = scripts
    asked = made["summaries_asked"]
    assert any("fundraiser" in said for said in asked)
    assert not any("renovation" in said for said in asked if "memory of one topic" in said)
    # Its week ends on 4 June: due a day later, not at the question.
    store = written.written_folder(made["data"], LABEL) / "stores" / "written_q.db"
    token = written.pilot._STORE.set((str(store), str(store.with_suffix(".ops.db"))))
    try:
        late = written.bench.parse_date("2023/06/06 (Tue) 10:00")
        assert [key for *_rest, key in written.due_weeks(late)] == ["2023-W22"]
    finally:
        written.pilot._STORE.reset(token)


def test_the_turn_reads_the_written_store(scripts, made):
    written, turn = scripts
    bench = written.bench
    from tests._no_models import keep_models_out

    store = written.written_folder(made["data"], LABEL) / "stores" / "written_q.db"
    blocks: list[str] = []

    class Reader:
        def can_turn_off_reasoning(self) -> bool:
            return True

        async def chat(self, *, messages, tools=None, model=None, **_kw):
            from kazma_core.llm_provider import LLMResponse

            blocks.append(next((str(m.get("content")) for m in messages
                                if str(m.get("content") or "").lstrip().startswith("## Memory recall rules")), ""))
            return LLMResponse(content="Handmade bowls.", finish_reason="stop", model="stand-in",
                               usage={"prompt_tokens": 10, "completion_tokens": 2})

    with pytest.MonkeyPatch.context() as mp:
        keep_models_out(mp, llm=False)
        mp.setenv("KAZMA_COMMITMENT_ENABLED", "0")
        rows = bench.history_rows(_item())

        def ask(seed, question=ASKED):
            return asyncio.run(turn.run_turn(
                question, now=bench.parse_date(_item()["question_date"]), tenant=bench.TENANT,
                seed=seed, client=turn.CountingClient(Reader()), system_prompt="BASE", thread_id="lme-w"))

        assert "Pottery club spring fundraiser" in ask(bench.seed_from(store, rows)).block
        assert "user attended a pottery exhibition" in ask(bench.seed_from(store, rows), WHEN).block
        # Control: the turn on today's seeding.
        assert "Pottery club spring fundraiser" not in ask(bench.seed_from(None, rows)).block
        assert "user attended" not in ask(bench.seed_from(None, rows), WHEN).block


def _answer(written, data: Path, monkeypatch) -> tuple:
    bench = written.bench
    seen: dict[str, str] = {}

    class Chat:
        def __init__(self, *, model, **_kw):
            self.model = model

        def ask(self, prompt, *, max_tokens):
            return "yes", {"prompt_tokens": 1, "completion_tokens": 1}

    def history(item, rows, store=None):
        seen["store" if store else "episodes"] = str(store)
        return ("written block" if store else "today's block", "whole", {"shown": 1, "facts": 0})

    monkeypatch.setenv("DEMO_KEY", "demo-key")
    monkeypatch.setattr(bench, "_private_data_folder", lambda: None)
    monkeypatch.setattr(bench, "load", lambda _data: [_item()])
    monkeypatch.setattr(bench, "question_ids", lambda _split: ["written_q"])
    monkeypatch.setattr(bench, "kazma_history", history)
    monkeypatch.setattr(bench, "plain_top", lambda *a, **k: [])
    monkeypatch.setattr(bench, "Chat", Chat)
    monkeypatch.setattr(bench, "CachedEmbedder", lambda *a, **k: None)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    out = data / "answers.json"
    monkeypatch.setattr(bench, "result_path", lambda base, split: out)
    code = bench._cmd_answer(data, None, "demo", "http://demo.test", 1, split="dev", key_env="DEMO_KEY",
                             conditions=("kazma", "kazma_whole"), written=LABEL)
    return code, seen, (json.loads(out.read_text(encoding="utf-8")) if out.is_file() else None)


def test_a_run_reads_the_written_stores_and_keeps_todays_block_labelled(scripts, made, monkeypatch):
    written, _turn = scripts
    code, seen, result = _answer(written, made["data"], monkeypatch)
    assert code == 0 and seen["store"].endswith("written_q.db") and "episodes" in seen
    assert result["conditions"] == ["kazma", "kazma_whole", "kazma_episodes"]
    said = result["written_with"]
    assert said["kazma_episodes"] == written.bench.WRITTEN_EPISODES
    assert said["kazma"] == said["kazma_whole"] and f"the facts of {RUN}" in said["kazma"]
    assert "kazma_vs_kazma_episodes" in result


@pytest.mark.parametrize("step", ["episodes", "facts", "summaries", "merge"])
def test_a_store_lacking_a_step_refuses_the_run(scripts, made, monkeypatch, step):
    written, _turn = scripts
    done = written.written_folder(made["data"], LABEL) / "done" / "written_q.json"
    held = json.loads(done.read_text(encoding="utf-8"))
    del held["written_with"][step]
    done.write_text(json.dumps(held), encoding="utf-8")
    code, seen, result = _answer(written, made["data"], monkeypatch)
    assert code == 2 and not seen and result is None


def _undone(store: Path, done: Path, how: str) -> None:
    """Leave one step undone in *store*, or say so in its *done* file."""
    held = json.loads(done.read_text(encoding="utf-8"))
    if how == "merge_errors":
        held["written_with"]["merge"]["errors"] = 1
    elif how == "weeks_left":
        held["written_with"]["summaries"]["weeks_left"] = 1
    elif how == "no_question_time":
        held["written_with"].pop("at")
    done.write_text(json.dumps(held), encoding="utf-8")
    conn = sqlite3.connect(str(store))
    try:
        if how == "week_queued":
            conn.execute("UPDATE memory_summary_periods SET status = 'queued'")
        elif how == "summary_to_rebuild":
            conn.execute("UPDATE memory_summaries SET status = 'rebuild'")
        elif how == "only_summaries_gone":
            # the done markers kept (the final review's L3)
            conn.execute("DELETE FROM memory_summary_sources")
            conn.execute("DELETE FROM memory_summaries")
        elif how == "summaries_gone":
            # the done file still says no week is left (the delta review's E)
            conn.execute("DELETE FROM memory_summary_sources")
            conn.execute("DELETE FROM memory_summaries")
            conn.execute("DELETE FROM memory_summary_periods")
        elif how == "twin_left":
            row = conn.execute("SELECT * FROM beliefs WHERE predicate = 'attended' AND valid_until IS NULL").fetchone()
            names = [d[0] for d in conn.execute("SELECT * FROM beliefs LIMIT 0").description]
            twin = dict(zip(names, row, strict=True))
            twin.update(id=twin["id"] + "-left", object=twin["object"].upper())
            conn.execute(f"INSERT INTO beliefs ({','.join(twin)}) VALUES ({','.join('?' * len(twin))})",
                         list(twin.values()))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("how", ["merge_errors", "weeks_left", "week_queued", "summary_to_rebuild", "twin_left",
                                 "summaries_gone", "only_summaries_gone", "no_question_time"])
def test_a_store_left_unfinished_refuses_the_run(scripts, made, monkeypatch, how):
    """The step 5 review, M2: the refusal reads the store itself and each
    step's own counts, not only that the done file names the step."""
    written, _turn = scripts
    folder = written.written_folder(made["data"], LABEL)
    _undone(folder / "stores" / "written_q.db", folder / "done" / "written_q.json", how)
    assert written.bench.store_unfinished(folder / "stores" / "written_q.db",
                                          json.loads((folder / "done" / "written_q.json").read_text())["written_with"])
    code, seen, result = _answer(written, made["data"], monkeypatch)
    assert code == 2 and not seen and result is None


def test_control_a_finished_store_has_nothing_undone(scripts, made):
    written, _turn = scripts
    folder = written.written_folder(made["data"], LABEL)
    steps = json.loads((folder / "done" / "written_q.json").read_text(encoding="utf-8"))["written_with"]
    assert written.bench.store_unfinished(folder / "stores" / "written_q.db", steps) == []


def test_the_rounds_never_answered_are_said_beside_the_number(scripts):
    written, _turn = scripts
    steps = {"a": {"facts": {"run": "r", "facts": "replayed", "deep": 90, "no_reply": 8, "deep_failed": 2},
                   "summaries": {"model": "m"}},
             "b": {"facts": {"run": "r", "facts": "replayed", "deep": 100, "no_reply": 0, "deep_failed": 0},
                   "summaries": {"model": "m"}}}
    assert "10 of 200 rounds' deep pass never answered" in written.bench.written_with("w", steps)
    for step in steps.values():
        step["facts"].update(no_reply=0, deep_failed=0)
    assert "never answered" not in written.bench.written_with("w", steps)


def test_a_question_without_its_facts_is_left_unwritten(scripts, private):
    written, _turn = scripts
    model = Model()
    counts = _write(written, private, model)
    assert counts == {"written": 0, "held": 0, "unwritten": 1} and not model.asked
    assert written.bench.written_stores(private, LABEL, ["written_q"])[2] == ["written_q"]


def test_weeks_and_months_are_read_in_the_operators_zone(scripts, monkeypatch):
    """The product reads weeks and months in the operator's zone, as recall
    reads days (the step 5 review, L2); the run patches that zone to UTC.
    Sunday 30 Apr 2023 22:30 UTC is that week's in UTC and Monday's (the
    next week's) in Kuwait, UTC+3."""
    from zoneinfo import ZoneInfo

    from kazma_core.memory import recall, topic_rollups, topic_summaries

    written, _turn = scripts

    def at(text):
        return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()

    monkeypatch.setattr(recall, "_memory_zone", lambda: UTC)
    late_sunday = at("2023-04-30T22:30:00")
    assert topic_summaries._week_of(late_sunday)[0] == "2023-W17"
    assert topic_summaries._week_of(at("2023-05-03T12:00:00"))[1:] == (at("2023-05-01T00:00:00"),
                                                                        at("2023-05-08T00:00:00"))
    assert topic_rollups._month_of(at("2023-12-31T23:30:00")) == ("2023-12", at("2023-12-01T00:00:00"),
                                                                  at("2024-01-01T00:00:00"))
    monkeypatch.setattr(recall, "_memory_zone", lambda: ZoneInfo("Asia/Kuwait"))
    assert topic_summaries._week_of(late_sunday)[0] == "2023-W18"
    assert topic_rollups._month_of(at("2023-12-31T23:30:00"))[0] == "2024-01"
    # The run's own patch is that zone, UTC.
    written.in_utc()
    assert recall._memory_zone() is UTC


def test_the_nightly_merge_uses_the_cadences_numbers(scripts):
    """The merge runs with the numbers the product's reconsolidation cadence
    queues (read from its source)."""
    written, _turn = scripts
    source = (REPO / "kazma-core" / "kazma_core" / "memory" / "worker_bootstrap.py").read_text(encoding="utf-8")
    numbers = ", ".join(f'"{k}": {v}' for k, v in written.NIGHTLY.items())
    assert f'"global_reconsolidation",\n                                       {{{numbers}}}' in source


def test_the_pilots_evidence_holds_ids_and_counts_and_says_what_was_measured():
    """The step's measured run as committed (``longmemeval_written.py
    --evidence``): no dataset text; every question written with each step;
    a block changes exactly where it shows a summary, and the nightly merge
    changed no fact line (the step 5 review, H2); the answer runs are named
    with the blocks they were asked on, and no claim of a gain rests on them."""
    evidence = REPO / "docs" / "audits" / "evidence" / "memory_ingestion" / "pilot_written.json"
    held = json.loads(evidence.read_text(encoding="utf-8"))
    assert not {"question", "answer", "haystack_sessions", "content"} & _keys(held)
    rows = held["questions"]
    assert len(rows) == 50 and all(r["sweep"]["weeks_left"] == 0 for r in rows)
    assert all(r["changed"] == (r["blocks"]["written"]["summary_lines"] > 0) for r in rows)
    assert not any(r["fact_lines_changed"] for r in rows)
    assert "eb58ed364" in held["answers_note"]
    for path in held["answers"]:
        run = json.loads((REPO / path).read_text(encoding="utf-8"))
        assert run["written_with"]["kazma"].startswith("written run w6-5-qwen")


def _keys(value) -> set:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def test_sweeps_that_do_not_settle_say_so_apart_from_a_failed_call(scripts, private):
    """The step 5 review, L5: a question whose sweeps run out is said as that
    (run again with more), never as a model call that failed."""
    written, _turn = scripts
    model = Model()
    facts = _facts(written, private, model)
    folder = written.written_folder(private, LABEL)
    for sub in ("stores", "done"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    cached = written.pilot.CachedModel(model, folder / "replies.jsonl")
    out = asyncio.run(written._write([_item()], facts, folder, cached, workers=1, sweeps=1))
    assert out["unwritten"] == 1 and not (folder / "done" / "written_q.json").exists()
    with mock.patch.object(written, "SWEEPS", 1):
        reason = asyncio.run(_one_question_reason(written, private, facts, folder, cached))
    assert reason.startswith("1 sweep did not settle")
    # Control: enough sweeps write it.
    assert asyncio.run(written._write([_item()], facts, folder, cached, workers=1, sweeps=10))["written"] == 1


async def _one_question_reason(written, data, facts, folder, cached):
    written.pilot._route_memory_paths()
    written.route_queue()
    written.in_utc()
    facts_with = written.facts_written_with(facts, "written_q")
    with written.as_the_active_model(cached):
        return await written.write_question(_item(), facts, folder, cached.config.model, facts_with, sweeps=1)
