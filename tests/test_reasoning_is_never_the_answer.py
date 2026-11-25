"""A model's thinking text is never part of its answer, whole or streamed, for
any provider (``kazma_core.reasoning_text``)."""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import httpx
import pytest
from kazma_core import reasoning_text
from kazma_core.llm_provider import LLMConfig, LLMProvider
from kazma_core.reasoning_text import ThinkFilter, split_reasoning, text_of_parts

ANSWER = "The meeting is on Thursday at ten, in the small room."

CASES = {
    "a think block first": ("<think>\nlet me see\n</think>\n\n" + ANSWER, ANSWER),
    "two of them": ("<think>a</think>\n<think>b</think>" + ANSWER, ANSWER),
    "thinking, upper case": (" <THINKING>a</THINKING> " + ANSWER, ANSWER),
    "never closed": ("<think>still going when the limit came", ""),
    "the Qwen reply": (ANSWER + "\n</think>\n\n" + ANSWER, ANSWER),
    "the Qwen reply, on one line": (ANSWER + "</think>" + ANSWER, ANSWER),
    "a lone tag on its line": ("First I add them.\n</think>\n\n" + ANSWER, ANSWER),
    "a tag after the answer": (ANSWER + "\n</think>\n", ANSWER),
    "Arabic": ("<think>أفكر</think>\nالاجتماع يوم الخميس", "الاجتماع يوم الخميس"),
}

KEPT = {
    "no tag": ANSWER,
    "a word": "I think it is on Thursday.",
    "the tag named in prose": "A model writes </think> when it is done, and then the answer.",
    "the tag in a code span": "It ends with `</think>` then the answer.\n`</think>`\nSo it goes.",
    "the tag in a fence": "```\n<think>\nx\n</think>\ny\n```\nThat is the format.",
    "an opening tag later": "Use <think> to start it.",
    "less than": "a < b and b <c",
}


def _streamed(text: str, cuts: list[int]) -> str:
    """What a stream of *text* cut at *cuts* shows."""
    f, shown, last = ThinkFilter(), [], 0
    for cut in [*cuts, len(text)]:
        shown.append(f.feed(text[last:cut]))
        last = cut
    return "".join(shown) + f.finish()


@pytest.mark.parametrize("name", CASES)
def test_reasoning_is_taken_out(name):
    text, answer = CASES[name]
    got, reasoning = split_reasoning(text)
    assert got.strip() == answer and reasoning or name == "a tag after the answer"
    assert got.strip() == answer


@pytest.mark.parametrize("name", KEPT)
def test_an_answer_that_names_the_tag_is_not_cut(name):
    assert split_reasoning(KEPT[name]) == (KEPT[name], "")


@pytest.mark.parametrize("name", [*CASES, *KEPT])
def test_a_stream_shows_no_tag_and_nothing_twice_however_it_is_cut(name):
    text, answer = CASES.get(name) or (KEPT[name], KEPT[name])
    rng = random.Random(7)
    ways = [list(range(1, len(text))), []] + [
        sorted(rng.sample(range(1, len(text)), k=min(5, len(text) - 1))) for _ in range(40)]
    for cuts in ways:
        shown = _streamed(text, cuts)
        if name in KEPT:
            assert shown == text
            continue
        assert "think" not in shown.lower()
        # What streamed is the answer, or what stood before a stray tag (which
        # the final response then replaces) -- never both.
        assert shown.strip() in (answer, text.split("</think>")[0].strip())


def test_parts_of_a_list_that_are_reasoning_are_not_text():
    parts = [{"type": "thinking", "thinking": [{"type": "text", "text": "hm"}]}, {"type": "reasoning", "text": "hm"},
             {"type": "text", "text": ANSWER}]
    assert text_of_parts(parts) == ANSWER


# ── Through the client ──────────────────────────────────────────────────


def _client(handler) -> LLMProvider:
    client = LLMProvider(LLMConfig(base_url="https://api.example.com/v1", api_key="k", model="m"))
    wired = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.example.com/v1")

    async def _wired():
        return wired

    client._get_client = _wired
    return client


def _sse(pieces: list[dict]) -> str:
    frames = [json.dumps({"choices": [{"index": 0, "delta": d}]}) for d in pieces]
    frames.append(json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}))
    return "".join(f"data: {f}\n\n" for f in frames) + "data: [DONE]\n\n"


def _old_stream(pieces: list[dict]) -> str:
    """What a stream showed until 2026-11-25: every content delta (the control)."""
    return "".join(str(d.get("content") or "") for d in pieces)


#: A recorded Qwen-shaped stream: its reasoning field, then the answer, a
#: stray tag, and the answer again.
QWEN = [{"reasoning_content": "The user asks when."}, *({"content": w + " "} for w in ANSWER.split()),
        {"content": "</think>"}, {"content": "\n\n"}, *({"content": w + " "} for w in ANSWER.split())]


async def test_the_recorded_qwen_stream(monkeypatch):
    monkeypatch.setenv("KAZMA_LLM_STREAM", "1")
    client = _client(lambda req: httpx.Response(200, text=_sse(QWEN), headers={"content-type": "text/event-stream"}))
    shown, final = "", None
    async for delta in client.chat_stream([{"role": "user", "content": "when?"}]):
        shown += delta.content or ""
        final = delta.response or final
    assert final is not None and final.content.strip() == ANSWER
    assert "</think>" not in shown and shown.count("Thursday") == 1
    old = _old_stream(QWEN)
    assert "</think>" in old and old.count("Thursday") == 2


@pytest.mark.parametrize("message", [
    {"content": "<think>hm</think>" + ANSWER}, {"content": ANSWER, "reasoning_content": "hm"},
    {"content": ANSWER, "reasoning": "hm"},
    {"content": [{"type": "thinking", "thinking": [{"type": "text", "text": "hm"}]}, {"type": "text", "text": ANSWER}]},
])
async def test_a_blocking_answer(message):
    client = _client(lambda req: httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", **message}, "finish_reason": "stop"}], "usage": {}}))
    answer = await client.chat([{"role": "user", "content": "when?"}])
    assert answer.content == ANSWER and answer.reasoning_chars > 0 or isinstance(message["content"], list)
    assert answer.content == ANSWER


def test_anthropic_and_bedrock_too():
    from kazma_core.anthropic_llm import AnthropicProvider
    from kazma_core.bedrock_llm import BedrockProvider

    text = "<think>hm</think>\n" + ANSWER
    anthropic = AnthropicProvider(LLMConfig(api_key="k", model="claude-x"))
    got = anthropic._parse_response(
        {"content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": text}], "usage": {}}, "claude-x", 0.0)
    assert got.content == ANSWER
    bedrock = BedrockProvider(LLMConfig(model="m"), region="us-east-1")
    assert bedrock._parse({"output": {"message": {"content": [{"text": text}]}}}, "m", 0.0).content == ANSWER


# ── The class: every client's text goes through the rule ────────────────

#: What a delta may carry: the filter's answer, or a whole parsed response's text.
_ALLOWED = {"shown", "resp.content", "response.content"}


def _raw_deltas(source: str) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "StreamDelta":
            for kw in node.keywords:
                if kw.arg == "content" and ast.unparse(kw.value) not in _ALLOWED:
                    out.append(ast.unparse(node))
    return out


def test_no_client_streams_a_providers_text_as_it_came():
    root = Path(reasoning_text.__file__).parent
    files = [p for p in sorted(root.glob("*.py")) if "StreamDelta(content=" in p.read_text(encoding="utf-8")]
    assert {"llm_provider.py", "anthropic_llm.py", "bedrock_llm.py"} <= {p.name for p in files}
    assert {p.name: _raw_deltas(p.read_text(encoding="utf-8")) for p in files
            if _raw_deltas(p.read_text(encoding="utf-8"))} == {}


def test_the_gate_sees_a_raw_delta():
    assert _raw_deltas("yield StreamDelta(content=piece)") == ["StreamDelta(content=piece)"]
