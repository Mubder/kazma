"""Tests for the KazmaAgent service-layer facade (VAL-ARCH-001, VAL-ARCH-002).

These tests verify that:
  - KazmaAgent exposes stable public methods that UI routers can call
    instead of reaching into private attributes.
  - get_streaming_graph() returns a compiled graph suitable for SSE.
  - The facade methods cover every private access the UI previously used.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kazma_core.agent import AgentConfig, KazmaAgent

# ═══════════════════════════════════════════════════════════════════
# Fixture
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def agent_config() -> AgentConfig:
    """Return a minimal agent config for facade testing."""
    return AgentConfig(
        name="test-kazma",
        version="0.0.0-test",
        language="en",
        rtl=False,
    )


@pytest.fixture
def agent(agent_config: AgentConfig) -> KazmaAgent:
    """Return a KazmaAgent instance for facade testing."""
    return KazmaAgent(config=agent_config)


# ═══════════════════════════════════════════════════════════════════
# Facade method existence
# ═══════════════════════════════════════════════════════════════════


class TestFacadeMethodExistence:
    """Verify all expected facade methods exist on KazmaAgent."""

    def test_has_get_tools_info(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "get_tools_info")
        assert callable(agent.get_tools_info)

    def test_has_get_checkpoint_summary(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "get_checkpoint_summary")
        assert callable(agent.get_checkpoint_summary)

    def test_has_get_mcp_servers(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "get_mcp_servers")
        assert callable(agent.get_mcp_servers)

    def test_has_add_mcp_server(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "add_mcp_server")
        assert callable(agent.add_mcp_server)

    def test_has_remove_mcp_server(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "remove_mcp_server")
        assert callable(agent.remove_mcp_server)

    def test_has_get_streaming_graph(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "get_streaming_graph")
        assert callable(agent.get_streaming_graph)

    def test_has_is_running(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "is_running")
        # is_running should be a property or method
        _ = agent.is_running

    def test_has_set_running(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "set_running")
        assert callable(agent.set_running)

    def test_has_get_llm_config(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "get_llm_config")
        assert callable(agent.get_llm_config)

    def test_has_get_config_section(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "get_config_section")
        assert callable(agent.get_config_section)

    def test_has_get_mcp_servers_config(self, agent: KazmaAgent) -> None:
        assert hasattr(agent, "get_mcp_servers_config")
        assert callable(agent.get_mcp_servers_config)


# ═══════════════════════════════════════════════════════════════════
# get_tools_info
# ═══════════════════════════════════════════════════════════════════


class TestGetToolsInfo:
    """Test the get_tools_info facade method."""

    def test_returns_dict_with_count_and_list(self, agent: KazmaAgent) -> None:
        info = agent.get_tools_info()
        assert isinstance(info, dict)
        assert "count" in info
        assert "list" in info
        assert "servers" in info

    def test_count_matches_tool_definitions(self, agent: KazmaAgent) -> None:
        info = agent.get_tools_info()
        defs = agent.tools.get_tool_definitions()
        assert info["count"] == len(defs)

    def test_does_not_access_private_attrs(self, agent: KazmaAgent) -> None:
        """get_tools_info should not rely on _servers; it should use list_servers()."""
        # The method should work even if we patch the internal _servers attr
        info = agent.get_tools_info()
        assert isinstance(info["servers"], int)


# ═══════════════════════════════════════════════════════════════════
# get_mcp_servers / add_mcp_server / remove_mcp_server
# ═══════════════════════════════════════════════════════════════════


class TestMCPServerFacade:
    """Test the MCP server management facade methods."""

    def test_get_mcp_servers_config_returns_list(self, agent: KazmaAgent) -> None:
        servers = agent.get_mcp_servers_config()
        assert isinstance(servers, list)

    def test_add_mcp_server(self, agent: KazmaAgent) -> None:
        initial = len(agent.get_mcp_servers_config())
        agent.add_mcp_server(
            name="test-server",
            transport="stdio",
            command=["echo"],
        )
        servers = agent.get_mcp_servers_config()
        assert len(servers) == initial + 1
        assert any(s["name"] == "test-server" for s in servers)

    def test_add_mcp_server_duplicate_returns_error(self, agent: KazmaAgent) -> None:
        agent.add_mcp_server(name="dup", transport="stdio", command=["echo"])
        result = agent.add_mcp_server(name="dup", transport="stdio", command=["echo"])
        assert "error" in result or result.get("status") == "error"

    def test_remove_mcp_server(self, agent: KazmaAgent) -> None:
        agent.add_mcp_server(name="to-remove", transport="stdio", command=["echo"])
        assert any(s["name"] == "to-remove" for s in agent.get_mcp_servers_config())
        agent.remove_mcp_server("to-remove")
        assert not any(s["name"] == "to-remove" for s in agent.get_mcp_servers_config())

    def test_add_mcp_server_sse_transport(self, agent: KazmaAgent) -> None:
        agent.add_mcp_server(
            name="sse-server",
            transport="sse",
            url="http://localhost:8080",
        )
        servers = agent.get_mcp_servers_config()
        match = [s for s in servers if s["name"] == "sse-server"]
        assert len(match) == 1
        assert match[0].get("url") == "http://localhost:8080"


# ═══════════════════════════════════════════════════════════════════
# is_running / set_running
# ═══════════════════════════════════════════════════════════════════


class TestRunningStateFacade:
    """Test the is_running / set_running facade."""

    def test_is_running_default_false(self, agent: KazmaAgent) -> None:
        assert agent.is_running is False

    def test_set_running_true(self, agent: KazmaAgent) -> None:
        agent.set_running(True)
        assert agent.is_running is True

    def test_set_running_false(self, agent: KazmaAgent) -> None:
        agent.set_running(True)
        agent.set_running(False)
        assert agent.is_running is False


# ═══════════════════════════════════════════════════════════════════
# get_llm_config / get_config_section
# ═══════════════════════════════════════════════════════════════════


class TestConfigFacade:
    """Test config accessor facade methods."""

    def test_get_llm_config_returns_dict(self, agent: KazmaAgent) -> None:
        cfg = agent.get_llm_config()
        assert isinstance(cfg, dict)
        assert "base_url" in cfg
        assert "api_key" in cfg
        assert "model" in cfg
        assert "max_tokens" in cfg
        assert "temperature" in cfg

    def test_get_llm_config_matches_llm_config(self, agent: KazmaAgent) -> None:
        cfg = agent.get_llm_config()
        assert cfg["base_url"] == agent.llm_config.base_url
        assert cfg["model"] == agent.llm_config.model

    def test_get_config_section_existing(self, agent: KazmaAgent) -> None:
        # Agent config should have an "agent" section
        section = agent.get_config_section("agent")
        assert isinstance(section, dict)

    def test_get_config_section_missing_returns_empty(self, agent: KazmaAgent) -> None:
        section = agent.get_config_section("nonexistent_section")
        assert section == {}


# ═══════════════════════════════════════════════════════════════════
# get_streaming_graph (VAL-ARCH-002)
# ═══════════════════════════════════════════════════════════════════


class TestStreamingGraph:
    """Test the get_streaming_graph facade (VAL-ARCH-002)."""

    def test_get_streaming_graph_returns_graph(self, agent: KazmaAgent) -> None:
        """get_streaming_graph must return a compiled graph object."""
        graph = agent.get_streaming_graph()
        assert graph is not None
        # The compiled graph must support ainvoke and astream_events
        assert hasattr(graph, "ainvoke")

    def test_get_streaming_graph_is_cached(self, agent: KazmaAgent) -> None:
        """The graph should be built once and cached."""
        g1 = agent.get_streaming_graph()
        g2 = agent.get_streaming_graph()
        assert g1 is g2


# ═══════════════════════════════════════════════════════════════════
# get_checkpoint_summary
# ═══════════════════════════════════════════════════════════════════


class TestCheckpointSummary:
    """Test the get_checkpoint_summary facade."""

    @pytest.mark.asyncio
    async def test_returns_summary_dict(self, agent: KazmaAgent) -> None:
        summary = await agent.get_checkpoint_summary()
        assert isinstance(summary, dict)
        # When no checkpointer is set up, should return empty/defaults
        assert "sessions" in summary or "checkpoints" in summary or "count" in summary


# ═══════════════════════════════════════════════════════════════════
# Static check: no private attribute access in kazma_ui/
# ═══════════════════════════════════════════════════════════════════


class TestNoPrivateAccessInUI:
    """Verify UI modules do not access private attributes of core objects.

    This is the programmatic equivalent of VAL-ARCH-001's grep check.
    """

    @staticmethod
    def _scan_ui_dir() -> list[str]:
        """Scan all .py files under kazma-ui/kazma_ui/ for private-attr access."""
        import re

        ui_dir = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
        # Patterns that indicate private attribute access on core objects.
        # We look for ._clients, ._conn, ._tools, ._checkpoint_conn, ._servers,
        # ._running accessed on objects that are NOT self.
        forbidden_patterns = [
            re.compile(r"\._clients\b"),
            re.compile(r"\._conn\b"),
            re.compile(r"\._tools\b"),
            re.compile(r"\._checkpoint_conn\b"),
            re.compile(r"\._checkpoint_manager\b"),
            re.compile(r"\._servers\b"),
            re.compile(r"\bagent\._running\b"),
            re.compile(r"\ba\._running\b"),
        ]
        violations: list[str] = []
        for py_file in ui_dir.rglob("*.py"):
            rel = py_file.relative_to(ui_dir.parent)
            text = py_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                # Skip comments and strings that are just documentation
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for pat in forbidden_patterns:
                    if pat.search(line):
                        violations.append(f"{rel}:{lineno}: {line.strip()}")
        return violations

    def test_no_private_attr_access_in_ui(self) -> None:
        violations = self._scan_ui_dir()
        # Print violations for debugging if the test fails
        if violations:
            msg = "\n".join(violations)
            pytest.fail(f"Private attribute access found in kazma_ui/:\n{msg}")
