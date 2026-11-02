"""A call refused before it ran is a step of the turn (2026-11, FA-253).

Live: asked to change a protected setting, the agent's ``config_save`` was
denied by the commitment gate. The reply quoted the refusal, but the stream
had no tool frame for the call and the stored turn no tool part: the only
trace was the model's wording and a WARNING line for an expected refusal.

Through the real tool worker node: a commitment deny, and a call refused
before its card (python_exec importing ``os``), each emit a start and an end
activity carrying the refusal; the stream turns an end with an error into a
``failed`` row. The worker without the step is the negative control.
"""

from __future__ import annotations

import logging

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


def _state(call: dict):
    from kazma_core.agent.state import initial_supervisor_state

    s = initial_supervisor_state(thread_id="t-refused")
    s["messages"] = [{"role": "user", "content": "please change it"}]
    s["tool_calls_pending"] = [call]
    return s


@pytest.fixture
def activity(monkeypatch):
    from kazma_core import llm_stream

    seen: list[dict] = []

    def _record(phase, tool_name, **kw):
        seen.append({"phase": phase, "name": tool_name, **kw})

    monkeypatch.setattr(llm_stream, "emit_tool_activity", _record)
    return seen


CALLS = {
    "commitment_deny": {"id": "c-config", "name": "config_save",
                        "arguments": {"key": "safety.hitl.enabled", "value": False}},
    "refused_before_card": {"id": "c-py", "name": "python_exec",
                            "arguments": {"code": "import os\nprint(os.listdir('.'))"}},
}


@pytest.mark.anyio
@pytest.mark.parametrize("case", sorted(CALLS))
async def test_a_refused_call_leaves_a_failed_step(activity, case):
    from kazma_core.agent.graph_builder import tool_worker_node

    call = CALLS[case]
    exe = _Executor()
    await tool_worker_node(_state(call), tool_executor=exe, tracer=_NoopTracer(), hitl_config=None)
    assert exe.calls == [], "the refused call never ran"
    mine = [a for a in activity if a.get("call_id") == call["id"]]
    assert [a["phase"] for a in mine] == ["start", "end"], activity
    assert mine[1].get("error"), "the end carries the refusal"


@pytest.mark.anyio
async def test_negative_control_the_old_worker_showed_nothing(activity, monkeypatch):
    from kazma_core.agent import graph_tool_worker
    from kazma_core.agent.graph_builder import tool_worker_node

    monkeypatch.setattr(graph_tool_worker, "_show_refused_steps", lambda state, refused: None)
    call = CALLS["commitment_deny"]
    await tool_worker_node(_state(call), tool_executor=_Executor(), tracer=_NoopTracer(), hitl_config=None)
    assert not [a for a in activity if a.get("call_id") == call["id"]]


@pytest.mark.anyio
async def test_the_protected_key_deny_is_not_a_warning(caplog):
    from kazma_core.agent.graph_builder import tool_worker_node

    with caplog.at_level(logging.DEBUG, logger="kazma_core.safety.commitment.authorize"):
        await tool_worker_node(_state(CALLS["commitment_deny"]), tool_executor=_Executor(),
                               tracer=_NoopTracer(), hitl_config=None)
    deny = [r for r in caplog.records if "DENY config" in r.getMessage()]
    assert deny and all(r.levelno == logging.INFO for r in deny)


def test_the_stored_activity_keeps_the_failed_state():
    from kazma_ui.sse_chat._helpers import _record_frame_activity

    log: list[dict] = []
    _record_frame_activity(log, "tool_result", {"tool_name": "config_save", "tool_call_id": "c1",
                                                "result": "refused", "state": "failed"})
    _record_frame_activity(log, "tool_result", {"tool_name": "file_read", "tool_call_id": "c2",
                                                "result": "ok"})
    assert [row["state"] for row in log] == ["failed", "done"]
