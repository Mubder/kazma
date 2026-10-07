"""Grounding reaches real graph calls; scripted answers are not accuracy evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from kazma_core.agent.source_grounding import SOURCE_GROUNDING_PROMPT, with_source_grounding
from kazma_core.agent_evaluation import evaluate_case, validate_dataset
from kazma_core.llm_provider import LLMResponse, ToolCall


@pytest.fixture(autouse=True)
def isolated_graph(monkeypatch):
    from kazma_core.config_store import get_config_store

    for key in ("KAZMA_SELF_IMPROVEMENT", "KAZMA_COMMITMENT_ENABLED", "KAZMA_LLM_STREAM"):
        monkeypatch.setenv(key, "0")
    store = get_config_store()
    for key in ("memory.enabled", "agent.nonstop.ledger.enabled", "agent.nonstop.failover.enabled"):
        store.set(key, False)
    monkeypatch.setattr("kazma_core.memory.consolidator._schedule_post_turn_memory", lambda *a, **k: None)


def assert_grounding_before_user(messages):
    rules = [i for i, m in enumerate(messages)
             if m.get("role") == "system" and m.get("content") == SOURCE_GROUNDING_PROMPT]
    assert len(rules) == 1
    assert rules[0] < next(i for i, m in enumerate(messages) if m.get("role") == "user")
    assert "does not establish that a release has not shipped" in messages[rules[0]]["content"]
    assert "has not been published" in messages[rules[0]]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "ar"])
async def test_custom_prompt_graph_receives_grounding_before_and_after_file_read(language):
    cases = validate_dataset(json.loads((Path(__file__).parent / "fixtures/live_agent_eval_examples.json")
                                      .read_text(encoding="utf-8")))
    case = next(c for c in cases if c["id"] == f"read-{language}")
    calls = []

    class Client:
        async def chat(self, messages, **kwargs):
            calls.append(copy.deepcopy(messages))
            assert_grounding_before_user(messages)
            if len(calls) == 1:
                return LLMResponse(content="", tool_calls=[ToolCall(id="read", name="file_read",
                                                                    arguments={"path": "status.txt"})])
            return LLMResponse(content="Pending review." if language == "en" else "بانتظار المراجعة.")

    result = await evaluate_case(case, Client(), model="pinned", system_prompt="Custom operator identity.")
    assert all(result["checks"].values()), result
    assert len(calls) == 2
    assert result["review"] is None
    tool_result = next(m["content"] for m in calls[-1] if m.get("role") == "tool")
    assert json.loads(tool_result) == case["fixtures"]["file_read"]["result"]
    # Per-call guidance must not accumulate in the checkpoint transcript.
    assert not any(m.get("content") == SOURCE_GROUNDING_PROMPT for m in result["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["Release status: pending review.", "حالة الإصدار: بانتظار المراجعة."])
async def test_forced_synthesis_on_restored_history_receives_grounding(status):
    from kazma_core.agent.graph_respond import respond_node

    calls = []
    state = {"messages": [
        {"role": "system", "content": "Old custom prompt without grounding rules."},
        {"role": "user", "content": "Report the status."},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "read", "type": "function",
          "function": {"name": "file_read", "arguments": '{"path":"status.txt"}'}}]},
        {"role": "tool", "tool_call_id": "read", "content": status},
    ], "iteration": 4, "max_iterations": 5, "thread_id": "grounding-synthesis"}
    original = copy.deepcopy(state)

    class Client:
        async def chat(self, messages, **kwargs):
            assert kwargs["tools"] is None
            assert_grounding_before_user(messages)
            assert next(m["content"] for m in messages if m.get("role") == "tool") == status
            calls.append(messages)
            return LLMResponse(content=status)

    out = await respond_node(state, llm=Client())
    assert len(calls) == 1
    assert out["messages"][-1]["content"] == status
    assert state == original


def test_policy_does_not_rewrite_or_duplicate_checkpointed_tool_history():
    messages = [{"role": "system", "content": "Custom identity."},
                {"role": "user", "content": SOURCE_GROUNDING_PROMPT},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "read"}]},
                {"role": "tool", "tool_call_id": "read", "content": "unchanged"}]
    original = copy.deepcopy(messages)
    once = with_source_grounding(messages)
    assert_grounding_before_user(once)
    assert with_source_grounding(once) == once
    assert messages == original
    assert once[2:] == messages[1:]


@pytest.mark.asyncio
async def test_failed_turn_still_skips_synthesis():
    from kazma_core.agent.graph_respond import respond_node

    class Client:
        async def chat(self, **kwargs):
            pytest.fail("A failed turn must not call synthesis, even with grounding guidance.")

    await respond_node({"messages": [{"role": "user", "content": "Report the status."}],
                        "turn_failed": True, "iteration": 4, "max_iterations": 5}, llm=Client())
