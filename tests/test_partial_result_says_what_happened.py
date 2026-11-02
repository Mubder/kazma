"""A turn that ends without a finished answer says what happened (FA-211).

The 2026-11 functional audit (C11): a provider that answered 200 with a body
that was not JSON, or with no choices, was reported as "⚠️ Partial result: hit
the tool-round limit ... Send **continue**" -- a broken provider misread as a
step limit, and "continue" re-ran the same failure. The stream reader skipped
what it could not parse, so the body became an empty answer; the respond
node's fallback then listed every tool CALL the model had asked for in the
whole chat (earlier turns too) and named the step limit whatever the cause.
And a tool call cut off by the output limit was asked for again seven times.

The real provider on a scripted transport (both the stream and the blocking
path), a real supervisor graph, the real respond node and tool worker. The
negative controls are the old fallback text and the turn without the
cut-twice rule.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider, LLMResponse, ToolCall
from kazma_core.retry import friendly_llm_error

SSE_NO_CHOICES = 'data: {"choices": []}\n\ndata: [DONE]\n\n'

#: (body, content type, what the reply must name). The audit's fault-malformed
#: and fault-empty turns, and their siblings.
BAD_BODIES = {
    "html": ("<html><body>502 Bad Gateway</body></html>", "text/html", "an HTML page"),
    "not-json": ("upstream said no", "text/plain", "text that is not JSON"),
    "empty": ("", "application/json", "an empty body"),
    "error-json": ('{"error": {"message": "upstream overloaded"}}', "application/json",
                   "an error instead of an answer: upstream overloaded"),
    "no-choices-json": ('{"id": "x", "choices": []}', "application/json", "no choices"),
    "list-json": ("[1, 2]", "application/json", "JSON that is not an answer"),
}


def _provider(monkeypatch, body: str, content_type: str, calls: list[str] | None = None) -> LLMProvider:
    prov = LLMProvider(LLMConfig(base_url="http://test", api_key="k", model="stub"))

    async def handler(request):
        if calls is not None:
            calls.append(json.loads(request.content).get("stream") and "stream" or "chat")
        return httpx.Response(200, headers={"content-type": content_type}, content=body.encode())

    transport = httpx.MockTransport(handler)

    async def get_client():
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def no_gateway():
        return None

    monkeypatch.setattr(prov, "_get_client", get_client)
    monkeypatch.setattr(prov, "_sync_gateway_async", no_gateway)
    return prov


def _stream(prov):
    async def go():
        final = None
        async for delta in prov.chat_stream([{"role": "user", "content": "q"}]):
            if delta.response is not None:
                final = delta.response
        return final

    return asyncio.run(go())


@pytest.mark.parametrize("case", sorted({**BAD_BODIES, "sse-no-choices": None}))
def test_the_stream_names_what_the_provider_sent(monkeypatch, case):
    body, ctype, named = BAD_BODIES.get(case) or (SSE_NO_CHOICES, "text/event-stream", "no choices")
    with pytest.raises(LLMError) as raised:
        _stream(_provider(monkeypatch, body, ctype))
    err = raised.value
    assert err.kind == "unusable_answer" and err.transient, err
    assert named in err.answer
    text = friendly_llm_error(err, model="stub")
    assert text.startswith("⚠️") and named in text and "step limit" not in text


@pytest.mark.parametrize("case", sorted(BAD_BODIES))
def test_the_blocking_call_names_what_the_provider_sent(monkeypatch, case):
    body, ctype, named = BAD_BODIES[case]
    prov = _provider(monkeypatch, body, ctype)
    with pytest.raises(LLMError) as raised:
        asyncio.run(prov.chat([{"role": "user", "content": "q"}]))
    assert raised.value.kind == "unusable_answer" and named in raised.value.answer


def test_a_whole_answer_to_a_stream_request_is_the_answer(monkeypatch):
    """A server that does not stream sends its whole JSON answer: read it,
    never as an empty answer."""
    body = json.dumps({"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}]})
    final = _stream(_provider(monkeypatch, body, "application/json"))
    assert final.content == "hello"


def test_the_reply_names_the_provider_through_the_real_graph(monkeypatch):
    """The audit's fault-malformed turn end to end: a supervisor graph on the
    real provider, every answer an HTML page."""
    from kazma_core import retry
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    monkeypatch.setattr(retry, "load_retry_config",
                        lambda: {"max_attempts": 2, "min_wait": 0, "max_wait": 0})
    from kazma_core.runtime import live_llm

    calls: list[str] = []
    body, ctype, named = BAD_BODIES["html"]
    prov = _provider(monkeypatch, body, ctype, calls)
    # The turn's client is the registry's; here, the scripted provider.
    monkeypatch.setattr(live_llm, "resolve_live_client", lambda fallback, **kw: (prov, None))
    graph = build_supervisor_graph(
        llm=prov, system_prompt="base", tool_definitions=[],
        tool_executor=None, cost_breaker=create_cost_breaker(),
        authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
    )
    out = asyncio.run(graph.ainvoke({"messages": [{"role": "user", "content": "hello"}]},
                                    {"configurable": {"thread_id": "fa211"}}))
    reply = out["messages"][-1]["content"]
    assert out.get("turn_failed") is True
    assert "not with a model answer" in reply and named in reply, reply
    assert "tool-round limit" not in reply and "step limit" not in reply
    assert len(calls) == 2  # asked again once (the retry budget), then said so


# ── The respond node's partial result ──────────────────────────────────

SEVERED = ("Error: Your previous response was TRUNCATED by the output token limit "
           "(max_tokens), which severed the 'file_write' arguments mid-JSON -- the tool "
           "was NOT executed, and no approval was asked.")


def _call(cid: str, name: str, args: str = "{}") -> dict[str, Any]:
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def _chat(*, this_turn_reads: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """An earlier turn that read a file, then this turn: a cut-off write."""
    messages = [
        {"role": "user", "content": "read my notes"},
        {"role": "assistant", "content": "", "tool_calls": [_call("r1", "file_read")]},
        {"role": "tool", "tool_call_id": "r1", "content": "the notes"},
        {"role": "assistant", "content": "Here are your notes."},
        {"role": "user", "content": "write the long report"},
    ]
    results: dict[str, Any] = {
        "r1": {"tool_call_id": "r1", "name": "file_read", "content": "the notes", "is_error": False},
        "w1": {"tool_call_id": "w1", "name": "file_write", "content": SEVERED,
               "is_error": True, "outcome": "policy"},
    }
    if this_turn_reads:
        messages += [
            {"role": "assistant", "content": "", "tool_calls": [_call("r2", "file_read")]},
            {"role": "tool", "tool_call_id": "r2", "content": "section one"},
        ]
        results["r2"] = {"tool_call_id": "r2", "name": "file_read", "content": "section one",
                         "is_error": False}
    messages += [
        {"role": "assistant", "content": "", "tool_calls": [_call("w1", "file_write", '{"raw": "{\\"path"}')]},
        {"role": "tool", "tool_call_id": "w1", "content": SEVERED},
    ]
    return messages, results


class _Synth:
    """The final-summary model call: empty, or failing."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.calls = 0

    async def chat(self, messages=None, tools=None, *args, **kwargs):
        self.calls += 1
        if self.fail:
            raise self.fail
        return LLMResponse(content="", tool_calls=[], finish_reason="stop", model="stub",
                           usage={"total_tokens": 1}, cost_usd=0.0)


def _respond(monkeypatch, state: dict[str, Any], llm: Any) -> str:
    from kazma_core.agent.graph_respond import respond_node
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    out = asyncio.run(respond_node(state, llm=llm))
    return out["messages"][-1]["content"]


def _state(messages, results, **extra) -> dict[str, Any]:
    from kazma_core.agent.state import initial_supervisor_state

    state = initial_supervisor_state()
    state.update({"messages": messages, "tool_results": results, "iteration": 3,
                  "max_iterations": 15, "thread_id": "fa211-respond", **extra})
    return state


def _old_partial_text(messages: list[dict[str, Any]]) -> str:
    """The fallback before FA-211, word for word."""
    tools_used = [
        tc.get("function", {}).get("name") or tc.get("name", "tool")
        for m in messages if isinstance(m, dict) and m.get("tool_calls")
        for tc in (m.get("tool_calls") or []) if isinstance(tc, dict)
    ]
    tool_summary_str = (
        f" used tools ({', '.join(sorted(set(tools_used)))})" if tools_used
        else " hit the tool-round limit"
    )
    return (f"⚠️ Partial result:{tool_summary_str}. I could not finish a full report before "
            "the step limit. Send **continue** or a narrower request to finish.")


def _honest(reply: str) -> bool:
    """Names the cut-off, nothing from an earlier turn, and no step limit."""
    return ("cut off by its output limit" in reply and "No tool ran this turn" in reply
            and "file_write" in reply and "file_read" not in reply and "step limit" not in reply)


def test_a_cut_off_turn_says_nothing_ran(monkeypatch):
    messages, results = _chat()
    synth = _Synth()
    reply = _respond(monkeypatch, _state(messages, results, _last_finish_reason="length"), synth)
    assert synth.calls == 1 and "got an empty answer" in reply, reply
    assert _honest(reply), reply
    assert "Not run (refused or declined): file_write" in reply
    # Negative control: the old text, on the same chat, failed every part.
    assert not _honest(_old_partial_text(messages))


def test_the_step_limit_is_named_only_when_it_was_reached(monkeypatch):
    messages, results = _chat(this_turn_reads=True)
    state = _state(messages, results, iteration=14, _last_finish_reason="tool_calls")
    reply = _respond(monkeypatch, state, _Synth())
    assert "step limit (15 steps)" in reply and "continue" in reply
    assert "This turn ran: file_read." in reply  # this turn's read, not the earlier one twice


def test_a_failed_summary_names_its_failure(monkeypatch):
    messages, results = _chat()
    fail = LLMError("The provider sent an HTML page", transient=True, kind="unusable_answer",
                    answer="an HTML page (it began: '<html>')")
    reply = _respond(monkeypatch, _state(messages, results, _last_finish_reason="length"), _Synth(fail))
    assert "not with a model answer" in reply and "HTML page" in reply
    assert "tool-round limit" not in reply


# ── The same cut-off call twice ends the turn ──────────────────────────


def test_a_call_cut_off_twice_ends_the_turn_through_the_real_graph(monkeypatch):
    """The audit's fault-badtool turn: every answer a ``file_write`` whose
    arguments the output limit cut off. The second refusal ends the turn."""
    from kazma_core.agent import graph_tool_worker
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.authority import create_authority
    from kazma_core.cost_breaker import create_cost_breaker
    from kazma_core.tracing import KazmaTracer
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")

    def run(stop_rule: bool) -> tuple[int, dict[str, Any]]:
        asked = []

        class CutOff:
            async def chat(self, *, messages, tools=None, model=None, **kwargs):
                asked.append(1)
                if not tools:  # the tool-less final summary
                    return LLMResponse(content="", tool_calls=[], finish_reason="stop", model="stub",
                                       usage={"total_tokens": 1}, cost_usd=0.0)
                return LLMResponse(
                    content="", finish_reason="length", model="stub", usage={"total_tokens": 1},
                    cost_usd=0.0,
                    tool_calls=[ToolCall(id=f"w{len(asked)}", name="file_write",
                                         arguments={"raw": '{"path": "report.md", "content": "# Rep'})],
                )

        if not stop_rule:
            monkeypatch.setattr(graph_tool_worker, "_severed_again", lambda state, refused: "")
        registry = LocalToolRegistry(include_builtins=True)
        graph = build_supervisor_graph(
            llm=CutOff(), system_prompt="base", tool_definitions=registry.get_tool_definitions(),
            tool_executor=registry, cost_breaker=create_cost_breaker(),
            authority=create_authority(model="test", window=128000), tracer=KazmaTracer(backend="console"),
        )
        out = asyncio.run(graph.ainvoke(
            {"messages": [{"role": "user", "content": "write the long report"}], "max_iterations": 8},
            {"configurable": {"thread_id": f"fa211-cut-{stop_rule}"}, "recursion_limit": 60}))
        return len(asked), out

    asked, out = run(stop_rule=True)
    reply = out["messages"][-1]["content"]
    assert asked == 2, asked
    assert out.get("turn_failed") is True
    assert "cut off by its output limit twice" in reply and "file_write" in reply
    # Negative control: without the rule the model is asked again and again.
    asked_before, _ = run(stop_rule=False)
    assert asked_before > 3, asked_before
