"""Tests for the MCP bridge and UnifiedToolExecutor.

Covers:
  - AsyncMCPManager: server lifecycle, schema generation, tool routing
  - UnifiedToolExecutor: local+MCP merge, routing priority, error handling
  - Integration with the LangGraph tool_worker_node
"""

from __future__ import annotations

import asyncio
import json

import pytest
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.mcp.manager import (
    AsyncMCPManager,
    MCPBridgeError,
    MCPServerHandle,
    UnifiedToolExecutor,
    _jsonrpc_parse,
    _jsonrpc_request,
)

# ═══════════════════════════════════════════════════════════════════
# JSON-RPC helpers
# ═══════════════════════════════════════════════════════════════════


class TestJsonRpcHelpers:
    """Tests for the JSON-RPC helper functions."""

    def test_request_format(self):
        req = _jsonrpc_request("tools/list", {})
        assert req["jsonrpc"] == "2.0"
        assert "id" in req
        assert req["method"] == "tools/list"
        assert req["params"] == {}

    def test_request_no_params(self):
        req = _jsonrpc_request("initialize")
        assert "params" not in req

    def test_parse_success(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        result = _jsonrpc_parse(raw)
        assert result == {"tools": []}

    def test_parse_error(self):
        raw = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32600, "message": "Invalid request"},
            }
        )
        with pytest.raises(MCPBridgeError, match="Invalid request"):
            _jsonrpc_parse(raw)


# ═══════════════════════════════════════════════════════════════════
# AsyncMCPManager
# ═══════════════════════════════════════════════════════════════════


class TestAsyncMCPManager:
    """Tests for the AsyncMCPManager."""

    def test_initial_state(self):
        manager = AsyncMCPManager()
        assert manager.list_servers() == []
        assert manager.get_all_tool_schemas() == []
        assert manager.get_tool_server_map() == {}

    def test_is_mcp_tool_empty(self):
        manager = AsyncMCPManager()
        assert manager.is_mcp_tool("anything") is False

    def test_get_server_for_tool_empty(self):
        manager = AsyncMCPManager()
        assert manager.get_server_for_tool("anything") is None

    @pytest.mark.asyncio
    async def test_shutdown_empty(self):
        manager = AsyncMCPManager()
        await manager.shutdown()  # should not raise

    @pytest.mark.asyncio
    async def test_execute_on_disconnected_server(self):
        manager = AsyncMCPManager()
        result = await manager.execute_mcp_tool("nonexistent", "tool", {})
        assert result["is_error"] is True
        assert "not connected" in result["content"]

    def test_schema_generation_from_handle(self):
        """Test that schemas are correctly generated from MCP tool descriptors."""
        manager = AsyncMCPManager()

        # Manually inject a connected server handle
        handle = MCPServerHandle(
            name="test_server",
            transport="stdio",
            connected=True,
            tools=[
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path"},
                        },
                        "required": ["path"],
                    },
                },
                {
                    "name": "list_dir",
                    "description": "List directory contents",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "dir": {"type": "string"},
                        },
                        "required": ["dir"],
                    },
                },
            ],
        )
        manager._servers["test_server"] = handle

        # Test get_all_tool_schemas
        schemas = manager.get_all_tool_schemas()
        assert len(schemas) == 2

        s0 = schemas[0]
        assert s0["type"] == "function"
        assert s0["function"]["name"] == "read_file"
        assert s0["function"]["description"] == "Read a file"
        assert s0["function"]["parameters"]["properties"]["path"]["type"] == "string"
        assert s0["_mcp_server"] == "test_server"

        # Test get_clean_schemas (strips _mcp_server)
        clean = manager.get_clean_schemas()
        assert len(clean) == 2
        assert "_mcp_server" not in clean[0]

        # Test get_tool_server_map
        mapping = manager.get_tool_server_map()
        assert mapping == {"read_file": "test_server", "list_dir": "test_server"}

        # Test is_mcp_tool
        assert manager.is_mcp_tool("read_file") is True
        assert manager.is_mcp_tool("unknown") is False

        # Test get_server_for_tool
        assert manager.get_server_for_tool("read_file") == "test_server"
        assert manager.get_server_for_tool("unknown") is None

    def test_disconnected_server_excluded_from_schemas(self):
        manager = AsyncMCPManager()
        handle = MCPServerHandle(
            name="dead",
            transport="stdio",
            connected=False,
            tools=[{"name": "ghost", "description": "...", "inputSchema": {}}],
        )
        manager._servers["dead"] = handle

        assert manager.get_all_tool_schemas() == []
        assert manager.is_mcp_tool("ghost") is False

    @pytest.mark.asyncio
    async def test_execute_mcp_tool_success(self):
        """Test successful MCP tool execution on a mocked handle."""
        manager = AsyncMCPManager()

        handle = MCPServerHandle(
            name="mock_server",
            transport="stdio",
            connected=True,
            tools=[{"name": "greet", "description": "Say hello", "inputSchema": {}}],
        )
        manager._servers["mock_server"] = handle

        # Mock _send to return a successful MCP result
        async def mock_send(h, method, params):
            return {
                "content": [{"type": "text", "text": "Hello, World!"}],
                "isError": False,
            }

        manager._send = mock_send  # type: ignore

        result = await manager.execute_mcp_tool("mock_server", "greet", {"name": "Kazma"})
        assert result["is_error"] is False
        assert "Hello, World!" in result["content"]

    @pytest.mark.asyncio
    async def test_execute_mcp_tool_error(self):
        """Test MCP tool execution that returns isError=True."""
        manager = AsyncMCPManager()
        handle = MCPServerHandle(
            name="err_server",
            transport="stdio",
            connected=True,
            tools=[{"name": "fail", "description": "...", "inputSchema": {}}],
        )
        manager._servers["err_server"] = handle

        async def mock_send(h, method, params):
            return {
                "content": [{"type": "text", "text": "Permission denied"}],
                "isError": True,
            }

        manager._send = mock_send  # type: ignore

        result = await manager.execute_mcp_tool("err_server", "fail", {})
        assert result["is_error"] is True
        assert "Permission denied" in result["content"]

    @pytest.mark.asyncio
    async def test_execute_mcp_tool_bridge_error(self):
        """Test MCP tool execution when the server raises MCPBridgeError."""
        manager = AsyncMCPManager()
        handle = MCPServerHandle(
            name="crash_server",
            transport="stdio",
            connected=True,
            tools=[{"name": "crash", "description": "...", "inputSchema": {}}],
        )
        manager._servers["crash_server"] = handle

        async def mock_send(h, method, params):
            raise MCPBridgeError("Server process died")

        manager._send = mock_send  # type: ignore

        result = await manager.execute_mcp_tool("crash_server", "crash", {})
        assert result["is_error"] is True
        assert "Server process died" in result["content"]

    @pytest.mark.asyncio
    async def test_execute_mcp_tool_unexpected_error(self):
        """Test MCP tool execution when an unexpected exception occurs."""
        manager = AsyncMCPManager()
        handle = MCPServerHandle(
            name="weird_server",
            transport="stdio",
            connected=True,
            tools=[{"name": "weird", "description": "...", "inputSchema": {}}],
        )
        manager._servers["weird_server"] = handle

        async def mock_send(h, method, params):
            raise RuntimeError("Something completely unexpected")

        manager._send = mock_send  # type: ignore

        result = await manager.execute_mcp_tool("weird_server", "weird", {})
        assert result["is_error"] is True
        assert "Unexpected error" in result["content"]


# ═══════════════════════════════════════════════════════════════════
# UnifiedToolExecutor
# ═══════════════════════════════════════════════════════════════════


class TestUnifiedToolExecutor:
    """Tests for the UnifiedToolExecutor."""

    def test_local_only_schemas(self):
        local = LocalToolRegistry(include_builtins=True)
        executor = UnifiedToolExecutor(local=local)

        defs = executor.get_tool_definitions()
        assert len(defs) == 17  # built-in tools

    def test_merged_schemas(self):
        local = LocalToolRegistry(include_builtins=False)
        mcp = AsyncMCPManager()

        # Add a local tool
        @local.register(description="Local tool")
        async def my_local(x: int) -> str:
            return str(x)

        # Add an MCP tool
        handle = MCPServerHandle(
            name="test",
            transport="stdio",
            connected=True,
            tools=[
                {
                    "name": "mcp_tool",
                    "description": "MCP tool",
                    "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                }
            ],
        )
        mcp._servers["test"] = handle

        executor = UnifiedToolExecutor(local=local, mcp=mcp)
        defs = executor.get_tool_definitions()

        assert len(defs) == 2
        names = [d["function"]["name"] for d in defs]
        assert "my_local" in names
        assert "mcp_tool" in names

        # Verify _mcp_server is stripped in clean schemas
        for d in defs:
            assert "_mcp_server" not in d

    @pytest.mark.asyncio
    async def test_local_routing_priority(self):
        """Local tools take priority over MCP tools with the same name."""
        local = LocalToolRegistry(include_builtins=False)
        mcp = AsyncMCPManager()

        @local.register(description="Local version")
        async def shared_tool() -> str:
            return "local_result"

        handle = MCPServerHandle(
            name="test",
            transport="stdio",
            connected=True,
            tools=[{"name": "shared_tool", "description": "MCP version", "inputSchema": {}}],
        )
        mcp._servers["test"] = handle

        executor = UnifiedToolExecutor(local=local, mcp=mcp)
        result = await executor.execute("shared_tool", {})
        assert result["content"] == "local_result"
        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_mcp_routing(self):
        """MCP tools are routed correctly when not in local."""
        local = LocalToolRegistry(include_builtins=False)
        mcp = AsyncMCPManager()

        handle = MCPServerHandle(
            name="remote",
            transport="stdio",
            connected=True,
            tools=[{"name": "remote_search", "description": "...", "inputSchema": {}}],
        )
        mcp._servers["remote"] = handle

        async def mock_send(h, method, params):
            return {"content": [{"type": "text", "text": "search results"}], "isError": False}

        mcp._send = mock_send  # type: ignore

        executor = UnifiedToolExecutor(local=local, mcp=mcp)
        result = await executor.execute("remote_search", {"q": "test"})
        assert result["is_error"] is False
        assert "search results" in result["content"]

    @pytest.mark.asyncio
    async def test_not_found(self):
        executor = UnifiedToolExecutor(
            local=LocalToolRegistry(include_builtins=False),
        )
        result = await executor.execute("ghost_tool", {})
        assert result["is_error"] is True
        assert "not found" in result["content"].lower()

    @pytest.mark.asyncio
    async def test_empty_executor(self):
        executor = UnifiedToolExecutor()
        result = await executor.execute("anything", {})
        assert result["is_error"] is True

    def test_connected_property(self):
        executor = UnifiedToolExecutor()
        assert executor.connected is False

        local = LocalToolRegistry(include_builtins=True)
        executor = UnifiedToolExecutor(local=local)
        assert executor.connected is True

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        mcp = AsyncMCPManager()
        executor = UnifiedToolExecutor(mcp=mcp)
        await executor.disconnect_all()  # should not raise


# ═══════════════════════════════════════════════════════════════════
# Integration: tool_worker_node with UnifiedToolExecutor
# ═══════════════════════════════════════════════════════════════════


class TestToolWorkerIntegration:
    """Test the tool_worker_node with a UnifiedToolExecutor."""

    @pytest.mark.asyncio
    async def test_worker_routes_to_local(self):
        """tool_worker_node correctly executes local tools via UnifiedToolExecutor."""
        from kazma_core.agent.graph_builder import tool_worker_node
        from kazma_core.agent.state import NodeName, PendingToolCall, initial_supervisor_state
        from kazma_core.tracing import KazmaTracer

        local = LocalToolRegistry(include_builtins=False)

        @local.register(description="Add numbers")
        async def add(a: int, b: int) -> int:
            return a + b

        executor = UnifiedToolExecutor(local=local)
        tracer = KazmaTracer(backend="console")

        state = initial_supervisor_state()
        state["tool_calls_pending"] = [
            PendingToolCall(id="tc_1", name="add", arguments={"a": 2, "b": 3}),
        ]

        result = await tool_worker_node(state, tool_executor=executor, tracer=tracer)

        assert result["next_node"] == NodeName.SUPERVISOR
        assert result["tool_calls_pending"] == []
        assert len(result["tool_calls_done"]) == 1
        assert result["tool_calls_done"][0]["content"] == "5"
        assert result["tool_calls_done"][0]["is_error"] is False

        # Verify tool message was appended
        tool_msgs = [m for m in result["messages"] if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "tc_1"
        assert tool_msgs[0]["content"] == "5"

    @pytest.mark.asyncio
    async def test_worker_routes_to_mcp(self):
        """tool_worker_node correctly routes unknown tools to MCP."""
        from kazma_core.agent.graph_builder import tool_worker_node
        from kazma_core.agent.state import NodeName, PendingToolCall, initial_supervisor_state
        from kazma_core.tracing import KazmaTracer

        mcp = AsyncMCPManager()
        handle = MCPServerHandle(
            name="remote",
            transport="stdio",
            connected=True,
            tools=[{"name": "web_search", "description": "...", "inputSchema": {}}],
        )
        mcp._servers["remote"] = handle

        async def mock_send(h, method, params):
            return {"content": [{"type": "text", "text": "search results"}], "isError": False}

        mcp._send = mock_send  # type: ignore

        executor = UnifiedToolExecutor(local=LocalToolRegistry(include_builtins=False), mcp=mcp)
        tracer = KazmaTracer(backend="console")

        state = initial_supervisor_state()
        state["tool_calls_pending"] = [
            PendingToolCall(id="tc_1", name="web_search", arguments={"query": "kazma"}),
        ]

        result = await tool_worker_node(state, tool_executor=executor, tracer=tracer)

        assert result["next_node"] == NodeName.SUPERVISOR
        assert len(result["tool_calls_done"]) == 1
        assert "search results" in result["tool_calls_done"][0]["content"]

    @pytest.mark.asyncio
    async def test_worker_parallel_execution(self):
        """Multiple tool calls execute concurrently via asyncio.gather."""
        from kazma_core.agent.graph_builder import tool_worker_node
        from kazma_core.agent.state import PendingToolCall, initial_supervisor_state
        from kazma_core.tracing import KazmaTracer

        local = LocalToolRegistry(include_builtins=False)

        call_order: list[str] = []

        @local.register(description="Slow tool A")
        async def tool_a() -> str:
            await asyncio.sleep(0.05)
            call_order.append("a")
            return "result_a"

        @local.register(description="Slow tool B")
        async def tool_b() -> str:
            await asyncio.sleep(0.05)
            call_order.append("b")
            return "result_b"

        executor = UnifiedToolExecutor(local=local)
        tracer = KazmaTracer(backend="console")

        state = initial_supervisor_state()
        state["tool_calls_pending"] = [
            PendingToolCall(id="tc_a", name="tool_a", arguments={}),
            PendingToolCall(id="tc_b", name="tool_b", arguments={}),
        ]

        start = asyncio.get_event_loop().time()
        result = await tool_worker_node(state, tool_executor=executor, tracer=tracer)
        elapsed = asyncio.get_event_loop().time() - start

        assert len(result["tool_calls_done"]) == 2
        # Both should complete — if parallel, < 0.1s; if serial, >= 0.1s
        assert elapsed < 0.15  # generous margin for CI
        assert set(call_order) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_worker_handles_tool_crash(self):
        """A crashing tool doesn't take down the whole worker."""
        from kazma_core.agent.graph_builder import tool_worker_node
        from kazma_core.agent.state import PendingToolCall, initial_supervisor_state
        from kazma_core.tracing import KazmaTracer

        local = LocalToolRegistry(include_builtins=False)

        @local.register(description="Crasher")
        async def crasher() -> str:
            raise RuntimeError("intentional crash")

        @local.register(description="Good tool")
        async def good() -> str:
            return "ok"

        executor = UnifiedToolExecutor(local=local)
        tracer = KazmaTracer(backend="console")

        state = initial_supervisor_state()
        state["tool_calls_pending"] = [
            PendingToolCall(id="tc_bad", name="crasher", arguments={}),
            PendingToolCall(id="tc_good", name="good", arguments={}),
        ]

        result = await tool_worker_node(state, tool_executor=executor, tracer=tracer)

        assert len(result["tool_calls_done"]) == 2
        results_by_id = {r["tool_call_id"]: r for r in result["tool_calls_done"]}
        assert results_by_id["tc_bad"]["is_error"] is True
        assert "intentional crash" in results_by_id["tc_bad"]["content"]
        assert results_by_id["tc_good"]["is_error"] is False
        assert results_by_id["tc_good"]["content"] == "ok"
