"""Regression: malformed/empty tool arguments must produce corrective errors,
never raw TypeErrors — so the model can self-repair instead of retry-looping.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from kazma_core.agent.tool_registry import LocalToolRegistry


@pytest.fixture
def registry() -> LocalToolRegistry:
    return LocalToolRegistry()


@pytest.fixture
def graph_gate():
    """Simulate the supervisor graph being the HITL authority (mechanism A):
    skips the redundant bus gate so danger tools execute in tests."""
    from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx

    token = _graph_hitl_gate_ctx.set(True)
    yield
    _graph_hitl_gate_ctx.reset(token)


@pytest.mark.asyncio
async def test_empty_args_returns_corrective_error(registry, graph_gate) -> None:
    result = await registry.execute("file_write", {})
    assert result["is_error"] is True
    assert "missing required argument" in result["content"]
    assert "path" in result["content"] and "content" in result["content"]
    assert "Expected parameters" in result["content"]


@pytest.mark.asyncio
async def test_raw_unparsed_blob_returns_corrective_error(registry, graph_gate) -> None:
    # DeepSeek truncation path: arguments JSON failed to parse upstream and
    # arrived as {"raw": "<!DOCTYPE..."} — 'raw' is filtered out, leaving
    # no required params.
    result = await registry.execute(
        "file_write",
        {"raw": '{"path": "hello.html"'},  # truncated JSON
    )
    assert result["is_error"] is True
    assert "missing required argument" in result["content"]


@pytest.mark.asyncio
async def test_partial_args_name_whats_missing(registry, graph_gate) -> None:
    result = await registry.execute("file_write", {"path": "hello.html"})
    assert result["is_error"] is True
    assert "content" in result["content"]
    assert "path," not in result["content"]  # path was provided


@pytest.mark.asyncio
async def test_valid_args_still_execute(registry, graph_gate, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    from kazma_core.tools.file_write import configure_workspace

    configure_workspace(str(tmp_path))
    result = await registry.execute(
        "file_write",
        {"path": "hello.html", "content": "<html>hi</html>"},
    )
    assert result["is_error"] is False
    # Workspace resolution prefers a persisted active workspace over the env
    # pin, so don't assert the exact location — only that the write succeeded
    # and named the file.
    assert "hello.html" in result["content"]


@pytest.mark.asyncio
async def test_unknown_tool_still_clean_error(registry) -> None:
    result = await registry.execute("no_such_tool_xyz", {})
    assert result["is_error"] is True
    assert "not found" in result["content"].lower() or "unknown" in result["content"].lower()


def test_malformed_non_dict_args_logged(caplog) -> None:
    """llm_provider must log and wrap non-dict arguments instead of splatting."""
    import json
    import logging
    from kazma_core.llm_provider import LLMProvider

    provider = LLMProvider.__new__(LLMProvider)  # parse path only
    provider.config = MagicMock(input_cost_per_1m=0.0, output_cost_per_1m=0.0)
    data = {
        "choices": [{
            "message": {
                "content": "",
                "tool_calls": [{
                    "id": "1",
                    "function": {"name": "file_write", "arguments": '["not","an","object"]'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {},
    }
    with caplog.at_level(logging.WARNING):
        resp = provider._parse_response(data, duration_ms=0.0)
    assert resp.tool_calls[0].arguments == {"_malformed": ["not", "an", "object"]}
    assert any("not a JSON object" in r.message for r in caplog.records)

    # Unparseable JSON → {"raw": ...} + warning
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"path": "x'
    with caplog.at_level(logging.WARNING):
        resp = provider._parse_response(data, duration_ms=0.0)
    assert resp.tool_calls[0].arguments == {"raw": '{"path": "x'}
