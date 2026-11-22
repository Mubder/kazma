"""The LongMemEval reader that asks through Kazma's own chat turn
(``scripts/longmemeval_turn.py``, the memory plan's W1c), driven by a
scripted stand-in model: no model call, no network.

* The turn is Kazma's: the base prompt first with the question's date, the
  recall block of the question's own history, the memory read tools and
  nothing else, the reasoning level the run names.
* ``memory_read`` opens a memory the block cut, whole, from the turn's own
  private store, which is gone afterwards.
* At most three tool rounds run; a fourth is refused and the turn answers.
* The question's clock reaches recall: "last week" is the question's week
  (the real clock as the negative control).
* A refused model call is the turn's failure, said, never an answer; a call
  retried past is counted and is not.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
BASE = "BASE-PROMPT-MARKER: you are Kazma."


@pytest.fixture(scope="module")
def scripts():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_bench
        import longmemeval_turn
    finally:
        sys.path.pop(0)
    return longmemeval_bench, longmemeval_turn


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    from tests._no_models import keep_models_out

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.delenv("KAZMA_MEMORY_STATE_DB", raising=False)


FILLER = "We also talked about the weather and the long drive to work that morning. " * 40
OTHERS = ["How do I boil an egg?", "Recommend a film about space.", "What is the capital of Peru?"]


def _item(question: str, evidence: str, *, evidence_date: str = "2023/05/10 (Wed) 09:00",
          question_date: str = "2023/06/01 (Thu) 10:00") -> dict:
    sessions, ids, dates = [], [], []
    for n, text in enumerate([evidence, *OTHERS]):
        sessions.append([{"role": "user", "content": text, **({"has_answer": True} if n == 0 else {})},
                         {"role": "assistant", "content": "Noted, thanks for telling me."}])
        ids.append(f"s{n}")
        dates.append(evidence_date if n == 0 else f"2023/04/{10 + n:02d} (Mon) 09:00")
    return {"question_id": "q1", "question_type": "single-session-user", "question": question,
            "answer": "Business Administration", "question_date": question_date,
            "haystack_sessions": sessions, "haystack_session_ids": ids, "haystack_dates": dates,
            "answer_session_ids": ["s0"]}


def test_the_block_is_read_from_the_call_that_holds_it(scripts):
    """The intent classifier's refinement can ask first, with the user's
    message alone: the turn's block is the one the supervisor's call held
    (16 of the first 200 dev turns read no block from the first call)."""
    import asyncio

    _bench, turn = scripts
    CountingClient, _recall_block = turn.CountingClient, turn._recall_block

    class Any_:
        async def chat(self, *args, **kwargs):
            return type("R", (), {"usage": {}, "tool_calls": None, "finish_reason": "stop"})()

    note = {"role": "system", "content": "## Memory recall rules\n- one\n\n## Relevant History\n- a memory"}
    client = CountingClient(Any_())
    client.reset()
    asyncio.run(client.chat([{"role": "user", "content": "Where do I live?"}], tools=None))
    asyncio.run(client.chat(messages=[note, {"role": "user", "content": "Where do I live?"}]))
    assert client.recall_block == note["content"]
    # Control: the first call's messages hold no block.
    assert _recall_block(client.first_messages) == ""


class Scripted:
    """A stand-in model: answers each call from *script* (a function of the
    call's messages and offered tools), records what it was sent."""

    def __init__(self, script):
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def can_turn_off_reasoning(self) -> bool:
        return True

    async def chat(self, *, messages, tools=None, model=None, **kwargs):
        from kazma_core.llm_provider import LLMResponse, ToolCall

        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, **kwargs})
        out = self.script(messages, tools, len(self.calls))
        if isinstance(out, Exception):
            raise out
        if isinstance(out, dict):
            return LLMResponse(content="", finish_reason="tool_calls", model="stand-in",
                               tool_calls=[ToolCall(id=f"c{len(self.calls)}", name=out["name"],
                                                    arguments=out["arguments"])],
                               usage={"prompt_tokens": 100, "completion_tokens": 10,
                                      "prompt_cache_hit_tokens": 60,
                                      "completion_tokens_details": {"reasoning_tokens": 4}})
        return LLMResponse(content=out, finish_reason="stop", model="stand-in",
                           usage={"prompt_tokens": 120, "completion_tokens": 8, "prompt_cache_hit_tokens": 60})


def _turn(scripts, item, model, **kwargs):
    import asyncio

    lme, turn = scripts
    rows = lme.history_rows(item)
    client = turn.CountingClient(model)
    outcome = asyncio.run(turn.run_turn(
        str(item["question"]), now=lme.parse_date(item["question_date"]), tenant=lme.TENANT,
        seed=lambda conn: lme._seed_history(conn, rows), client=client, system_prompt=BASE,
        thread_id="lme-turn-q1", **kwargs))
    return outcome, client


def _text(message: dict) -> str:
    return str(message.get("content") or "")


def test_the_turn_opens_a_cut_memory_whole_with_memory_read(scripts, quiet):
    from kazma_core.config_store import get_config_store

    item = _item("Which degree did I graduate with?",
                 "About my degree: " + FILLER + "I graduated in Business Administration.")
    seen: dict[str, Any] = {}

    def script(messages, tools, n):
        if n == 1:
            seen["first"] = messages
            seen["tools"] = sorted(t["function"]["name"] for t in tools or [])
            block = next(_text(m) for m in messages if _text(m).lstrip().startswith("## Memory recall rules"))
            seen["block"] = block
            return {"name": "memory_read", "arguments": {"id": re.search(r"memory_read\(\"(e_[0-9a-f]{8,})\"\)", block).group(1)}}
        seen["read"] = _text(messages[-1])
        return "Business Administration."

    outcome, client = _turn(scripts, item, Scripted(script), reasoning="low")
    assert outcome.error is None and outcome.text == "Business Administration."
    # Kazma's base prompt first, dated at the question's day, read in UTC.
    first = seen["first"][0]
    assert first["role"] == "system" and BASE in _text(first)
    assert "TODAY: Thursday 2023-06-01 (UTC)" in _text(first)
    # The block shows the memory cut (the answer is at its far end), and
    # memory_read gives it whole from the turn's own store.
    assert "graduated in Business Administration" not in seen["block"]
    assert "graduated in Business Administration" in seen["read"]
    assert outcome.block == seen["block"]
    # The memory read tools and nothing else; the run's reasoning level.
    assert seen["tools"] == sorted(scripts[1].TOOLS)
    assert client.inner.calls[0]["reasoning"] == "low"
    assert outcome.usage["calls"] == 2 and outcome.usage["tool_requests"] == 1
    assert outcome.usage["tools"] == {"memory_read": 1}
    assert outcome.usage["prompt_tokens"] == 220 and outcome.usage["prompt_cache_hit_tokens"] == 120
    assert outcome.usage["reasoning_tokens"] == 4 and outcome.usage["failed_calls"] == 0
    # Nothing is left behind: the store, its address, the setting.
    assert "KAZMA_MEMORY_STATE_DB" not in os.environ
    assert get_config_store().get("agent.chat_reasoning", None) is None


def test_at_most_three_tool_rounds_run(scripts, quiet):
    item = _item("Which degree did I graduate with?", "I graduated with a degree in Business Administration.")

    def ask_forever(messages, tools, n):
        return {"name": "memory_search", "arguments": {"query": f"degree {n}"}} if tools else "I could not tell."

    outcome, _ = _turn(scripts, item, Scripted(ask_forever))
    assert outcome.text and outcome.usage["tools"] == {"memory_search": 3}
    # The fourth round was asked for and refused by the turn's own budget.
    assert outcome.usage["tool_requests"] == 4
    # Control: with room for five rounds, five run: the cap is what stops it.
    outcome, _ = _turn(scripts, item, Scripted(ask_forever), rounds=5)
    assert outcome.usage["tools"] == {"memory_search": 5}


def test_the_questions_clock_reaches_recall(scripts, quiet, monkeypatch):
    """A question naming "last week" finds the turn of the question's last
    week; on the real clock (years later) it finds nothing: the control."""
    item = _item("What did I tell you about my degree last week?",
                 "I graduated with a degree in Business Administration.",
                 evidence_date="2023/05/26 (Fri) 09:00", question_date="2023/06/01 (Thu) 10:00")
    blocks: list[str] = []

    def script(messages, tools, n):
        blocks.append(next((_text(m) for m in messages
                            if _text(m).lstrip().startswith("## Memory recall rules")), ""))
        return "Business Administration."

    outcome, _ = _turn(scripts, item, Scripted(script))
    assert "Business Administration" in outcome.block == blocks[0]
    # Control: recall on the real clock.
    _lme, turn = scripts
    real = turn._at_the_questions_time

    def real_clock(now, client):
        return real(__import__("time").time(), client)

    monkeypatch.setattr(turn, "_at_the_questions_time", real_clock)
    blocks.clear()
    outcome, _ = _turn(scripts, item, Scripted(script))
    assert "Business Administration" not in outcome.block


def test_a_refused_call_fails_the_turn_and_a_retried_one_does_not(scripts, quiet, monkeypatch):
    from kazma_core.llm_provider import LLMError

    # The supervisor's own retry, without its wait.
    monkeypatch.setattr("kazma_core.retry.load_retry_config",
                        lambda: {"max_attempts": 3, "min_wait": 0, "max_wait": 0})
    item = _item("Which degree did I graduate with?", "I graduated with a degree in Business Administration.")

    def refused(messages, tools, n):
        return LLMError("Insufficient Balance", transient=False)

    outcome, _ = _turn(scripts, item, Scripted(refused))
    assert outcome.text == "" and outcome.error and "LLMError" in outcome.error
    assert outcome.usage["failed_calls"] >= 1

    def once_then_answer(messages, tools, n):
        return LLMError("read timeout", transient=True) if n == 1 else "Business Administration."

    outcome, _ = _turn(scripts, item, Scripted(once_then_answer))
    assert outcome.error is None and outcome.text == "Business Administration."
    assert outcome.usage["failed_calls"] == 1 and outcome.usage["calls"] == 1


def test_a_streamed_call_is_counted_once(scripts, quiet):
    """A real provider streams (``invoke_llm_chat`` takes ``chat_stream``):
    each call is counted once, from its final response."""
    from kazma_core.llm_stream import StreamDelta

    class Streaming(Scripted):
        async def chat_stream(self, **kwargs):
            response = await self.chat(**kwargs)
            yield StreamDelta(content=response.content)
            yield StreamDelta(response=response)

    item = _item("Which degree did I graduate with?", "I graduated with a degree in Business Administration.")
    model = Streaming(lambda messages, tools, n: "Business Administration.")
    outcome, _ = _turn(scripts, item, model)
    assert outcome.text == "Business Administration." and outcome.usage["calls"] == 1 == len(model.calls)


def test_a_tool_not_offered_is_refused(scripts, quiet):
    import asyncio

    _lme, turn = scripts
    tools = turn.MemoryTools()
    assert sorted(d["function"]["name"] for d in tools.definitions()) == sorted(turn.TOOLS)
    out = asyncio.run(tools.execute("memory_forget_mentions", {"text": "degree"}))
    assert out["is_error"] and out["refused"] and not tools.used


# ── The harness: the turn's answers cached, judged, counted ─────────────────


class _Judge:
    def __init__(self) -> None:
        self.asked = 0

    def ask(self, prompt: str, *, max_tokens: int):
        self.asked += 1
        return "yes", {"prompt_tokens": 50, "completion_tokens": 1}


def test_the_harness_keeps_the_turns_answer_and_judges_it_once(scripts, quiet, monkeypatch, tmp_path):
    lme, turn = scripts
    answer = {"text": "Business Administration."}
    model = Scripted(lambda messages, tools, n: answer["text"])
    monkeypatch.setattr(lme, "_turn_reader", lambda *a, **k: (turn.CountingClient(model), BASE))
    items = [{**_item("Which degree did I graduate with?",
                      "I graduated with a degree in Business Administration."), "question_id": qid}
             for qid in ("q1", "q2")]
    histories = {}
    for i in items:
        block, _whole, info = lme.kazma_history(i, lme.history_rows(i))
        histories[str(i["question_id"])] = {"kazma": block, **info}
    cache: dict = {}
    path = tmp_path / "cache.json"

    def run() -> None:
        lme._answer_turns(items, histories, cache, path, model="m", base_url="https://api.example", key="k",
                          reasoning="low")

    run()
    entry = cache["q1"][lme.TURN]
    assert entry["hypothesis"] == "Business Administration." and entry["same_block"] is True
    assert entry["reader_usage"]["calls"] == 1 and len(model.calls) == 2
    # The turn's block is the block the one-shot readers are given: neither
    # carries the channels a memory was found by (the plan's finding 8).
    assert "_(via " not in histories["q1"]["kazma"]
    # Judged by the pool like any answer, and never asked of a reader there.
    judge = _Judge()
    lme.judge_entry(entry, items[0], judge=judge)
    lme.judge_entry(entry, items[0], judge=judge)
    assert entry["verdict"] == "yes" and judge.asked == 1
    row = lme.turn_row(entry)
    assert row == {"kazma_turn_calls": 1, "kazma_turn_tool_requests": 0, "kazma_turn_tools": 0,
                   "kazma_turn_cache_hit_tokens": 60, "kazma_turn_seconds": row["kazma_turn_seconds"],
                   "kazma_turn_same_block": True}
    summary = lme.summarise_turns([{**row, "kazma_turn_prompt_tokens": 120}])
    assert summary["calls_mean"] == 1 and summary["same_block"] == 1.0 and summary["tool_requests"] == {"0": 1}
    # A second run asks nothing it has.
    run()
    assert len(model.calls) == 2 and cache["q1"][lme.TURN]["verdict"] == "yes"
    # Another block for the question (recall changed) is another question.
    histories["q1"]["kazma"] += "\n(changed)"
    run()
    assert len(model.calls) == 3 and "verdict" not in cache["q1"][lme.TURN]


def test_a_turn_that_failed_is_asked_again_never_judged(scripts, quiet, monkeypatch, tmp_path):
    from kazma_core.llm_provider import LLMError

    lme, turn = scripts
    refused = {"on": True}

    def script(messages, tools, n):
        return LLMError("Insufficient Balance", transient=False) if refused["on"] else "Business Administration."

    model = Scripted(script)
    monkeypatch.setattr(lme, "_turn_reader", lambda *a, **k: (turn.CountingClient(model), BASE))
    item = _item("Which degree did I graduate with?", "I graduated with a degree in Business Administration.")
    block, _whole, info = lme.kazma_history(item, lme.history_rows(item))
    histories = {"q1": {"kazma": block, **info}}
    cache: dict = {}

    def run() -> None:
        lme._answer_turns([item], histories, cache, tmp_path / "c.json", model="m", base_url="https://api.example",
                          key="k", reasoning="low")

    run()
    entry = cache["q1"][lme.TURN]
    assert "hypothesis" not in entry and entry["reader_error"]
    judge = _Judge()
    lme.judge_entry(entry, item, judge=judge)
    assert judge.asked == 0 and "verdict" not in entry
    refused["on"] = False
    run()
    assert cache["q1"][lme.TURN]["hypothesis"] == "Business Administration."
    assert "reader_error" not in cache["q1"][lme.TURN]


def test_the_runs_reader_and_base_prompt_are_kazmas(scripts, quiet):
    """No model call: the reader is the client Kazma builds for DeepSeek, its
    reasoning switch known; the base prompt is the agent's own."""
    from kazma_core.llm_provider import LLMProvider

    lme, turn = scripts
    client, prompt = lme._turn_reader("deepseek-flash", "https://api.deepseek.com", "not-a-real-key")
    assert isinstance(client, turn.CountingClient) and isinstance(client.inner, LLMProvider)
    assert client.inner.config.model == "deepseek-flash" and client.can_turn_off_reasoning() is True
    assert "CRITICAL LANGUAGE RULE" in prompt and "Kazma" in prompt
    # The turn's workspace is the run's own empty folder, never the checkout.
    workspace = Path(os.environ["KAZMA_WORKSPACE"])
    assert workspace.is_dir() and not any(workspace.iterdir()) and REPO not in workspace.parents


def test_the_turns_reader_follows_the_provider(scripts):
    _lme, turn = scripts
    assert turn.preset_for("https://api.deepseek.com") == "deepseek"
    assert turn.preset_for("https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1") == "dashscope"
    assert turn.preset_for("https://dashscope-intl.aliyuncs.com/compatible-mode/v1") == "dashscope"
    assert turn.preset_for("http://127.0.0.1:1234/v1") == "custom"
    client = turn.reader_client(model="qwen3.7-plus", key="k",
                                base_url="https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1")
    # Kazma's reasoning switch reaches DeepSeek's API only: Qwen is sent nothing.
    assert client.config.model == "qwen3.7-plus" and client.can_turn_off_reasoning() is False
    assert client.config.base_url == "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1"
