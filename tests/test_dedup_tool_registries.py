"""Tests for the unified tool registry consolidation (dedup-001).

Asserts:
  - VAL-DEDUP-001: Only one ToolRegistry abstraction exists.
  - VAL-DEDUP-008: No stub-lambda MCP tools in app.py; the SSE path
    wires the real UnifiedToolExecutor so MCP tools execute for real.
  - The surviving registry (UnifiedToolExecutor) handles both local
    and MCP tools.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════
# VAL-DEDUP-001: Only one ToolRegistry abstraction remains
# ═══════════════════════════════════════════════════════════════════


def test_old_tool_registry_module_is_deleted() -> None:
    """kazma_core/tool_registry.py must be deleted (the redundant MCP-only registry)."""
    assert not (REPO_ROOT / "kazma-core" / "kazma_core" / "tool_registry.py").exists(), (
        "kazma_core/tool_registry.py still exists — the redundant ToolRegistry must be removed"
    )


def test_toolregistry_class_not_exported_from_top_level() -> None:
    """kazma_core must not re-export the deleted ToolRegistry."""
    import kazma_core

    assert not hasattr(kazma_core, "ToolRegistry"), (
        "kazma_core.ToolRegistry is still exported — the redundant registry must be removed"
    )


def test_only_one_toolregistry_class_definition() -> None:
    """``grep -rn 'class .*ToolRegistry'`` must return at most one definition.

    UnifiedToolExecutor is the canonical abstraction now; LocalToolRegistry
    is a backend (not a top-level ToolRegistry subclass). No class whose
    name matches ``*ToolRegistry`` may remain except LocalToolRegistry.
    """
    matches: list[str] = []
    for py in (REPO_ROOT / "kazma-core").rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("ToolRegistry"):
                matches.append(f"{py}:{node.lineno} class {node.name}")
    # LocalToolRegistry is the legitimate local backend — exactly one match allowed.
    assert len(matches) <= 1, f"Multiple *ToolRegistry classes found: {matches}"
    if matches:
        assert matches[0].endswith("class LocalToolRegistry"), (
            f"Unexpected ToolRegistry class: {matches[0]}"
        )


# ═══════════════════════════════════════════════════════════════════
# VAL-DEDUP-008: No stub-lambda MCP tools in app.py
# ═══════════════════════════════════════════════════════════════════


def _app_py_source() -> str:
    return (REPO_ROOT / "kazma-ui" / "kazma_ui" / "app.py").read_text(encoding="utf-8")


def test_app_py_has_no_mcp_stub_lambda() -> None:
    """app.py must not register MCP tools as ``lambda **kw: ...`` stubs."""
    src = _app_py_source()
    assert "MCP tool (use WebSocket)" not in src, (
        "app.py still contains the MCP stub-lambda string literal"
    )
    # The stub pattern was: register_function(..., func=lambda **kw: {...}, ..., category="mcp")
    assert 'lambda **kw: {"content": "MCP tool' not in src, (
        "app.py still registers MCP tools via stub lambda"
    )


def test_app_py_wires_unified_executor() -> None:
    """The SSE graph in app.py must use the agent's real UnifiedToolExecutor."""
    src = _app_py_source()
    # The SSE path must delegate to agent.tools (the UnifiedToolExecutor)
    # rather than building a parallel LocalToolRegistry + stub lambdas.
    assert "UnifiedToolExecutor" in src or "agent.tools" in src, (
        "app.py SSE path does not reference UnifiedToolExecutor or agent.tools"
    )


# ═══════════════════════════════════════════════════════════════════
# UnifiedToolExecutor handles both local and MCP tools
# ═══════════════════════════════════════════════════════════════════


def test_unified_executor_is_canonical_abstraction() -> None:
    """UnifiedToolExecutor is importable from kazma_core.mcp and is the single executor."""
    from kazma_core.mcp.manager import UnifiedToolExecutor

    assert UnifiedToolExecutor is not None


@pytest.mark.asyncio
async def test_unified_executor_routes_local_and_mcp() -> None:
    """UnifiedToolExecutor.execute() routes to local first, then MCP."""
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.mcp.manager import AsyncMCPManager, MCPServerHandle, UnifiedToolExecutor

    local = LocalToolRegistry(include_builtins=False)

    @local.register(description="Local echo")
    async def local_echo(text: str) -> str:
        return f"local:{text}"

    mcp = AsyncMCPManager()
    handle = MCPServerHandle(
        name="remote",
        transport="stdio",
        connected=True,
        tools=[{"name": "remote_tool", "description": "...", "inputSchema": {}}],
    )
    mcp._servers["remote"] = handle

    async def mock_send(h, method, params):
        return {"content": [{"type": "text", "text": "mcp_result"}], "isError": False}

    mcp._send = mock_send  # type: ignore[assignment]

    executor = UnifiedToolExecutor(local=local, mcp=mcp)

    # Local tool executes in-process
    local_result = await executor.execute("local_echo", {"text": "hi"})
    assert local_result["is_error"] is False
    assert local_result["content"] == "local:hi"

    # MCP tool executes via the MCP manager
    mcp_result = await executor.execute("remote_tool", {})
    assert mcp_result["is_error"] is False
    assert mcp_result["content"] == "mcp_result"


# ═══════════════════════════════════════════════════════════════════
# KazmaAgent.tools is a UnifiedToolExecutor (handles both MCP + local)
# ═══════════════════════════════════════════════════════════════════


def test_kazma_agent_uses_unified_executor() -> None:
    """KazmaAgent.tools must be a UnifiedToolExecutor (not the deleted ToolRegistry)."""
    from kazma_core.agent import KazmaAgent
    from kazma_core.mcp.manager import UnifiedToolExecutor

    agent = KazmaAgent()
    assert isinstance(agent.tools, UnifiedToolExecutor), (
        f"KazmaAgent.tools is {type(agent.tools).__name__}, expected UnifiedToolExecutor"
    )


def test_kazma_agent_tools_exposes_local_builtin_tools() -> None:
    """The unified executor on the agent must expose local built-in tools."""
    from kazma_core.agent import KazmaAgent

    agent = KazmaAgent()
    defs = agent.tools.get_tool_definitions()
    names = {d["function"]["name"] for d in defs}
    # A few representative built-ins that must remain available.
    assert "file_read" in names
    assert "current_datetime" in names


@pytest.mark.asyncio
async def test_kazma_agent_can_execute_local_tool() -> None:
    """The agent's unified executor can execute a real local tool."""
    from kazma_core.agent import KazmaAgent

    agent = KazmaAgent()
    result = await agent.tools.execute("current_datetime", {})
    assert result["is_error"] is False
    assert result["content"]  # non-empty ISO timestamp


# ═══════════════════════════════════════════════════════════════════
# MCP server wiring on the unified executor
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_kazma_agent_connect_mcp_servers_delegates_to_unified_executor(monkeypatch) -> None:
    """connect_mcp_servers must route through the UnifiedToolExecutor's MCP manager."""
    from kazma_core.agent import KazmaAgent

    agent = KazmaAgent()

    # Spy on the underlying AsyncMCPManager.connect_from_config
    called: list[list] = []

    async def _fake_connect(servers):
        called.append(servers)
        return 0

    assert agent.tools._mcp is not None
    monkeypatch.setattr(agent.tools._mcp, "connect_from_config", _fake_connect)

    # Provide a dummy server config
    agent.config.raw.setdefault("mcp", {})["servers"] = [
        {"name": "dummy", "transport": "stdio", "command": ["echo"]}
    ]
    await agent.connect_mcp_servers()
    assert called, "connect_mcp_servers did not delegate to the MCP manager"
    await agent.shutdown()


@pytest.mark.asyncio
async def test_unified_executor_list_servers_for_mcp_ui() -> None:
    """UnifiedToolExecutor exposes list_servers() so mcp_ui.py can list MCP servers."""
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.mcp.manager import AsyncMCPManager, MCPServerHandle, UnifiedToolExecutor

    mcp = AsyncMCPManager()
    handle = MCPServerHandle(
        name="fs",
        transport="stdio",
        connected=True,
        tools=[{"name": "read_file", "description": "Read", "inputSchema": {}}],
    )
    mcp._servers["fs"] = handle

    executor = UnifiedToolExecutor(local=LocalToolRegistry(include_builtins=False), mcp=mcp)
    servers = executor.list_servers()
    names = [s["name"] for s in servers]
    assert "fs" in names
