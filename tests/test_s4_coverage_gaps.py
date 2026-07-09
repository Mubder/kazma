"""S4 coverage fillers for modules previously listed as thin/zero coverage.

Thin, isolated unit tests — no network, no real LLM.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from kazma_core.agent_runner import AgentConfig, load_config
from kazma_core.compaction import CompactionEngine
from kazma_core.mcp_client import MCPClient, MCPConnectionError, MCPServerConfig
from kazma_core.permissions import PermissionManager
from kazma_core.settings_manager import MCPSettingsService
from kazma_core.tracing import TraceEntry, TraceStore
from kazma_gateway.gateway import GatewayManager, IncomingMessage, MessageMetrics, RateLimiter


# ── agent_runner.load_config ───────────────────────────────────────────


class TestAgentRunnerConfig:
    def test_load_config_missing_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "nope.yaml")
        assert isinstance(cfg, AgentConfig)
        assert cfg.name == "kazma"

    def test_load_config_from_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "kazma.yaml"
        p.write_text(
            yaml.dump(
                {
                    "agent": {"name": "test-agent", "version": "9.9.9"},
                    "models": {"default": "gpt-test"},
                    "system_prompt": "You are a test.",
                }
            ),
            encoding="utf-8",
        )
        cfg = load_config(p)
        assert cfg.name == "test-agent"
        assert cfg.version == "9.9.9"
        assert cfg.default_model == "gpt-test"
        assert cfg.system_prompt == "You are a test."

    def test_get_streaming_graph_passes_hitl_and_caches(self) -> None:
        from kazma_core.agent_runner import KazmaAgent

        agent = MagicMock(spec=KazmaAgent)
        agent._streaming_graph = None
        agent.config = AgentConfig(
            raw={"safety": {"hitl": {"enabled": True, "require_approval_for": ["shell_exec"]}}}
        )
        agent.llm = MagicMock()
        agent.system_prompt = "sys"
        agent.tools = MagicMock()
        agent.tools.get_tool_definitions.return_value = []
        agent.cost_breaker = MagicMock()
        agent.authority = MagicMock()
        agent.tracer = MagicMock()

        fake_graph = object()
        with (
            patch(
                "kazma_core.agent.graph_builder.build_supervisor_graph",
                return_value=fake_graph,
            ) as build,
            patch(
                "kazma_core.safety.hitl.get_hitl_config",
                return_value={"enabled": True, "require_approval_for": ["shell_exec"]},
            ),
        ):
            # Bind real method
            result = KazmaAgent.get_streaming_graph(agent)
            assert result is fake_graph
            assert agent._streaming_graph is fake_graph
            build.assert_called_once()
            kwargs = build.call_args.kwargs
            assert kwargs.get("hitl_config") is not None
            assert kwargs["hitl_config"]["enabled"] is True

            # Second call uses cache — no rebuild
            result2 = KazmaAgent.get_streaming_graph(agent)
            assert result2 is fake_graph
            assert build.call_count == 1


# ── mcp_client auth injection ──────────────────────────────────────────


class TestMCPClientAuth:
    @pytest.mark.asyncio
    async def test_connect_sse_requires_url(self) -> None:
        client = MCPClient()
        with pytest.raises(MCPConnectionError, match="URL"):
            await client._connect_sse(MCPServerConfig(name="x", transport="sse", url=""))

    @pytest.mark.asyncio
    async def test_connect_sse_injects_bearer_auth(self) -> None:
        client = MCPClient()
        cfg = MCPServerConfig(
            name="remote",
            transport="sse",
            url="http://example.invalid",
            auth={"type": "bearer", "token": "sekrit"},
            headers={"X-Extra": "1"},
        )
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            await client._connect_sse(cfg)
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args.kwargs
            headers = kwargs["headers"]
            assert headers["Authorization"] == "Bearer sekrit"
            assert headers["X-Extra"] == "1"

    @pytest.mark.asyncio
    async def test_connect_sse_injects_custom_header_auth(self) -> None:
        client = MCPClient()
        cfg = MCPServerConfig(
            name="remote",
            transport="sse",
            url="http://example.invalid",
            auth={"type": "header", "name": "X-Api-Key", "value": "abc"},
        )
        with patch("httpx.AsyncClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            await client._connect_sse(cfg)
            headers = mock_cls.call_args.kwargs["headers"]
            assert headers["X-Api-Key"] == "abc"


# ── permissions matrix ─────────────────────────────────────────────────


class TestPermissionsMatrix:
    def test_deny_overrides_wildcard(self, tmp_path: Path) -> None:
        pm = PermissionManager(config_path=tmp_path / "p.yaml")
        pm.grant("*")
        pm.deny("shell_exec")
        assert pm.is_allowed("file_read") is True
        assert pm.is_allowed("shell_exec") is False

    def test_grant_removes_from_denied(self, tmp_path: Path) -> None:
        pm = PermissionManager(config_path=tmp_path / "p.yaml")
        pm.deny("web_search")
        assert pm.is_allowed("web_search") is False
        pm.grant("web_search")
        assert pm.is_allowed("web_search") is True


# ── compaction heuristic ───────────────────────────────────────────────


class TestCompactionHeuristic:
    @pytest.mark.asyncio
    async def test_empty_messages(self) -> None:
        eng = CompactionEngine()
        summary = await eng.summarize([])
        assert "CONTEXT SUMMARY" in summary

    @pytest.mark.asyncio
    async def test_heuristic_keeps_user_text(self) -> None:
        eng = CompactionEngine(llm_client=None)
        messages = [
            {"role": "user", "content": "Build a drone report"},
            {"role": "assistant", "content": "Sure"},
        ]
        summary = await eng.summarize(messages)
        assert "drone" in summary.lower() or "2" in summary  # count or content

    @pytest.mark.asyncio
    async def test_compact_returns_system_message(self) -> None:
        eng = CompactionEngine()
        state = {
            "messages": [{"role": "user", "content": "hello"}],
            "tool_results": {},
            "context_tokens": 100,
            "last_cp_id": "",
            "created_at": "",
            "provenance": {},
        }
        new_state = await eng.compact(state)  # type: ignore[arg-type]
        assert len(new_state["messages"]) == 1
        assert new_state["messages"][0]["role"] == "system"


# ── gateway smoke ──────────────────────────────────────────────────────


class TestGatewaySmoke:
    def test_incoming_message_fields(self) -> None:
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:1",
            text="hi",
            context_metadata={"chat_id": 99},
        )
        assert msg.platform == "telegram"
        assert msg.reply_target() == "telegram:1"
        assert msg.correlation_id.startswith("cid-")

    def test_metrics_snapshot(self) -> None:
        m = MessageMetrics()
        snap = m.snapshot()
        assert snap["inbound_total"] == 0
        assert "errors_total" in snap

    @pytest.mark.asyncio
    async def test_rate_limiter_acquire(self) -> None:
        rl = RateLimiter(max_per_second=100)
        await rl.acquire()
        assert rl._tokens < 100

    @pytest.mark.asyncio
    async def test_gateway_manager_start_stop_no_adapters(self) -> None:
        gw = GatewayManager(max_queue_size=10)
        await gw.start()
        assert gw._started is True
        await gw.stop()
        assert gw._started is False

    def test_gateway_add_adapter(self) -> None:
        gw = GatewayManager()
        adapter = MagicMock()
        adapter.name = "mock"
        gw.add_adapter(adapter)
        assert len(gw.adapters) == 1


# ── settings MCP service ───────────────────────────────────────────────


class TestMCPSettingsService:
    def test_add_get_delete_mcp_server(self) -> None:
        store = MagicMock()
        # Simulate empty then populated list
        store._servers: list = []

        def _get(key, default=None):
            if key == "mcp.servers":
                return store._servers
            return default

        def _set(key, value, category="general"):
            if key == "mcp.servers":
                import json

                store._servers = json.loads(value) if isinstance(value, str) else value

        store.get.side_effect = _get
        store.set.side_effect = _set

        svc = MCPSettingsService(store)
        added = svc.add_mcp_server(
            {"name": "local-fs", "transport": "stdio", "command": ["npx", "server"]}
        )
        assert added["name"] == "local-fs"
        assert len(svc.get_mcp_servers()) == 1
        svc.delete_mcp_server("local-fs")
        assert svc.get_mcp_servers() == []


# ── tracing TraceStore ─────────────────────────────────────────────────


class TestTraceStore:
    def test_ring_buffer_and_metrics(self) -> None:
        store = TraceStore(max_entries=3)
        for i in range(5):
            store._traces.append(
                TraceEntry(
                    timestamp=float(i),
                    trace_type="llm",
                    label=f"call-{i}",
                    status="success",
                    duration_ms=1.0,
                    tokens=10,
                    cost=0.01,
                )
            )
            store._total_tokens += 10
            store._total_cost += 0.01
            store._total_llm_calls += 1
        assert len(store._traces) == 3  # ring capacity
        assert store._total_llm_calls == 5
