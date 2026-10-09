"""Bilingual argument-fragment recovery must invoke real gated tools, not text."""

from __future__ import annotations

import copy
import json

import pytest
from kazma_core.agent.answer_quality import TOOL_ARGUMENT_RECHECK, looks_like_tool_arguments
from kazma_core.agent_evaluation import evaluate_case
from kazma_core.llm_provider import LLMResponse, ToolCall

READ_TOOL = {
    "type": "function",
    "function": {
        "name": "file_read",
        "parameters": {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
        },
    },
}


@pytest.fixture(autouse=True)
def isolated_graph(monkeypatch):
    from kazma_core.config_store import get_config_store

    for key in ("KAZMA_SELF_IMPROVEMENT", "KAZMA_COMMITMENT_ENABLED", "KAZMA_LLM_STREAM"):
        monkeypatch.setenv(key, "0")
    for key in ("memory.enabled", "agent.nonstop.ledger.enabled", "agent.nonstop.failover.enabled"):
        get_config_store().set(key, False)


def case(prompt, language="en", tools=None, fixtures=None, required=None):
    return {
        "id": f"argument-{language}", "language": language, "split": "development",
        "group_id": "argument-recovery", "source": "regression", "source_kind": "synthetic",
        "human_labeled": False, "rubric": "Read the source with a real tool and report its status.",
        "prompt": prompt, "tools": tools or [READ_TOOL],
        "fixtures": fixtures or {"file_read": {"arguments": {"path": "reports/release.txt"},
                                               "result": "Review approved. Deployment not authorized."}},
        "required_tools": required if required is not None else ["file_read"],
    }


@pytest.mark.parametrize("text, expected", [
    ("{'path': 'reports/release.txt'}", True),
    ('{"path":"reports/release.txt"}', True),
    ('{"name":"sample","count":3}', False),
    ('{"path":"x","extra":true}', False),
    ('{}', False), ('[]', False), ('{not literal}', False),
    ("```python\n{'path': 'x'}\n```", False),
    ("Example: {'path': 'x'}", False),
    ("{'path': __import__('os').getcwd()}", False),
])
def test_fragment_is_only_a_schema_matching_candidate(text, expected):
    assert looks_like_tool_arguments(text, [READ_TOOL]) is expected
    assert looks_like_tool_arguments(text, []) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("language,prompt,answer", [
    ("en", "Read reports/release.txt using the file tool and report the status.",
     "Review approved. Deployment not authorized."),
    ("ar", "اقرأ reports/release.txt بأداة قراءة الملفات واذكر الحالة.",
     "المراجعة معتمدة. النشر غير مأذون به."),
])
async def test_argument_fragment_recovers_through_actual_tool_worker(language, prompt, answer):
    calls = []

    class Client:
        async def chat(self, messages, **kwargs):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                return LLMResponse(content="{'path': 'reports/release.txt'}")
            if len(calls) == 2:
                assert kwargs["tools"] == [READ_TOOL]
                assert any(m.get("content") == TOOL_ARGUMENT_RECHECK for m in messages)
                assert [m["content"] for m in messages if m["role"] == "user"] == [prompt]
                return LLMResponse(content="", tool_calls=[ToolCall(
                    id="read", name="file_read", arguments={"path": "reports/release.txt"})])
            assert not any(m.get("content") == TOOL_ARGUMENT_RECHECK for m in messages)
            assert any(m["role"] == "tool" for m in messages)
            return LLMResponse(content=answer)

    result = await evaluate_case(case(prompt, language), Client(), model="test", system_prompt="Custom identity.")
    assert all(result["checks"].values()), result
    assert result["answer"] == answer
    assert len(result["fixture_calls"]) == 1
    assert len(calls) == 3
    assert not any(m.get("content") == TOOL_ARGUMENT_RECHECK for m in result["messages"])


@pytest.mark.asyncio
async def test_original_fragment_without_recovery_fails_required_tool_check(monkeypatch):
    """Negative control: the old route accepts the fragment without reading."""
    monkeypatch.setattr("kazma_core.agent.graph_supervisor.looks_like_tool_arguments", lambda *args: False)

    class Client:
        async def chat(self, **kwargs):
            return LLMResponse(content="{'path': 'reports/release.txt'}")

    result = await evaluate_case(case("اقرأ reports/release.txt بأداة قراءة الملفات واذكر الحالة.", "ar"),
                                 Client(), model="test", system_prompt="Custom identity.")
    assert result["answer"] == "{'path': 'reports/release.txt'}"
    assert not result["checks"]["required_tools_used"]
    assert not result["fixture_calls"] and not result["attempted_tools"]


def test_recheck_guard_is_declared_in_checkpoint_state():
    from kazma_core.agent.state import SupervisorState, initial_supervisor_state

    assert "tool_argument_rechecks" in SupervisorState.__annotations__
    assert initial_supervisor_state()["tool_argument_rechecks"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,expected_calls", [
    ('{"name":"sample","count":3}', 1),
    ('{"path":"reports/release.txt"}', 2),
    ("{'path': 'reports/release.txt'}", 2),
    ("```python\n{'path': 'reports/release.txt'}\n```", 1),
    ("Requested plan:\n\n## Plan\n- Inspect the release\n- Review the diff\n\nWait for approval.", 1),
])
async def test_requested_data_examples_and_plans_survive_without_execution(answer, expected_calls):
    calls = []

    class Client:
        async def chat(self, **kwargs):
            calls.append(kwargs)
            return LLMResponse(content=answer)

    result = await evaluate_case(case("Return this exact example as data, without invoking tools: " + answer,
                                      required=[]), Client(), model="test", system_prompt="Custom identity.")
    if answer.startswith('{"'):
        assert json.loads(result["answer"]) == json.loads(answer)
    else:
        assert result["answer"] == answer
    assert not result["attempted_tools"] and not result["fixture_calls"]
    assert len(calls) == expected_calls
    # Repeated argument-shaped data is allowed after the single recheck.
    assert all(result["checks"].values()), result


@pytest.mark.asyncio
async def test_recovery_cannot_bypass_denied_danger_tool():
    tool = copy.deepcopy(READ_TOOL)
    tool["function"]["name"] = "file_delete"
    calls = []

    class Client:
        async def chat(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return LLMResponse(content="{'path': 'reports/release.txt'}")
            if len(calls) == 2:
                return LLMResponse(content="", tool_calls=[ToolCall(
                    id="delete", name="file_delete", arguments={"path": "reports/release.txt"})])
            return LLMResponse(content="Deletion was denied; nothing executed.")

    result = await evaluate_case(case("Delete reports/release.txt.", tools=[tool],
                                      fixtures={"file_delete": {"result": "must not run"}}, required=[]),
                                 Client(), model="test", system_prompt="Custom identity.")
    assert [t["name"] for t in result["attempted_tools"]] == ["file_delete"]
    assert not result["fixture_calls"]
    assert result["checks"]["danger_tools_not_executed"]
