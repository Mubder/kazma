"""A prompt change waits for its recording, declared (framework audit part C2).

Part C2 changes the deep pass's prompt several times (the date, the previous
turn, the subject's current facts, retractions, the fenced input), and the
plan records the model once, after all of them, with the owner's yes
(decision 2: 150 askings, 300 at most). Until then the recording answers the
prompt before the changes, and the benchmark refused it, as it must: with
the prompt replaced by "Keep nothing" every model-pass score used to stay
the same (benchmark audit F4).

So a change is declared in the lock (``lock --prompt-changed "why"``): the
prompt it waits for, the recording it replaces, each change with its date
and reason. Until the recording, the model's pass replays the recording of
the prompt before the change through today's parser, sanitizer and scorer
-- a drift check, compared exactly -- and the benchmarks page says the rows
do not measure the new prompt. An undeclared change is refused, as before.

The product and the benchmark build the deep pass's message with one
function (``belief_extractor.turn_message``), and a recording says the form
it was asked in (``message_shape``).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from kazma_core.memory import belief_extractor, benchmark

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"


def _script():
    spec = importlib.util.spec_from_file_location("_memory_bench_pending", REPO / "scripts" / "memory_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _page():
    spec = importlib.util.spec_from_file_location("_benchmarks_page_pending", REPO / "scripts" / "benchmarks_page.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _recording() -> dict:
    return json.loads((FIXTURES / "extraction_llm.json").read_text(encoding="utf-8"))


def _locked() -> dict:
    return json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8"))


def _llm_scores(scores: dict) -> dict:
    return {k: v for k, v in scores.items() if k.startswith("llm.")}


def _change_the_prompt(monkeypatch, words: str = "A change waiting for its recording.") -> None:
    monkeypatch.setattr(belief_extractor, "_EXTRACT_SYSTEM", belief_extractor._EXTRACT_SYSTEM + "\n" + words)


def _declared(monkeypatch, recording: dict) -> dict:
    """The declaration today's lock would write for the changed prompt."""
    pending, problems = _script()._declare_prompt_change(recording, _pending_in_lock(), "the date for the extractor")
    assert not problems, problems
    return pending


def _pending_in_lock() -> dict | None:
    return (_locked().get("extraction") or {}).get("pending_recording")


def _today_mode() -> str:
    return benchmark._check_recording(_recording(), _pending_in_lock())


def test_a_declared_change_replays_the_previous_recording_and_keeps_the_locked_scores(monkeypatch):
    if _today_mode() == "previous":
        pytest.skip("the lock already declares today's prompt: the gate in test_memory_benchmark replays it")
    recording, locked = _recording(), _locked()["extraction"]
    _change_the_prompt(monkeypatch)
    pending = _declared(monkeypatch, recording)
    assert pending["prompt_sha256"] == benchmark._extraction_prompt_sha256()
    assert pending["replaces_prompt_sha256"] == recording["prompt_sha256"]
    report = benchmark.run_extraction_benchmark(llm_replies=recording, pending=pending)
    assert report["llm_replay"] == "previous"
    assert _llm_scores(benchmark.extraction_scores(report)) == _llm_scores(locked["scores"])


def test_control_an_undeclared_change_is_refused(monkeypatch):
    recording = _recording()
    _change_the_prompt(monkeypatch, "An undeclared change.")
    with pytest.raises(benchmark.StaleRecordingError, match="prompt changed.*--prompt-changed"):
        benchmark.run_extraction_benchmark(llm_replies=recording, pending=_pending_in_lock())


def test_a_declaration_names_one_prompt_and_a_later_change_is_refused_until_declared(monkeypatch):
    recording = _recording()
    _change_the_prompt(monkeypatch, "First change.")
    pending = _declared(monkeypatch, recording)
    _change_the_prompt(monkeypatch, "Second change.")
    with pytest.raises(benchmark.StaleRecordingError, match="prompt changed"):
        benchmark.run_extraction_benchmark(llm_replies=recording, pending=pending)
    again, problems = _script()._declare_prompt_change(recording, pending, "the previous turn too")
    assert not problems
    assert again["since"] == pending["since"]
    assert [c["why"] for c in again["changes"]][-2:] == ["the date for the extractor", "the previous turn too"]
    assert benchmark._check_recording(recording, again) == "previous"


def test_the_drift_check_sees_the_code_that_reads_the_replies(monkeypatch):
    """Negative control: the replay measures today's parser. A sanitizer that
    drops every fact the model returned loses the found facts."""
    if _today_mode() == "previous":
        pending = _pending_in_lock()
    else:
        _change_the_prompt(monkeypatch)
        pending = _declared(monkeypatch, _recording())
    monkeypatch.setattr(belief_extractor, "_sanitize_belief", lambda _b: None)
    report = benchmark.run_extraction_benchmark(llm_replies=_recording(), pending=pending)
    assert all(row["gold_found"] == 0 for row in report["llm"].values()), report["llm"]
    assert any(v > 0 for k, v in _locked()["extraction"]["scores"].items() if k.endswith(".found") and "llm" in k)


def test_the_lock_writes_the_declaration_and_refuses_its_misuses(monkeypatch):
    script = _script()
    old = _locked()["extraction"]
    if _today_mode() == "current":
        _part, problems = script._extraction_lock(old, prompt_changed="nothing changed")
        assert any("are the ones the recording answered" in p for p in problems), problems
    else:
        _part, problems = script._extraction_lock(old, prompt_changed="declared again")
        assert any("already declared" in p for p in problems), problems
    _change_the_prompt(monkeypatch)
    part, problems = script._extraction_lock(old, prompt_changed="the date for the extractor")
    assert not problems, problems
    pending = part["pending_recording"]
    assert pending["changes"][-1]["why"] == "the date for the extractor"
    assert _llm_scores(part["scores"]) == _llm_scores(old["scores"])
    # The same prompt is not declared twice, and a new recording must answer today's prompt.
    _part, problems = script._extraction_lock(part, prompt_changed="again")
    assert any("already declared" in p for p in problems), problems
    _part, problems = script._extraction_lock(part, new_recording="asked again")
    assert any("answers today's prompt" in p for p in problems), problems
    # Without the declaration the changed prompt is refused, as before.
    stripped = {k: v for k, v in part.items() if k != "pending_recording"}
    with pytest.raises(benchmark.StaleRecordingError):
        script._extraction_lock(stripped)


def test_a_recording_of_todays_prompt_ends_the_declaration(monkeypatch, tmp_path):
    script = _script()
    _change_the_prompt(monkeypatch)
    part, problems = script._extraction_lock(_locked()["extraction"], prompt_changed="the date for the extractor")
    assert not problems and part["pending_recording"]
    recording = _recording()
    # The changed prompt asked again, in today's message form (the same keys):
    # each turn the standard number of times (benchmark audit R2), each reply
    # reading as the locked one, written a little differently (a new
    # recording never repeats the locked replies themselves), and one more
    # turn's replies: another recording.
    n = benchmark.ASKINGS_STANDARD
    replies = {k: [r + "\n" for r in benchmark._reply_list(v)] for k, v in recording["replies"].items()}
    fresh = dict(recording, prompt_sha256=benchmark._extraction_prompt_sha256(),
                 message_shape=belief_extractor.MESSAGE_SHAPE, recorded_on="2026-12-01", askings=n, complete=True,
                 replies={**replies, "unused": ["{}"] * n})
    path = tmp_path / "extraction_llm.json"
    path.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(script, "LLM_REPLIES", path)
    new, problems = script._extraction_lock(part, new_recording="C2's recording")
    assert not problems, problems
    assert "pending_recording" not in new
    assert new["recordings"][-1]["answers"] == part["pending_recording"]


def test_the_write_scenarios_replay_the_same_way(monkeypatch):
    if _today_mode() == "previous":
        pending = _pending_in_lock()
    else:
        _change_the_prompt(monkeypatch)
        pending = _declared(monkeypatch, _recording())
    report = benchmark.run_write_scenarios(llm_replies=_recording(), pending=pending)
    assert report["llm_replay"] == "previous"
    assert report["failing"] == _locked()["writes"]["failing"]
    with pytest.raises(benchmark.StaleRecordingError):
        benchmark.run_write_scenarios(llm_replies=_recording(), pending=None)


def test_the_page_says_the_model_rows_wait_for_a_recording(tmp_path, monkeypatch):
    page = _page()
    data = json.loads(page.MEMORY.read_text(encoding="utf-8"))
    data["extraction"].pop("pending_recording", None)
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(page, "MEMORY", plain)
    assert "previous prompt" not in page.render()
    data["extraction"]["pending_recording"] = {"since": "2026-11-20", "changes": [{"why": "a"}, {"why": "b"}]}
    marked = tmp_path / "marked.json"
    marked.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(page, "MEMORY", marked)
    text = page.render()
    assert "a turn, previous prompt) |" in text
    assert "2 changes since 2026-11-20" in text and "do not measure the new prompt" in text


def test_the_product_and_the_benchmark_build_one_message():
    """The deep pass's message is ``turn_message``'s, and the benchmark's
    idea of it is the same function (it kept a copy, which could drift)."""
    sent: list[str] = []

    class Client:
        async def chat(self, messages, **_kw):
            sent.append(messages[-1]["content"])
            return '{"beliefs": []}'

    registry = type("R", (), {"get_client": lambda self: Client()})()
    from unittest import mock

    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        asyncio.run(belief_extractor._llm_extract("I live in Lisbon.", ""))
    assert sent == [benchmark._llm_blob("I live in Lisbon.")]
    assert benchmark._recorded_message("I live in Lisbon.", 1) == "User: I live in Lisbon.\nAssistant: "


def test_control_without_the_asked_messages_a_later_form_cannot_replay_this_recording(monkeypatch):
    """The recording names the message each turn was asked (``asked``, part
    C2): once Kazma builds another form, the old message cannot be rebuilt.
    Without the names, a declared form change replays nothing."""
    recording = {k: v for k, v in _recording().items() if k != "asked"}
    monkeypatch.setattr(belief_extractor, "MESSAGE_SHAPE", belief_extractor.MESSAGE_SHAPE + 1)
    real = belief_extractor.turn_message
    monkeypatch.setattr(belief_extractor, "turn_message",
                        lambda text, answer="", **kw: "Said on: 2026-11-20\n" + real(text, answer, **kw))
    pending, problems = _script()._declare_prompt_change(recording, _pending_in_lock(), "a later form")
    assert not problems
    with pytest.raises(benchmark.StaleRecordingError, match="no longer builds"):
        benchmark.run_extraction_benchmark(llm_replies=recording, pending=pending)


def test_a_message_form_change_is_declared_like_a_prompt_change(monkeypatch):
    """A new form of the message (``MESSAGE_SHAPE``) with the prompt the same:
    refused undeclared; declared, the replay answers each turn with the reply
    the recording was asked for it, in the recording's own form."""
    recording = _recording()
    pending_before = _pending_in_lock()
    monkeypatch.setattr(belief_extractor, "MESSAGE_SHAPE", belief_extractor.MESSAGE_SHAPE + 1)
    real = belief_extractor.turn_message
    monkeypatch.setattr(belief_extractor, "turn_message",
                        lambda text, answer="", **kw: "Said on: 2026-11-20\n" + real(text, answer, **kw))
    with pytest.raises(benchmark.StaleRecordingError, match="changed since the recording"):
        benchmark.run_extraction_benchmark(llm_replies=recording, pending=pending_before)
    pending, problems = _script()._declare_prompt_change(recording, pending_before, "the date for the extractor")
    assert not problems and pending["message_shape"] == belief_extractor.MESSAGE_SHAPE
    report = benchmark.run_extraction_benchmark(llm_replies=recording, pending=pending)
    assert report["llm_replay"] == "previous"
    assert _llm_scores(benchmark.extraction_scores(report)) == _llm_scores(_locked()["extraction"]["scores"])
