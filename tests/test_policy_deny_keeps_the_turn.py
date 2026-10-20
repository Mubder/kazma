"""A call the policy refuses is an error the model acts on, not the turn's end.

Live 2026-10-20 (Telegram), retest 1: asked for a PDF report, the agent
looked for an old PDF, then tried a ``python_exec`` that imported ``os``. The
commitment layer refused it before the card (the sandbox refuses ``os``) and
marked the refusal "terminal" -- the outcome meant for an unresolved clarify
card -- so the turn went straight to its final answer ("blocked -- no PDF
was produced") without the model ever calling ``generate_pdf``. A deny is
now an ordinary policy refusal: the model reads it and goes on.
"""

from __future__ import annotations

import pytest

from tests._no_models import finish_turn_memory, keep_models_out


@pytest.fixture(autouse=True)
def _no_models(monkeypatch):
    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


class _Executor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, name, args):
        self.calls.append(name)
        return {"content": f"executed {name}", "is_error": False}


class _NoopTracer:
    def trace_tool_execution(self, **kw):
        pass


def _state():
    from kazma_core.agent.state import initial_supervisor_state

    s = initial_supervisor_state(thread_id="t1")
    s["messages"] = [{"role": "user", "content": "Make me a PDF report on Kuwait's weather."}]
    s["tool_calls_pending"] = [{"id": "c1", "name": "python_exec",
                                "arguments": {"code": "import os\nprint(os.listdir('.'))"}}]
    return s


async def _run():
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import NodeName

    exe = _Executor()
    out = await tool_worker_node(_state(), tool_executor=exe, tracer=_NoopTracer(), hitl_config=None)
    return exe, out, NodeName


@pytest.mark.anyio
async def test_a_refused_python_exec_hands_the_turn_back_to_the_model():
    exe, out, node = await _run()
    assert exe.calls == [], "the sandbox refuses os: nothing runs"
    refusal = next(m for m in out["messages"] if m.get("role") == "tool")
    assert "os" in str(refusal.get("content")), refusal
    assert out["next_node"] == node.SUPERVISOR, "the model gets to choose another route"


@pytest.mark.anyio
async def test_negative_control_a_terminal_deny_ended_the_turn(monkeypatch):
    """The old outcome, through the same node: the turn went to RESPOND."""
    from kazma_core.agent import graph_tool_worker
    from kazma_core.agent.state import ToolResult

    real = ToolResult

    def terminal(*args, **kwargs):
        if kwargs.get("outcome") == "policy" and kwargs.get("name") == "python_exec":
            kwargs["outcome"] = "terminal"
        return real(*args, **kwargs)

    monkeypatch.setattr(graph_tool_worker, "ToolResult", terminal)
    _exe, out, node = await _run()
    assert out["next_node"] == node.RESPOND
