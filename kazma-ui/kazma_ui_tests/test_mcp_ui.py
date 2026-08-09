"""Focused tests for MCP Web UI persistence and connection testing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.templating import Jinja2Templates
from jinja2 import BaseLoader, Environment

from kazma_ui.mcp_ui import create_mcp_router
from kazma_ui.models import MCPServerAddRequest


def _router_endpoint(router, path: str, method: str):
    """Return the endpoint registered for an exact route path."""
    return next(
        route.endpoint
        for route in router.routes
        if route.path == path and method in route.methods
    )


@pytest.fixture
def mcp_agent() -> MagicMock:
    agent = MagicMock()
    agent.add_mcp_server.return_value = {"status": "ok"}
    agent.remove_mcp_server.return_value = {"status": "ok"}
    agent.tools.is_server_connected.return_value = False
    return agent


@pytest.fixture
def mcp_router(mcp_agent: MagicMock):
    templates = Jinja2Templates(env=Environment(loader=BaseLoader()))
    return create_mcp_router(mcp_agent, templates)


@pytest.mark.asyncio
async def test_add_server_forwards_sse_bearer_auth_and_trust(
    mcp_agent: MagicMock, mcp_router
) -> None:
    endpoint = _router_endpoint(mcp_router, "/api/mcp/servers", "POST")
    request = MCPServerAddRequest(
        name="remote",
        transport="sse",
        url="https://mcp.example.test/sse",
        auth={"type": "bearer", "token": "test-token"},
        trust="trusted",
    )

    result = await endpoint(request)

    assert result == {"status": "ok"}
    assert mcp_agent.add_mcp_server.call_args.kwargs["auth"] == {
        "type": "bearer",
        "token": "test-token",
    }
    assert mcp_agent.add_mcp_server.call_args.kwargs["trust"] == "trusted"


@pytest.mark.asyncio
async def test_test_config_uses_sse_bearer_auth_and_trust(mcp_router) -> None:
    captured_configs = []

    class CapturingMCPClient:
        async def connect(self, config) -> None:
            captured_configs.append(config)

        async def list_tools(self) -> list[dict[str, str]]:
            return [{"name": "remote_tool"}]

        async def disconnect(self) -> None:
            return None

    endpoint = _router_endpoint(mcp_router, "/api/mcp/test-config", "POST")
    with patch("kazma_core.mcp_client.MCPClient", CapturingMCPClient):
        result = await endpoint(
            {
                "name": "remote",
                "transport": "sse",
                "url": "https://mcp.example.test/sse",
                "auth": {"type": "bearer", "token": "test-token"},
                "trust": "trusted",
            }
        )

    assert result["success"] is True
    assert captured_configs[0].auth == {"type": "bearer", "token": "test-token"}
    assert captured_configs[0].trust == "trusted"


@pytest.mark.asyncio
async def test_test_config_uses_streamable_http_manager(mcp_router) -> None:
    """streamable_http must be tested via AsyncMCPManager, not MCPClient."""
    captured = []

    class FakeManager:
        async def connect_from_config(self, configs, *, raise_on_error=False):
            captured.extend(configs)
            return 3

        def get_all_tool_schemas(self):
            return [{"name": "tool1"}, {"name": "tool2"}, {"name": "tool3"}]

        async def shutdown(self):
            pass

    endpoint = _router_endpoint(mcp_router, "/api/mcp/test-config", "POST")
    with patch("kazma_core.mcp_client.MCPClient") as mock_client:
        with patch(
            "kazma_core.mcp.manager.AsyncMCPManager", return_value=FakeManager()
        ) as mock_mgr:
            result = await endpoint(
                {
                    "name": "remote",
                    "transport": "streamable_http",
                    "url": "https://mcp.example.test/devtools",
                    "auth": {"type": "bearer", "token": "test-token"},
                    "trust": "trusted",
                }
            )

    mock_client.assert_not_called()
    assert result["success"] is True
    assert result["tool_count"] == 3
    assert captured[0]["auth"] == {"type": "bearer", "token": "test-token"}


@pytest.mark.asyncio
async def test_remove_server_returns_persistence_error_without_disconnect(
    mcp_agent: MagicMock, mcp_router
) -> None:
    mcp_agent.remove_mcp_server.return_value = {
        "status": "error",
        "error": "Persist failed",
    }
    endpoint = _router_endpoint(mcp_router, "/api/mcp/servers/{name}", "DELETE")

    result = await endpoint("remote")

    assert result == {"status": "error", "error": "Persist failed"}
    mcp_agent.tools.is_server_connected.assert_not_called()
