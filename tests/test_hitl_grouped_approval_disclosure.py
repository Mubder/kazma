"""One approval covering several danger tools must be stated, not inferred.

Incident, 2026-09-21. An operator asked for four HITL cards to verify their
gate. They got two, because danger tools pending in the SAME supervisor step
are grouped into one card by design (`graph_tool_worker`: `len(danger_tools)
== 1` picks a single-tool card, anything more becomes "N danger tools: ...").

The human surfaces were honest about it. The Telegram card renders "N actions
in this turn" numbered; the web card renders every tool with its arguments;
the registry recorded `tool='4 tools'` with the names in `args_json`.

The MODEL was not told. It saw four individual tool results, nothing else, and
reported to the operator:

    "each of the four ran as its own discrete operation with its own approval
     prompt — nothing was batched"

which was false, and false about precisely the thing being verified. One click
had authorized file_write, file_apply_patch, shell_exec and file_delete.

Approval scope is a security property. A model that cannot see how many tools
one approval covered must not be left to describe it, so the worker now states
it as a system message alongside the results.
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.agent.graph_builder import tool_worker_node
from kazma_core.agent.state import initial_supervisor_state


class _Tracer:
    def trace_tool_execution(self, *a, **k):
        pass


class _Exec:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        return {"content": "ok", "is_error": False}


def _approve_all(_payload):
    """Stand in for the human clicking Approve on the grouped card."""
    return {"approved": True}


def _system_notes(result: dict[str, Any]) -> list[str]:
    msgs = result.get("messages") or []
    return [
        str(m.get("content") or "")
        for m in msgs
        if isinstance(m, dict) and m.get("role") == "system"
    ]


@pytest.mark.anyio
async def test_a_grouped_approval_is_disclosed_to_the_model(monkeypatch):
    """Four danger tools, one approval → the model is told so explicitly."""
    monkeypatch.setattr(
        "langgraph.types.interrupt", _approve_all, raising=False
    )
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [
        {"id": "t1", "name": "file_write", "arguments": {"path": "a.txt", "content": "x"}},
        {"id": "t2", "name": "file_apply_patch", "arguments": {"path": "a.txt"}},
        {"id": "t3", "name": "shell_exec", "arguments": {"command": "git --version"}},
        {"id": "t4", "name": "file_delete", "arguments": {"path": "a.txt"}},
    ]
    result = await tool_worker_node(
        state,
        tool_executor=_Exec(),
        tracer=_Tracer(),
        hitl_config={"enabled": True, "require_approval_for": []},
    )

    notes = " ".join(_system_notes(result))
    assert "APPROVAL SCOPE" in notes, (
        "the model was given no statement of approval scope — it will infer "
        "one prompt per tool, which is what produced the false report"
    )
    assert "SINGLE human approval" in notes
    assert "4 danger tools" in notes
    for name in ("file_write", "file_apply_patch", "shell_exec", "file_delete"):
        assert name in notes, f"{name} missing from the scope statement"
    assert "Do not claim they were approved individually" in notes


@pytest.mark.anyio
async def test_a_single_tool_approval_adds_no_scope_note(monkeypatch):
    """One tool, one card — saying "grouped" there would be its own lie."""
    monkeypatch.setattr(
        "langgraph.types.interrupt", _approve_all, raising=False
    )
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [
        {"id": "only", "name": "file_write", "arguments": {"path": "a.txt", "content": "x"}},
    ]
    result = await tool_worker_node(
        state,
        tool_executor=_Exec(),
        tracer=_Tracer(),
        hitl_config={"enabled": True, "require_approval_for": []},
    )
    assert "APPROVAL SCOPE" not in " ".join(_system_notes(result))


@pytest.mark.anyio
async def test_a_denied_group_gets_no_scope_note(monkeypatch):
    """Nothing ran, so there is no executed scope to describe."""
    monkeypatch.setattr(
        "langgraph.types.interrupt",
        lambda _p: {"approved": False},
        raising=False,
    )
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [
        {"id": "t1", "name": "file_write", "arguments": {"path": "a.txt", "content": "x"}},
        {"id": "t2", "name": "file_delete", "arguments": {"path": "a.txt"}},
    ]
    result = await tool_worker_node(
        state,
        tool_executor=_Exec(),
        tracer=_Tracer(),
        hitl_config={"enabled": True, "require_approval_for": []},
    )
    assert "APPROVAL SCOPE" not in " ".join(_system_notes(result))
