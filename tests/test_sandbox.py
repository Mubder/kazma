"""Tests for ToolSandbox — permission checks and dangerous-pattern blocking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_core.mcp_client import MCPClient
from kazma_core.tool_sandbox import ToolSandbox

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(connected: bool = True) -> MCPClient:
    """Create a mock MCPClient that looks connected."""
    client = MagicMock(spec=MCPClient)
    client.connected = connected
    client.server_name = "test-server"
    client.call_tool = AsyncMock(return_value={"content": "ok"})
    return client


# ---------------------------------------------------------------------------
# ToolSandbox — construction
# ---------------------------------------------------------------------------


class TestToolSandboxConstruction:
    def test_empty_defaults(self) -> None:
        sb = ToolSandbox()
        assert sb.allowed == set()
        assert sb.denied == set()

    def test_lists_become_sets(self) -> None:
        sb = ToolSandbox(allowed_tools=["a", "b"], denied_tools=["c"])
        assert sb.allowed == {"a", "b"}
        assert sb.denied == {"c"}


# ---------------------------------------------------------------------------
# ToolSandbox.is_allowed
# ---------------------------------------------------------------------------


class TestIsAllowed:
    def test_tool_in_allowlist(self) -> None:
        sb = ToolSandbox(allowed_tools=["read_file", "list_dir"])
        assert sb.is_allowed("read_file")
        assert sb.is_allowed("list_dir")
        assert not sb.is_allowed("write_file")

    def test_wildcard_allows_all(self) -> None:
        sb = ToolSandbox(allowed_tools=["*"])
        assert sb.is_allowed("anything")
        assert sb.is_allowed("dangerous_tool")

    def test_denied_overrides_wildcard(self) -> None:
        sb = ToolSandbox(allowed_tools=["*"], denied_tools=["rm"])
        assert sb.is_allowed("read_file")
        assert not sb.is_allowed("rm")

    def test_denied_blocks_even_if_allowed(self) -> None:
        sb = ToolSandbox(allowed_tools=["foo", "bar"], denied_tools=["foo"])
        assert sb.is_allowed("bar")
        assert not sb.is_allowed("foo")


# ---------------------------------------------------------------------------
# ToolSandbox.execute — permission checks
# ---------------------------------------------------------------------------


class TestSandboxExecute:
    @pytest.mark.asyncio
    async def test_execute_allowed_tool(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["read_file"])
        result = await sb.execute(client, "read_file", {"path": "/tmp/test"})
        assert result == {"content": "ok"}
        client.call_tool.assert_awaited_once_with("read_file", {"path": "/tmp/test"})

    @pytest.mark.asyncio
    async def test_execute_denied_tool(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["*"], denied_tools=["shell_exec"])
        with pytest.raises(PermissionError, match="denied"):
            await sb.execute(client, "shell_exec")

    @pytest.mark.asyncio
    async def test_execute_not_in_allowlist(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["read_file"])
        with pytest.raises(PermissionError, match="not in the allowlist"):
            await sb.execute(client, "write_file")

    @pytest.mark.asyncio
    async def test_execute_empty_args(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["foo"])
        await sb.execute(client, "foo")
        client.call_tool.assert_awaited_once_with("foo", {})


# ---------------------------------------------------------------------------
# ToolSandbox — dangerous pattern detection
# ---------------------------------------------------------------------------


class TestDangerousPatterns:
    @pytest.mark.asyncio
    async def test_dangerous_tool_name_rejected(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["*"])
        with pytest.raises(PermissionError, match="dangerous pattern"):
            await sb.execute(client, "foo; rm -rf /")

    @pytest.mark.asyncio
    async def test_dangerous_backtick_in_name(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["*"])
        with pytest.raises(PermissionError, match="dangerous pattern"):
            await sb.execute(client, "cmd `whoami`")

    @pytest.mark.asyncio
    async def test_dangerous_dollar_paren_in_arg(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["*"])
        with pytest.raises(PermissionError, match="dangerous pattern"):
            await sb.execute(
                client, "read_file", {"path": "$(cat /etc/passwd)"}
            )

    @pytest.mark.asyncio
    async def test_dangerous_pipe_rm_in_arg(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["*"])
        with pytest.raises(PermissionError, match="dangerous pattern"):
            await sb.execute(
                client, "search", {"query": "foo | rm -rf /"}
            )

    @pytest.mark.asyncio
    async def test_dangerous_mkfs_in_name(self) -> None:
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["*"])
        with pytest.raises(PermissionError, match="dangerous pattern"):
            await sb.execute(client, "format; mkfs.ext4 /dev/sda")

    @pytest.mark.asyncio
    async def test_safe_tool_with_normal_args(self) -> None:
        """Normal tool calls with safe arguments should pass."""
        client = _make_client()
        sb = ToolSandbox(allowed_tools=["web_search"])
        result = await sb.execute(
            client, "web_search", {"query": "hello world"}
        )
        assert result == {"content": "ok"}


# ---------------------------------------------------------------------------
# ToolSandbox — edge cases
# ---------------------------------------------------------------------------


class TestSandboxEdgeCases:
    def test_empty_allow_empty_deny_blocks_all(self) -> None:
        sb = ToolSandbox()
        assert not sb.is_allowed("anything")

    def test_deny_list_priority_over_allow(self) -> None:
        """A tool in BOTH allowed and denied is blocked."""
        sb = ToolSandbox(allowed_tools=["foo"], denied_tools=["foo"])
        assert not sb.is_allowed("foo")
