"""MCP HITL tests — verify tool classification and approval gate for MCP tools.

Covers:
    - classify_mcp_tool() name-pattern classification
    - UnifiedToolExecutor HITL gate fires for danger-tier MCP tools
    - _hitl_approved flag bypasses the gate (double-gating prevention)
    - Safe MCP tools never trigger the gate
    - MCPServerConfig auth/trust fields
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.mcp.manager import classify_mcp_tool, UnifiedToolExecutor


# ══════════════════════════════════════════════════════════════════════════
# Tool classification
# ══════════════════════════════════════════════════════════════════════════


class TestClassifyMcpTool:
    """classify_mcp_tool() classifies MCP tools by name pattern."""

    def test_danger_keywords(self):
        assert classify_mcp_tool("write_file") == "danger"
        assert classify_mcp_tool("delete_file") == "danger"
        assert classify_mcp_tool("run_command") == "danger"
        assert classify_mcp_tool("execute_code") == "danger"
        assert classify_mcp_tool("shell_exec") == "danger"
        assert classify_mcp_tool("install_package") == "danger"

    def test_safe_keywords(self):
        assert classify_mcp_tool("read_file") == "safe"
        assert classify_mcp_tool("list_directory") == "safe"
        assert classify_mcp_tool("search_files") == "safe"
        assert classify_mcp_tool("get_status") == "safe"
        assert classify_mcp_tool("query_database") == "safe"

    def test_unknown_defaults_to_unknown(self):
        """Tools with no recognized pattern are 'unknown' (treated as danger)."""
        assert classify_mcp_tool("frobnicate") == "unknown"
        assert classify_mcp_tool("transform") == "unknown"

    def test_danger_overrides_safe(self):
        """A name with both safe and danger keywords is danger."""
        # "read_and_write" has "read" (safe) AND "write" (danger) → danger
        assert classify_mcp_tool("read_and_write") == "danger"


# ══════════════════════════════════════════════════════════════════════════
# UnifiedToolExecutor HITL gate
# ══════════════════════════════════════════════════════════════════════════


class _MockMCPManager:
    """Minimal mock AsyncMCPManager for HITL testing."""

    def __init__(self, tools_map: dict[str, str] | None = None):
        # tools_map: {tool_name: server_name}
        self._tools_map = tools_map or {}
        self.execute_mcp_tool = AsyncMock(
            return_value={"content": "executed", "is_error": False}
        )

    def is_mcp_tool(self, name: str) -> bool:
        return name in self._tools_map

    def get_server_for_tool(self, name: str) -> str | None:
        return self._tools_map.get(name)

    def get_server_trust(self, server_name: str) -> str:
        return "approval_required"


class TestUnifiedExecutorHitlGate:
    """The HITL gate in UnifiedToolExecutor.execute() for MCP tools."""

    @pytest.mark.asyncio
    async def test_danger_mcp_tool_blocked_without_approval(self):
        """A danger-tier MCP tool is blocked when safety denies it."""
        from kazma_core.swarm.safety import SafetyMiddleware, set_safety

        # Safety that always denies
        denying_safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        denying_safety.check = AsyncMock(return_value=False)  # type: ignore
        set_safety(denying_safety)

        try:
            mcp_mgr = _MockMCPManager({"write_file": "filesystem"})
            executor = UnifiedToolExecutor(local=None, mcp=mcp_mgr)  # type: ignore

            result = await executor.execute("write_file", {"path": "/tmp/x", "content": "data"})

            assert result["is_error"] is True
            assert "denied" in result["content"].lower()
            # The MCP tool should NOT have been executed
            mcp_mgr.execute_mcp_tool.assert_not_awaited()
        finally:
            from kazma_core.swarm.safety import get_safety
            set_safety(get_safety())  # restore

    @pytest.mark.asyncio
    async def test_danger_mcp_tool_executed_when_approved(self):
        """A danger-tier MCP tool executes when safety approves."""
        from kazma_core.swarm.safety import SafetyMiddleware, set_safety

        approving_safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        approving_safety.check = AsyncMock(return_value=True)  # type: ignore
        set_safety(approving_safety)

        try:
            mcp_mgr = _MockMCPManager({"write_file": "filesystem"})
            executor = UnifiedToolExecutor(local=None, mcp=mcp_mgr)  # type: ignore

            result = await executor.execute("write_file", {"path": "/tmp/x"})

            assert result["is_error"] is False
            mcp_mgr.execute_mcp_tool.assert_awaited_once()
        finally:
            from kazma_core.swarm.safety import get_safety
            set_safety(get_safety())

    @pytest.mark.asyncio
    async def test_hitl_approved_flag_skips_gate(self):
        """ContextVar _hitl_approved bypasses the HITL gate (double-gating prevention).

        The _hitl_approved key in LLM args is always stripped and never
        honored — only the ContextVar set by graph_builder is trusted.
        """
        from kazma_core.agent.tool_registry import _hitl_approved_ctx
        from kazma_core.swarm.safety import SafetyMiddleware, set_safety

        # Safety that would deny — but should never be called
        denying_safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        denying_safety.check = AsyncMock(return_value=False)  # type: ignore
        denying_safety.is_danger_tool = MagicMock(return_value=True)  # type: ignore
        set_safety(denying_safety)

        try:
            mcp_mgr = _MockMCPManager({"write_file": "filesystem"})
            executor = UnifiedToolExecutor(local=None, mcp=mcp_mgr)  # type: ignore

            # Set the ContextVar (as graph_builder does after interrupt() approval)
            token = _hitl_approved_ctx.set(True)
            try:
                # Also pass _hitl_approved in args to verify it gets stripped
                result = await executor.execute(
                    "write_file", {"path": "/tmp/x", "_hitl_approved": True}
                )
            finally:
                _hitl_approved_ctx.reset(token)

            # Should execute despite safety being set to deny
            assert result["is_error"] is False
            mcp_mgr.execute_mcp_tool.assert_awaited_once()
            # _hitl_approved should be stripped from args
            call_args = mcp_mgr.execute_mcp_tool.call_args
            assert "_hitl_approved" not in (call_args.kwargs.get("arguments") or {})
        finally:
            from kazma_core.swarm.safety import get_safety
            set_safety(get_safety())

    @pytest.mark.asyncio
    async def test_safe_mcp_tool_no_gate(self):
        """Safe MCP tools (read/list/get) bypass the HITL gate entirely."""
        from kazma_core.swarm.safety import SafetyMiddleware, set_safety

        blocking_safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
        blocking_safety.check = AsyncMock(return_value=False)  # type: ignore
        set_safety(blocking_safety)

        try:
            mcp_mgr = _MockMCPManager({"read_file": "filesystem"})
            executor = UnifiedToolExecutor(local=None, mcp=mcp_mgr)  # type: ignore

            result = await executor.execute("read_file", {"path": "/tmp/x"})

            # Safe tool should execute despite safety blocking
            assert result["is_error"] is False
            mcp_mgr.execute_mcp_tool.assert_awaited_once()
        finally:
            from kazma_core.swarm.safety import get_safety
            set_safety(get_safety())

    @pytest.mark.asyncio
    async def test_disabled_safety_allows_all_mcp(self):
        """When safety is disabled, all MCP tools run without gating."""
        from kazma_core.swarm.safety import SafetyMiddleware, set_safety

        disabled_safety = SafetyMiddleware(enabled=False)
        set_safety(disabled_safety)

        try:
            mcp_mgr = _MockMCPManager({"write_file": "filesystem"})
            executor = UnifiedToolExecutor(local=None, mcp=mcp_mgr)  # type: ignore

            result = await executor.execute("write_file", {"path": "/tmp/x"})

            assert result["is_error"] is False
            mcp_mgr.execute_mcp_tool.assert_awaited_once()
        finally:
            from kazma_core.swarm.safety import get_safety
            set_safety(get_safety())


# ══════════════════════════════════════════════════════════════════════════
# MCPServerConfig auth/trust fields
# ══════════════════════════════════════════════════════════════════════════


class TestMCPServerConfigAuth:
    """MCPServerConfig supports auth and trust fields."""

    def test_auth_defaults_empty(self):
        from kazma_core.mcp_client import MCPServerConfig

        cfg = MCPServerConfig(name="test")
        assert cfg.auth == {}
        assert cfg.trust == "approval_required"

    def test_auth_bearer(self):
        from kazma_core.mcp_client import MCPServerConfig

        cfg = MCPServerConfig(
            name="remote",
            transport="sse",
            url="http://example.com/sse",
            auth={"type": "bearer", "token": "secret123"},
        )
        assert cfg.auth["type"] == "bearer"
        assert cfg.auth["token"] == "secret123"

    def test_trust_trusted(self):
        from kazma_core.mcp_client import MCPServerConfig

        cfg = MCPServerConfig(name="local", trust="trusted")
        assert cfg.trust == "trusted"
