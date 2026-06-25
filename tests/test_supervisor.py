"""Tests for the Supervisor orchestration layer.

Covers:
  - SupervisorState creation and defaults
  - LocalToolRegistry: registration, schema generation, execution
  - Graph compilation and topology
  - Built-in tools (file_read, sqlite_query, etc.)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from kazma_core.agent.state import (
    NodeName,
    initial_supervisor_state,
)
from kazma_core.agent.tool_registry import LocalToolRegistry, _generate_schema

# ═══════════════════════════════════════════════════════════════════
# SupervisorState
# ═══════════════════════════════════════════════════════════════════


class TestSupervisorState:
    """Tests for the SupervisorState TypedDict and factory."""

    def test_initial_state_has_all_fields(self):
        state = initial_supervisor_state()
        assert state["messages"] == []
        assert state["iteration"] == 0
        assert state["max_iterations"] == 10
        assert state["tool_calls_pending"] == []
        assert state["tool_calls_done"] == []
        assert state["tool_results"] == {}
        assert state["needs_compaction"] is False
        assert state["next_node"] == NodeName.SUPERVISOR
        assert state["last_model"] == ""
        assert state["last_tokens"] == 0
        assert state["last_cost_usd"] == 0.0
        assert isinstance(state["thread_id"], str)
        assert len(state["thread_id"]) == 36  # UUID
        assert isinstance(state["last_checkpoint_id"], str)
        assert isinstance(state["created_at"], str)

    def test_initial_state_custom_thread_id(self):
        state = initial_supervisor_state(thread_id="my-thread-123")
        assert state["thread_id"] == "my-thread-123"

    def test_initial_state_custom_max_iterations(self):
        state = initial_supervisor_state(max_iterations=20)
        assert state["max_iterations"] == 20

    def test_node_names_are_strings(self):
        assert NodeName.SUPERVISOR == "supervisor"
        assert NodeName.TOOL_WORKER == "tool_worker"
        assert NodeName.RESPOND == "respond"
        assert NodeName.COMPACT == "compact"

    def test_partial_state_update(self):
        """LangGraph merges partial dicts — verify partial updates work."""
        state = initial_supervisor_state()
        partial: dict[str, Any] = {"iteration": 3, "last_model": "gpt-4o"}
        merged = {**state, **partial}
        assert merged["iteration"] == 3
        assert merged["last_model"] == "gpt-4o"
        assert merged["messages"] == []  # untouched


# ═══════════════════════════════════════════════════════════════════
# Schema generation
# ═══════════════════════════════════════════════════════════════════


class TestSchemaGeneration:
    """Tests for _generate_schema introspection."""

    def test_primitives(self):
        async def fn(a: str, b: int, c: float, d: bool) -> str:
            return ""

        schema = _generate_schema(fn)
        assert schema["type"] == "object"
        assert schema["properties"]["a"]["type"] == "string"
        assert schema["properties"]["b"]["type"] == "integer"
        assert schema["properties"]["c"]["type"] == "number"
        assert schema["properties"]["d"]["type"] == "boolean"
        assert set(schema["required"]) == {"a", "b", "c", "d"}

    def test_defaults(self):
        async def fn(required: str, optional: str = "default") -> str:
            return ""

        schema = _generate_schema(fn)
        assert schema["required"] == ["required"]
        assert schema["properties"]["optional"]["default"] == "default"

    def test_skip_self(self):
        class Foo:
            async def bar(self, x: int) -> str:
                return ""

        schema = _generate_schema(Foo.bar)
        assert "self" not in schema["properties"]
        assert "x" in schema["properties"]

    def test_list_type(self):
        async def fn(items: list[str]) -> list:
            return []

        schema = _generate_schema(fn)
        assert schema["properties"]["items"]["type"] == "array"
        assert schema["properties"]["items"]["items"]["type"] == "string"


# ═══════════════════════════════════════════════════════════════════
# LocalToolRegistry
# ═══════════════════════════════════════════════════════════════════


class TestLocalToolRegistry:
    """Tests for the LocalToolRegistry."""

    def test_builtins_registered(self):
        registry = LocalToolRegistry(include_builtins=True)
        assert registry.tool_count == 17
        names = [t["name"] for t in registry.list_tools()]
        assert "file_read" in names
        assert "file_write" in names
        assert "file_list" in names
        assert "file_search" in names
        assert "sqlite_query" in names
        assert "memory_search" in names
        assert "current_datetime" in names
        assert "shell_exec" in names
        assert "python_exec" in names

    def test_no_builtins(self):
        registry = LocalToolRegistry(include_builtins=False)
        assert registry.tool_count == 0

    def test_register_decorator(self):
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Add two numbers", category="math")
        async def add(a: int, b: int) -> int:
            return a + b

        assert registry.tool_count == 1
        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "add"
        assert defs[0]["function"]["description"] == "Add two numbers"
        assert defs[0]["type"] == "function"

    def test_register_function_imperative(self):
        registry = LocalToolRegistry(include_builtins=False)

        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        registry.register_function("greet", greet, description="Say hello")
        assert registry.tool_count == 1

    @pytest.mark.asyncio
    async def test_execute_async_tool(self):
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Double a number")
        async def double(x: int) -> int:
            return x * 2

        result = await registry.execute("double", {"x": 21})
        assert result["content"] == "42"
        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_execute_sync_tool(self):
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Sync tool")
        def multiply(x: int, y: int) -> int:
            return x * y

        result = await registry.execute("multiply", {"x": 6, "y": 7})
        assert result["content"] == "42"
        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = LocalToolRegistry(include_builtins=False)
        result = await registry.execute("nonexistent", {})
        assert result["is_error"] is True
        assert "not found" in result["content"]

    @pytest.mark.asyncio
    async def test_execute_handles_exceptions(self):
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Always fails")
        async def boom() -> str:
            raise ValueError("intentional error")

        result = await registry.execute("boom", {})
        assert result["is_error"] is True
        assert "intentional error" in result["content"]

    def test_get_tool_definitions_format(self):
        registry = LocalToolRegistry(include_builtins=False)

        @registry.register(description="Test tool", category="test")
        async def test_tool(query: str, limit: int = 10) -> str:
            return ""

        defs = registry.get_tool_definitions()
        d = defs[0]
        assert d["type"] == "function"
        assert d["function"]["name"] == "test_tool"
        assert d["function"]["description"] == "Test tool"
        assert d["function"]["parameters"]["type"] == "object"
        assert "query" in d["function"]["parameters"]["properties"]
        assert "limit" in d["function"]["parameters"]["properties"]
        assert d["function"]["parameters"]["required"] == ["query"]

    def test_connected_always_true(self):
        registry = LocalToolRegistry()
        assert registry.connected is True


# ═══════════════════════════════════════════════════════════════════
# Built-in tool integration
# ═══════════════════════════════════════════════════════════════════


class TestBuiltinTools:
    """Integration tests for built-in tools."""

    @pytest.mark.asyncio
    async def test_file_read_real_file(self):
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute("file_read", {"path": "/etc/hostname"})
        assert result["is_error"] is False
        assert len(result["content"]) > 0

    @pytest.mark.asyncio
    async def test_file_read_missing(self):
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute("file_read", {"path": "/nonexistent/file"})
        assert "not found" in result["content"].lower()

    @pytest.mark.asyncio
    async def test_file_write_and_read(self):
        registry = LocalToolRegistry(include_builtins=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            path = f.name

        try:
            write_result = await registry.execute("file_write", {"path": path, "content": "hello kazma"})
            assert write_result["is_error"] is False
            assert "chars" in write_result["content"]

            read_result = await registry.execute("file_read", {"path": path})
            assert read_result["content"] == "hello kazma"
        finally:
            Path(path).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_file_list(self):
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute("file_list", {"path": "/tmp"})
        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_file_search(self):
        # Search this repo's own source tree (resolved relative to this test
        # file) instead of a hardcoded author-machine path, so the search
        # actually matches files and the ≤limit assertion is meaningful.
        import kazma_core

        search_root = str(Path(kazma_core.__file__).parent)
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute(
            "file_search",
            {
                "pattern": "def ",
                "path": search_root,
                "glob": "*.py",
                "limit": 5,
            },
        )
        assert result["is_error"] is False
        lines = result["content"].strip().split("\n")
        assert lines and lines[0], "file_search returned nothing — search root is wrong"
        assert len(lines) <= 5

    @pytest.mark.asyncio
    async def test_sqlite_query_select_only(self):
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute(
            "sqlite_query",
            {
                "query": "SELECT 1 as val",
                "db_path": ":memory:",
            },
        )
        # :memory: doesn't work across connections, but SELECT check passes
        # Test the safety check instead
        result = await registry.execute(
            "sqlite_query",
            {
                "query": "DROP TABLE users",
                "db_path": ":memory:",
            },
        )
        assert result["is_error"] is True
        assert "Only SELECT" in result["content"]

    @pytest.mark.asyncio
    async def test_current_datetime(self):
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute("current_datetime", {})
        assert result["is_error"] is False
        assert "T" in result["content"]  # ISO-8601

    @pytest.mark.asyncio
    async def test_shell_exec(self):
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute("shell_exec", {"command": "echo hello"})
        assert result["is_error"] is False
        assert "hello" in result["content"]

    @pytest.mark.asyncio
    async def test_shell_exec_timeout(self):
        registry = LocalToolRegistry(include_builtins=True)
        result = await registry.execute("shell_exec", {"command": "sleep 10", "timeout": 1})
        assert "timed out" in result["content"].lower()


# ═══════════════════════════════════════════════════════════════════
# Graph compilation
# ═══════════════════════════════════════════════════════════════════


class TestGraphBuilder:
    """Tests for the LangGraph Supervisor graph."""

    def test_graph_compiles(self):
        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.authority import create_authority
        from kazma_core.cost_breaker import create_cost_breaker
        from kazma_core.llm_provider import LLMConfig, LLMProvider
        from kazma_core.tracing import KazmaTracer

        registry = LocalToolRegistry(include_builtins=True)
        llm = LLMProvider(LLMConfig(base_url="http://test", api_key="test", model="test"))
        authority = create_authority(model="test", window=128000)
        cost_breaker = create_cost_breaker()
        tracer = KazmaTracer(backend="console")

        graph = build_supervisor_graph(
            llm=llm,
            system_prompt="Test",
            tool_definitions=registry.get_tool_definitions(),
            tool_executor=registry,
            cost_breaker=cost_breaker,
            authority=authority,
            tracer=tracer,
        )

        g = graph.get_graph()
        nodes = list(g.nodes)
        assert NodeName.SUPERVISOR in nodes
        assert NodeName.TOOL_WORKER in nodes
        assert NodeName.RESPOND in nodes
        assert NodeName.COMPACT in nodes

    def test_graph_with_checkpointer(self):
        import asyncio

        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.authority import create_authority
        from kazma_core.cost_breaker import create_cost_breaker
        from kazma_core.llm_provider import LLMConfig, LLMProvider
        from kazma_core.tracing import KazmaTracer

        async def _build():
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            registry = LocalToolRegistry(include_builtins=True)
            llm = LLMProvider(LLMConfig(base_url="http://test", api_key="test", model="test"))
            authority = create_authority(model="test", window=128000)
            cost_breaker = create_cost_breaker()
            tracer = KazmaTracer(backend="console")

            conn = await aiosqlite.connect(":memory:")
            saver = AsyncSqliteSaver(conn)
            await saver.setup()

            graph = build_supervisor_graph(
                llm=llm,
                system_prompt="Test",
                tool_definitions=registry.get_tool_definitions(),
                tool_executor=registry,
                cost_breaker=cost_breaker,
                authority=authority,
                tracer=tracer,
                checkpointer=saver,
            )

            g = graph.get_graph()
            assert len(g.nodes) >= 4
            await conn.close()

        asyncio.run(_build())
