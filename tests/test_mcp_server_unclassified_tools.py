"""`kazma mcp` gates a tool with no tier as the graph does (audit 2026-10-26
AUD-098).

The MCP tool server decided "danger" by ``get_tool_tier(name) in ("danger",
"unsafe")``. A tool nobody gave a tier is ``unknown``, so it was published as
read-only to a client with no approval path and run when called, while the
graph and the swarm bus gate it (``hitl.approval_floor``, §26B default-deny).
The server now asks the same floor.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kazma_core.mcp import server as mcp_server
from kazma_core.mcp.server import ApprovalPath, build_tool_list, call_tool

GATED = ApprovalPath(gated=True, reason="test: approval path present")
UNGATED = ApprovalPath(gated=False, reason="test: nothing can approve")
NEW_TOOL = "aud098_brand_new_mutator"


class _Registry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object"}}}
            for n in ("file_read", NEW_TOOL)
        ]

    async def execute(self, name: str, arguments: dict) -> dict:
        self.calls.append(name)
        return {"content": "ran", "is_error": False}


@pytest.fixture
def registry(monkeypatch) -> _Registry:
    from kazma_core.safety.hitl import TOOL_TIERS

    assert NEW_TOOL not in TOOL_TIERS
    monkeypatch.delenv("KAZMA_MCP_TOOLS", raising=False)
    reg = _Registry()
    monkeypatch.setattr("kazma_core.agent.tool_registry.get_tool_registry", lambda: reg)
    return reg


def test_an_unclassified_tool_is_withheld_with_no_approval_path(registry) -> None:
    names = {t["name"] for t in build_tool_list(UNGATED)}
    assert names == {"file_read"}


def test_an_unclassified_tool_is_published_as_needing_approval(registry) -> None:
    tools = {t["name"]: t for t in build_tool_list(GATED)}
    assert tools[NEW_TOOL]["annotations"] == {"destructiveHint": True, "readOnlyHint": False}
    assert "human approval" in tools[NEW_TOOL]["description"]
    assert tools["file_read"]["annotations"]["readOnlyHint"] is True


def test_calling_an_unclassified_tool_with_no_approval_path_is_refused(registry) -> None:
    result = asyncio.run(call_tool(NEW_TOOL, {}, UNGATED))
    assert result["isError"] is True
    assert registry.calls == []


def test_unreadable_approval_settings_gate_everything(registry, monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr("kazma_core.safety.hitl.get_hitl_config", _boom)
    assert build_tool_list(UNGATED) == []


def test_the_old_tier_check_let_it_through(registry) -> None:
    """Negative control: the classification as it was."""
    from kazma_core.safety.hitl import get_tool_tier

    old_is_danger = get_tool_tier(NEW_TOOL) in ("danger", "unsafe")
    assert old_is_danger is False
    assert mcp_server._is_danger(NEW_TOOL) is True
