"""The loop breaker, driven through the real supervisor node.

The audit put this in "strengthen -- sound, unproven": correct by
construction, verified offline, zero production firings in eight days of
logs. The existing tests say so in their own docstring -- they pin the
detector "without driving the full graph".

That leaves the part that actually failed in the live incident untested.
`detect_tool_loop` was never the broken piece; it EXISTED and was never
wired, and the user watched tool calls scroll for minutes. A green unit
test on the detector would have been just as green the day of the incident.

So these drive `supervisor_node` itself with a looping history and assert
on what the supervisor DOES: stops issuing the call, keeps the message
chain valid, and tells the model why.
"""

from __future__ import annotations

import json

import pytest
from kazma_core.agent.state import initial_supervisor_state
from kazma_core.llm_provider import LLMResponse, ToolCall

# -- the smallest harness that reaches the breaker ---------------------


class _Counter:
    def should_compact(self, messages):
        return False


class _Authority:
    counter = _Counter()


class _CostBreaker:
    def should_halt(self):
        return False

    def record_cost(self, *a, **k):
        pass

    def record_user_interaction(self, *a, **k):
        pass


class _Tracer:
    def trace_llm_call(self, *a, **k):
        pass


class _LoopingLLM:
    """Returns the same paging call shape every time, like the incident."""

    def __init__(self, offset_start=40001):
        self.calls = 0
        self._offset = offset_start

    async def chat(self, *a, **k):
        self.calls += 1
        self._offset += 40000
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(
                id=f"call_{self.calls}",
                name="python_exec",
                arguments={"code": f"substr(hex(checkpoint), {self._offset}, 40000)"},
            )],
            finish_reason="tool_calls",
            model="fake",
            usage={"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5},
            cost_usd=0.0,
        )


def _paging_history(n: int, name="python_exec"):
    """n prior assistant turns, each one paging call, offsets increasing."""
    msgs = []
    for i in range(n):
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"prev_{i}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        {"code": f"substr(hex(checkpoint), {40001 + i * 40000}, 40000)"}
                    ),
                },
            }],
        })
        msgs.append({"role": "tool", "tool_call_id": f"prev_{i}", "content": "..."})
    return msgs


async def _run(messages, iteration, llm=None):
    from kazma_core.agent.graph_supervisor import supervisor_node

    state = initial_supervisor_state()
    state["messages"] = messages
    state["iteration"] = iteration
    return await supervisor_node(
        state,
        llm=llm or _LoopingLLM(),
        system_prompt="you are a test",
        tool_definitions=[],
        tool_executor=None,
        cost_breaker=_CostBreaker(),
        authority=_Authority(),
        tracer=_Tracer(),
    )


# -- what the supervisor must DO --------------------------------------


@pytest.mark.asyncio
async def test_a_paging_loop_stops_being_issued():
    """The incident in one assertion: the repeated call is not run again."""
    out = await _run(_paging_history(8), iteration=8)
    assert out.get("tool_calls_pending") == [], (
        "the breaker must stop the loop, not merely notice it"
    )


@pytest.mark.asyncio
async def test_every_pending_call_gets_a_tool_response():
    """Message-chain validity, and the reason this cannot be a bare stop.

    An assistant message carrying tool_calls with no matching tool
    response is a malformed conversation: the NEXT provider request 400s.
    Breaking the loop by dropping the calls would trade a visible loop for
    an invisible corrupt thread.
    """
    out = await _run(_paging_history(8), iteration=8)
    msgs = out["messages"]
    issued = [tc["id"] for m in msgs if isinstance(m, dict) and m.get("tool_calls")
              for tc in m["tool_calls"]]
    answered = {m.get("tool_call_id") for m in msgs
                if isinstance(m, dict) and m.get("role") == "tool"}
    assert set(issued) <= answered, "every tool_call id must have a response"


@pytest.mark.asyncio
async def test_the_model_is_told_not_to_retry():
    """A breaker that stops the call without explaining invites the model
    to issue it again on the next turn."""
    out = await _run(_paging_history(8), iteration=8)
    notices = [m["content"] for m in out["messages"]
               if isinstance(m, dict) and m.get("role") == "tool"
               and "LOOP BREAKER" in str(m.get("content", ""))]
    assert notices, "the synthetic response must say a breaker fired"
    assert "Do NOT retry" in notices[0]
    assert "final answer" in notices[0], "and say what to do instead"


# -- and what it must NOT do ------------------------------------------


@pytest.mark.asyncio
async def test_distinct_work_is_never_broken():
    """The false-positive that would matter: a breaker that trips on real
    work makes the agent useless in exactly the long tasks it exists for.
    Different files are different work, not a loop."""
    msgs = []
    for i in range(9):
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{
                "id": f"d_{i}", "type": "function",
                "function": {"name": "file_read",
                             "arguments": json.dumps({"path": f"/src/module_{i}.py"})},
            }],
        })
        msgs.append({"role": "tool", "tool_call_id": f"d_{i}", "content": "..."})

    out = await _run(msgs, iteration=9)
    assert out.get("tool_calls_pending"), (
        "distinct work must keep flowing; a breaker with false positives "
        "is worse than none"
    )


@pytest.mark.asyncio
async def test_early_iterations_are_left_alone():
    """A short burst of similar calls is normal. The breaker only applies
    once a run is long enough for a loop to be the better explanation."""
    from kazma_core.agent.graph_supervisor import _LOOP_BREAK_MIN_ITERATION

    out = await _run(_paging_history(8), iteration=_LOOP_BREAK_MIN_ITERATION - 1)
    assert out.get("tool_calls_pending"), (
        "below the minimum iteration the breaker must not engage"
    )
