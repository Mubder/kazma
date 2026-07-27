"""MCP stdio spawn must raise StreamReader limit above 64 KiB default."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_connect_stdio_passes_raised_limit(monkeypatch) -> None:
    from kazma_core.mcp.manager import AsyncMCPManager

    captured: dict[str, Any] = {}

    async def fake_exec(*args, **kwargs):
        captured["kwargs"] = kwargs
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        proc.returncode = None
        # Handshake + tools/list need _send to succeed
        return proc

    mgr = AsyncMCPManager()

    async def fake_send(handle, method, params=None, timeout=None):
        if method == "initialize":
            return {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "fs"}}
        if method == "tools/list":
            return {"tools": [{"name": "list_directory", "description": "list", "inputSchema": {}}]}
        return {}

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(mgr, "_send", fake_send)
    monkeypatch.setattr(mgr, "_notify", AsyncMock())

    monkeypatch.setenv("KAZMA_MCP_STDIO_LIMIT", str(4 * 1024 * 1024))

    n = await mgr._connect_stdio(
        "filesystem",
        {
            "name": "filesystem",
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        },
    )
    assert n >= 1
    assert "limit" in captured["kwargs"]
    assert captured["kwargs"]["limit"] == 4 * 1024 * 1024


@pytest.mark.asyncio
async def test_framing_error_marks_disconnected() -> None:
    """After chunk-exceed, handle must be marked disconnected (poisoned pipe)."""
    from kazma_core.mcp.manager import AsyncMCPManager, MCPBridgeError, MCPServerHandle

    mgr = AsyncMCPManager()
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.stderr = None
    proc.returncode = None

    async def boom_readline():
        raise ValueError("Separator is not found, and chunk exceed the limit")

    proc.stdout.readline = boom_readline

    handle = MCPServerHandle(
        name="filesystem",
        transport="stdio",
        process=proc,
        command=["npx"],
    )
    handle.connected = True
    handle.read_lock = asyncio.Lock()
    mgr._servers["filesystem"] = handle

    with pytest.raises(MCPBridgeError) as ei:
        await mgr._send_stdio(handle, '{"jsonrpc":"2.0","id":1}\n')
    assert "chunk exceed" in str(ei.value).lower() or "separator" in str(ei.value).lower()
    assert handle.connected is False
