"""A swarm worker's forced final answer is a ledgered, retried model call
(audit 2026-10-26 AUD-119).

At the iteration limit ``InProcessWorker`` makes one more model call, with no
tools, for the final answer. It called ``provider.chat`` directly: no per-call
ledger row (so its cost was missing from the ledger the task's totals are
read from), no transient retry and no failover -- one dropped connection lost
the answer the whole run was for. The SW-E15 gate (``tests/test_swarm_refiner.py``)
exempts the whole of ``worker.py``, so nothing caught it. Every model call of
a worker now goes through ``resilient_chat``, and the gate below holds
``worker.py`` to that.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.llm_provider import LLMError, LLMResponse, ToolCall

WORKER = Path(__file__).resolve().parents[1] / "kazma-core" / "kazma_core" / "swarm" / "worker.py"
CAP = 5


def _looping() -> LLMResponse:
    return LLMResponse(
        content="partial notes",
        tool_calls=[ToolCall(id="c1", name="loop_tool", arguments={"x": 1})],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )


def _final() -> LLMResponse:
    return LLMResponse(content="THE FINAL REPORT", tool_calls=[], finish_reason="stop",
                       usage={"prompt_tokens": 100, "completion_tokens": 50}, cost_usd=0.25)


async def _run_to_the_limit(provider: Any, monkeypatch) -> tuple[dict, list[dict]]:
    from kazma_core.agent import resilient_chat as rc
    from kazma_core.swarm import worker as worker_mod
    from kazma_core.swarm.task import WorkerCapabilities

    rows: list[dict] = []

    async def _record(**fields):
        rows.append(fields)

    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(rc, "_record", _record)
    monkeypatch.setattr(rc.asyncio, "sleep", _no_wait)
    monkeypatch.setattr(worker_mod, "worker_iteration_budget", lambda *a, **k: CAP)
    monkeypatch.setattr("kazma_core.models.selection.select_provider_for_task", lambda r, p: None)

    registry = MagicMock()
    registry.get_client = MagicMock(return_value=provider)
    tools = MagicMock()
    tools.get_tool_definitions.return_value = [
        {"type": "function", "function": {"name": "loop_tool", "description": "loops", "parameters": {}}}
    ]
    tools.execute = AsyncMock(return_value={"content": "loop result", "is_error": False})
    worker = worker_mod.InProcessWorker(
        name="aud119", role="tester",
        capabilities=WorkerCapabilities(role="tester", tools=["loop_tool"]),
    )
    with patch("kazma_core.model_registry.get_model_registry", return_value=registry), \
         patch("kazma_core.agent.tool_registry.get_tool_registry", return_value=tools):
        result = await worker.dispatch("start looping")
    return result, rows


@pytest.mark.asyncio
async def test_the_final_answer_writes_its_ledger_row(monkeypatch) -> None:
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=[_looping()] * CAP + [_final()])
    result, rows = await _run_to_the_limit(provider, monkeypatch)

    assert result["output"] == "THE FINAL REPORT"
    assert len(rows) == CAP + 1, "one ledger row per model call, the final one included"
    final = rows[-1]
    assert final["thread_id"] == rows[0]["thread_id"] and final["thread_id"].startswith("swarm-aud119-")
    assert final["iteration"] == CAP + 1
    assert final["prompt_tokens"] == 100 and final["cost_usd"] == pytest.approx(0.25)
    assert result["cost"] == pytest.approx(0.25)


@pytest.mark.asyncio
async def test_a_transient_failure_of_the_final_call_is_retried(monkeypatch) -> None:
    provider = MagicMock()
    provider.chat = AsyncMock(side_effect=[_looping()] * CAP + [
        LLMError("connection dropped", transient=True), _final(),
    ])
    result, _rows = await _run_to_the_limit(provider, monkeypatch)
    assert result["output"] == "THE FINAL REPORT"
    assert provider.chat.call_count == CAP + 2


def _bare_model_calls(tree: ast.AST) -> list[int]:
    """Lines calling ``<x>.chat(`` or ``<x>.chat_stream(`` directly."""
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("chat", "chat_stream")
    ]


def test_a_worker_calls_its_model_only_through_resilient_chat() -> None:
    assert _bare_model_calls(ast.parse(WORKER.read_text(encoding="utf-8"))) == []


def test_the_gate_sees_the_old_final_call() -> None:
    """Negative control: the final call as it was."""
    old = ast.parse(
        "async def dispatch(self):\n"
        "    _final_resp = await provider.chat(_final_msgs, tools=None, model=self.model or None)\n"
    )
    assert _bare_model_calls(old) == [2]
