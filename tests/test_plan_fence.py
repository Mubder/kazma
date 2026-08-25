"""Plan-fence SoT — glued closer, plan-only hop, persist pick (2026-08-26).

Live incident: DeepSeek streamed ```plan then glued ``Saved.`` onto the
closing ticks. CommonMark never closed the fence, the SSE client dropped,
and the UI showed a checklist with no reply — while memory_store had
already succeeded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.agent.plan_fence import (
    PLAN_EXECUTE_CONTINUE,
    has_plan_fence,
    is_plan_only,
    normalize_plan_fence,
    pick_user_facing_text,
    prose_for_user,
    rewrite_terminal_assistant_message,
    should_execute_plan_only_hop,
    split_plan_and_prose,
)
from kazma_core.agent.state import SupervisorState, initial_supervisor_state

_GLUED = (
    "```plan\n"
    "- Check current stored Grok reset beliefs\n"
    "- Save the new SuperGrok Heavy reset\n"
    "- Confirm update, superseding the old date\n"
    "```Saved. One stale personal-reset value (Aug 24) is still lingering.\n"
    "✅ Updated — your new **personal Grok** reset is now saved."
)

_CANONICAL = (
    "```plan\n"
    "- Check current stored Grok reset beliefs\n"
    "- Save the new SuperGrok Heavy reset\n"
    "- Confirm update, superseding the old date\n"
    "```\n"
    "\n"
    "Saved. One stale personal-reset value (Aug 24) is still lingering.\n"
    "✅ Updated — your new **personal Grok** reset is now saved."
)


def test_glued_closer_splits_prose():
    plan, prose = split_plan_and_prose(_GLUED)
    assert "Check current stored" in plan
    assert "Confirm update" in plan
    assert prose.startswith("Saved.")
    assert "personal Grok" in prose
    assert "```" not in prose


def test_normalize_puts_closer_on_own_line():
    out = normalize_plan_fence(_GLUED)
    assert "```Saved" not in out
    assert "```\n\nSaved." in out
    assert out == _CANONICAL or (
        out.startswith("```plan\n") and "\n```\n\nSaved." in out
    )


def test_plan_only_is_plan_only():
    text = "```plan\n- Inspect\n- Write\n```"
    assert is_plan_only(text) is True
    assert has_plan_fence(text) is True
    assert prose_for_user(text) == ""


def test_proper_fence_plus_prose():
    text = "```plan\n- A\n- B\n```\n\nDone. Memory saved."
    plan, prose = split_plan_and_prose(text)
    assert plan.splitlines() == ["- A", "- B"]
    assert prose == "Done. Memory saved."
    assert is_plan_only(text) is False


def test_no_plan_is_all_prose():
    plan, prose = split_plan_and_prose("Just a normal reply.")
    assert plan == ""
    assert prose == "Just a normal reply."
    assert normalize_plan_fence("Just a normal reply.") == "Just a normal reply."


def test_unclosed_fence_splits_list_from_prose():
    text = "```plan\n- Step one\n- Step two\nSaved without closing ticks."
    plan, prose = split_plan_and_prose(text)
    assert "Step one" in plan
    assert "Step two" in plan
    assert prose.startswith("Saved without")


def test_pick_prefers_more_prose_and_keeps_plan():
    last_hop = "Saved. Memory updated."
    glued = _GLUED
    chosen = pick_user_facing_text(last_hop, glued)
    assert "Saved." in chosen
    assert "```plan" in chosen
    assert "```Saved" not in chosen
    # Longer glued payload wins over the short last hop
    assert "personal Grok" in chosen


def test_pick_empty():
    assert pick_user_facing_text("", None, "   ") == ""


def test_should_execute_plan_only_hop_yes():
    plan = "```plan\n- Store the fact\n```"
    assert should_execute_plan_only_hop(
        content=plan,
        has_tool_calls=False,
        tools_available=True,
        plan_mode_kind="off",
        plan_only_continues=0,
        iteration=0,
        max_iterations=15,
    ) is True


def test_should_execute_plan_only_hop_not_in_plan_mode():
    plan = "```plan\n- Store the fact\n```"
    assert should_execute_plan_only_hop(
        content=plan,
        has_tool_calls=False,
        tools_available=True,
        plan_mode_kind="plan",
        plan_only_continues=0,
        iteration=0,
        max_iterations=15,
    ) is False


def test_should_execute_plan_only_hop_once():
    plan = "```plan\n- Store the fact\n```"
    assert should_execute_plan_only_hop(
        content=plan,
        has_tool_calls=False,
        tools_available=True,
        plan_mode_kind="off",
        plan_only_continues=1,
        iteration=1,
        max_iterations=15,
    ) is False


def test_should_not_execute_when_tools_already_called():
    plan = "```plan\n- Store the fact\n```"
    assert should_execute_plan_only_hop(
        content=plan,
        has_tool_calls=True,
        tools_available=True,
        plan_mode_kind="off",
        plan_only_continues=0,
        iteration=0,
        max_iterations=15,
    ) is False


def test_rewrite_terminal_unglues():
    msgs = [
        {"role": "user", "content": "save this"},
        {"role": "assistant", "content": _GLUED},
    ]
    out = rewrite_terminal_assistant_message(msgs)
    assert "```Saved" not in out[-1]["content"]
    assert "\n```\n\nSaved." in out[-1]["content"]


def test_plan_only_continues_is_declared_state():
    assert "plan_only_continues" in SupervisorState.__annotations__
    st = initial_supervisor_state()
    assert st.get("plan_only_continues") == 0


def test_continue_note_is_explicit():
    assert "KAZMA_PLAN_EXECUTE_CONTINUE" in PLAN_EXECUTE_CONTINUE
    assert "Do not emit another plan fence" in PLAN_EXECUTE_CONTINUE


# ── JS source contracts (SSE client must replace-paint done.content) ──


_CHAT_JS = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui"
    / "kazma_ui"
    / "static"
    / "js"
    / "chat.js"
)


def test_chat_js_always_applies_done_content():
    src = _CHAT_JS.read_text(encoding="utf-8")
    assert "data.content && !tokenAccum" not in src
    assert "source: 'done'" in src
    assert "function splitPlanAndProse(" in src
    assert "function stripPlanFenceForDisplay(" in src
    assert "stripPlanFenceForDisplay(tokenAccum)" in src


def test_chat_js_handles_glued_closer():
    src = _CHAT_JS.read_text(encoding="utf-8")
    # Client split must not require a newline before the closing ticks.
    assert r"/```plan[^\n]*\n?([\s\S]*?)```/i" in src


# ── Supervisor: plan-only hop auto-continues when tools exist ──────────


class _FakeCompactor:
    async def retrieve_memories(self, query, limit=5):
        return []


class _FakeAuthority:
    def __init__(self):
        self.compactor = _FakeCompactor()

    async def check_and_enforce(self, state):
        return state


class _FakeCostBreaker:
    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass


class _FakeTracer:
    def trace_llm_call(self, **kwargs):
        pass


class _Response:
    def __init__(self, content="", tool_calls=None, model="fake-model"):
        self.content = content
        self.tool_calls = tool_calls or []
        self.model = model
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.001


class _ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.chat_calls: list[dict] = []

    async def chat(self, messages=None, tools=None, model=None, **kwargs):
        self.chat_calls.append({"messages": list(messages or []), "tools": tools, "model": model})
        if self._responses:
            return self._responses.pop(0)
        return _Response(content="fallback")


@pytest.mark.asyncio
async def test_supervisor_plan_only_auto_continues_when_tools_exist():
    from kazma_core.agent.graph_builder import supervisor_node
    from kazma_core.agent.state import NodeName

    llm = _ScriptedLLM([
        _Response(content="```plan\n- Store the fact in memory\n```"),
    ])
    tools = [{
        "type": "function",
        "function": {"name": "memory_store", "parameters": {"type": "object", "properties": {}}},
    }]
    out = await supervisor_node(
        {
            "messages": [
                {"role": "system", "content": "You are Kazma."},
                {"role": "user", "content": "Remember that my Grok reset is August 31."},
            ],
            "iteration": 0,
            "max_iterations": 15,
            "plan_only_continues": 0,
        },
        llm=llm,
        system_prompt="You are Kazma.",
        tool_definitions=tools,
        tool_executor=None,
        cost_breaker=_FakeCostBreaker(),
        authority=_FakeAuthority(),
        tracer=_FakeTracer(),
    )
    assert out["next_node"] == NodeName.SUPERVISOR
    assert out["plan_only_continues"] == 1
    assert any(
        m.get("role") == "user" and "KAZMA_PLAN_EXECUTE_CONTINUE" in str(m.get("content") or "")
        for m in out["messages"]
    )


@pytest.mark.asyncio
async def test_respond_unglues_terminal_plan_fence(monkeypatch):
    from kazma_core.agent.graph_respond import respond_node

    monkeypatch.setattr(
        "kazma_core.memory.consolidator.schedule_post_turn_memory",
        lambda *_a, **_k: None,
    )
    state = {
        "messages": [
            {"role": "user", "content": "save this"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "memory_store"}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": "stored"},
            {"role": "assistant", "content": _GLUED},
        ],
        "iteration": 2,
        "max_iterations": 15,
    }
    out = await respond_node(state, llm=None)
    last = out["messages"][-1]
    assert last["role"] == "assistant"
    assert "```Saved" not in last["content"]
    assert "Saved." in last["content"]
    assert "```\n\nSaved." in last["content"] or "personal Grok" in last["content"]
