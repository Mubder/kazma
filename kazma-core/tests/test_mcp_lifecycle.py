"""Lifecycle regression tests for the async MCP bridge."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.mcp.manager import (
    AsyncMCPManager,
    MCPBridgeError,
    MCPServerHandle,
    UnifiedToolExecutor,
)
from kazma_core.mcp_client import MCPClient, MCPConnectionError, MCPServerConfig


@pytest.mark.asyncio
async def test_repeated_connect_reuses_healthy_server() -> None:
    """A second start must not spawn another process for an active server."""
    manager = AsyncMCPManager()
    calls = 0

    async def connect(name: str, _cfg: dict[str, object]) -> int:
        nonlocal calls
        calls += 1
        manager._servers[name] = MCPServerHandle(
            name=name,
            transport="stdio",
            connected=True,
            tools=[{"name": "read_file"}],
        )
        return 1

    manager._connect_stdio = connect  # type: ignore[method-assign]
    cfg = {"name": "files", "transport": "stdio", "command": ["server"]}

    assert await manager.connect_from_config([cfg]) == 1
    assert await manager.connect_from_config([cfg]) == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_strict_connection_reports_actual_failure() -> None:
    """Connection tests must not turn an error into a misleading zero-tools success."""
    manager = AsyncMCPManager()
    manager._connect_stdio = AsyncMock(  # type: ignore[method-assign]
        side_effect=MCPBridgeError("Command not found: mcp-server")
    )

    with pytest.raises(MCPBridgeError, match="Command not found: mcp-server"):
        await manager.connect_from_config(
            [{"name": "broken", "transport": "stdio", "command": ["mcp-server"]}],
            raise_on_error=True,
        )

    assert manager.connection_errors == {"broken": "Command not found: mcp-server"}


@pytest.mark.asyncio
async def test_settings_test_returns_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settings must not label a swallowed manager failure as a zero-tool success."""
    from kazma_core.settings_mcp import MCPSettingsService

    manager = AsyncMCPManager()
    connect = AsyncMock(side_effect=MCPBridgeError("Command not found: mcp-server"))
    shutdown = AsyncMock()
    monkeypatch.setattr("kazma_core.mcp.manager.AsyncMCPManager", lambda: manager)
    monkeypatch.setattr(manager, "connect_from_config", connect)
    monkeypatch.setattr(manager, "shutdown", shutdown)

    service = MCPSettingsService(MagicMock())
    monkeypatch.setattr(
        service,
        "get_mcp_servers",
        lambda: [{"name": "broken", "transport": "stdio", "command": ["mcp-server"]}],
    )

    result = await service.test_mcp_server("broken")

    assert result == {
        "success": False,
        "error": "Command not found: mcp-server",
    }
    assert connect.await_args.kwargs == {"raise_on_error": True}
    shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_tools_discovery_failure_closes_stdio_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed tools/list after initialize must not orphan the child process."""
    manager = AsyncMCPManager()
    process = MagicMock()
    process.stdin = MagicMock()
    process.stdin.drain = AsyncMock()
    process.stdout = MagicMock()
    process.stderr = None
    process.returncode = None
    process.wait = AsyncMock()

    async def fake_exec(*_args: object, **_kwargs: object) -> MagicMock:
        return process

    async def fake_send(
        _handle: MCPServerHandle,
        method: str,
        _params: dict[str, object] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, object]:
        del timeout
        if method == "tools/list":
            raise MCPBridgeError("tools/list rejected")
        return {}

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(manager, "_send", fake_send)
    monkeypatch.setattr(manager, "_notify", AsyncMock())

    with pytest.raises(MCPBridgeError, match="tools/list rejected"):
        await manager._connect_stdio(
            "broken",
            {"name": "broken", "transport": "stdio", "command": ["server"]},
        )

    process.terminate.assert_called_once()
    process.wait.assert_awaited_once()
    assert "broken" not in manager._servers


@pytest.mark.asyncio
async def test_stdio_requests_keep_write_and_response_together() -> None:
    """Concurrent calls cannot steal one another's line-delimited response."""
    manager = AsyncMCPManager()
    first_read_started = asyncio.Event()
    responses: asyncio.Queue[bytes] = asyncio.Queue()
    writes: list[bytes] = []

    async def readline() -> bytes:
        first_read_started.set()
        return await responses.get()

    stdin = SimpleNamespace(
        write=lambda data: writes.append(data),
        drain=AsyncMock(),
    )
    process = SimpleNamespace(
        stdin=stdin,
        stdout=SimpleNamespace(readline=readline),
        stderr=None,
        returncode=None,
    )
    handle = MCPServerHandle(
        name="files",
        transport="stdio",
        process=process,  # type: ignore[arg-type]
        connected=True,
    )

    first = asyncio.create_task(manager._send_stdio(handle, '{"id": 1}\n'))
    await first_read_started.wait()
    second = asyncio.create_task(manager._send_stdio(handle, '{"id": 2}\n'))
    await asyncio.sleep(0)
    assert writes == [b'{"id": 1}\n']

    await responses.put(b'{"jsonrpc":"2.0","id":1,"result":{"request":1}}\n')
    assert await first == {"request": 1}

    for _ in range(10):
        if len(writes) == 2:
            break
        await asyncio.sleep(0)
    assert writes == [b'{"id": 1}\n', b'{"id": 2}\n']

    await responses.put(b'{"jsonrpc":"2.0","id":2,"result":{"request":2}}\n')
    assert await second == {"request": 2}


def test_namespaced_mcp_tool_keeps_its_description() -> None:
    """Introspection must match the raw descriptor behind the namespace."""
    manager = AsyncMCPManager()
    manager._servers["files"] = MCPServerHandle(
        name="files",
        transport="stdio",
        connected=True,
        tools=[{"name": "read_file", "description": "Read a workspace file."}],
    )

    tools = UnifiedToolExecutor(mcp=manager).list_tools()

    assert tools == [
        {
            "name": "mcp__files__read_file",
            "description": "Read a workspace file.",
            "category": "mcp",
            "server": "files",
        }
    ]


@pytest.mark.asyncio
async def test_legacy_client_stdio_read_uses_configured_timeout() -> None:
    """The temporary test client must not hang forever on an unresponsive server."""
    client = MCPClient()
    read_started = threading.Event()
    release_read = threading.Event()

    def readline() -> bytes:
        read_started.set()
        release_read.wait()
        return b""

    client._config = MCPServerConfig(
        name="slow",
        transport="stdio",
        command=["server"],
        timeout=0.05,
    )
    client._process = SimpleNamespace(
        stdin=SimpleNamespace(write=lambda _data: None, flush=lambda: None),
        stdout=SimpleNamespace(readline=readline),
    )
    try:
        with pytest.raises(MCPConnectionError, match="timed out after 0.05s"):
            await client._send_stdio('{"id": 1}\n')
        assert read_started.is_set()
        assert client.connected is False
    finally:
        release_read.set()
