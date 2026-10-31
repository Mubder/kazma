"""Regression: a response truncated at max_tokens must NOT execute severed
tool-call arguments — the model must get a truncation-aware corrective error.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_truncated_tool_call_gets_chunking_guidance() -> None:
    from kazma_core.agent.graph_builder import tool_worker_node

    executor = MagicMock()
    executor.execute = AsyncMock(return_value={"content": "unused", "is_error": False})

    state: dict[str, Any] = {
        "messages": [{"role": "user", "content": "create a big html file"}],
        "tool_calls_pending": [
            {
                "id": "tc1",
                "name": "file_write",
                # Severed JSON as parsed by llm_provider: unparseable → {"raw": ...}
                "arguments": {"raw": '{"path": "deck.html", "content": "<!DOCTYPE html>\\n<…'},
            }
        ],
        "_last_finish_reason": "length",
        "iteration": 1,
        "tool_results": {},
    }

    out = await tool_worker_node(state, tool_executor=executor, tracer=MagicMock())

    executor.execute.assert_not_called()
    results = out["tool_calls_done"]
    assert results and results[0]["is_error"] is True
    content = results[0]["content"]
    assert "TRUNCATED" in content
    assert "max_tokens" in content
    assert "SMALLER" in content.upper()


@pytest.mark.asyncio
async def test_malformed_args_without_truncation_still_schema_error() -> None:
    """Empty args WITHOUT a length finish_reason go through normal validation."""
    from kazma_core.agent.graph_builder import tool_worker_node

    executor = MagicMock()
    executor.execute = AsyncMock(return_value={
        "content": "Error: Tool 'file_write' was called with missing required argument(s)",
        "is_error": True,
    })

    state: dict[str, Any] = {
        "messages": [{"role": "user", "content": "hi"}],
        "tool_calls_pending": [{"id": "tc1", "name": "file_write", "arguments": {}}],
        "_last_finish_reason": "tool_calls",
        "iteration": 1,
        "tool_results": {},
    }

    out = await tool_worker_node(state, tool_executor=executor, tracer=MagicMock())

    executor.execute.assert_called_once()
    assert "missing required argument" in out["tool_calls_done"][0]["content"]


LIVE_SEVERED = '{"path": "research/kuwait/KUWAIT_HISTORY_PAPER.md", "content": "# Kuwait: From Kazima to the Modern State\n\n## A Research Pap'


def _paper_state(finish_reason: str) -> dict[str, Any]:
    from kazma_core.agent.state import initial_supervisor_state

    state = initial_supervisor_state()
    state["tool_calls_pending"] = [{"id": "w1", "name": "file_write", "arguments": {"raw": LIVE_SEVERED}}]
    state["_last_finish_reason"] = finish_reason
    return state


async def _run_with_cards(monkeypatch, state) -> tuple[list[Any], dict[str, Any], MagicMock]:
    cards: list[Any] = []

    def _card(payload):
        cards.append(payload)
        return {"approved": True}

    monkeypatch.setattr("langgraph.types.interrupt", _card, raising=False)
    from kazma_core.agent.graph_builder import tool_worker_node

    executor = MagicMock()
    executor.execute = AsyncMock(return_value={"content": "Error: missing required argument(s): path, content", "is_error": True})
    out = await tool_worker_node(state, tool_executor=executor, tracer=MagicMock(),
                                 hitl_config={"enabled": True, "require_approval_for": ["file_write"]})
    return cards, out, executor


@pytest.mark.asyncio
async def test_a_call_the_output_limit_cut_off_gets_no_approval_card(monkeypatch) -> None:
    """Live 2026-10-31: a research paper written in one file_write ran past
    the output limit, and the owner was asked to approve
    ``file_write(raw="{\\"path\\": ...")`` -- half a JSON object that the worker
    then skipped after the approval. It is refused before any card."""
    cards, out, executor = await _run_with_cards(monkeypatch, _paper_state("length"))
    assert cards == []
    executor.execute.assert_not_called()
    done = out["tool_calls_done"]
    assert done[0]["is_error"] and done[0].get("outcome") == "policy"
    assert "TRUNCATED" in done[0]["content"] and "no approval was asked" in done[0]["content"]


@pytest.mark.asyncio
async def test_negative_control_the_same_call_with_no_cut_off_still_asks(monkeypatch) -> None:
    """Without a cut-off response the call is the registry's to refuse (it
    names the missing arguments), and the card is asked as before."""
    cards, out, executor = await _run_with_cards(monkeypatch, _paper_state("tool_calls"))
    assert len(cards) == 1
