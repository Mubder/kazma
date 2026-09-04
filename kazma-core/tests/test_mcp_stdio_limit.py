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
async def test_win32_which_hit_prepends_shim_dir_without_unbound_os(
    monkeypatch,
) -> None:
    """2026-09-04: ``import os as _os`` lived only in the npx-not-found
    probe. ``shutil.which`` succeeding (the common case) skipped that
    import, then ``if resolved:`` used ``_os`` → UnboundLocalError, which
    the reconnect loop classified as transient and paged Telegram every
    30 min for sequential-thinking.
    """
    import sys
    from kazma_core.mcp.manager import AsyncMCPManager

    captured: dict[str, Any] = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        proc = MagicMock()
        proc.stdin = MagicMock()
        proc.stdin.write = MagicMock()
        proc.stdin.drain = AsyncMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        proc.returncode = None
        return proc

    mgr = AsyncMCPManager()

    async def fake_send(handle, method, params=None, timeout=None):
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "st"},
            }
        if method == "tools/list":
            return {
                "tools": [{"name": "think", "description": "t", "inputSchema": {}}]
            }
        return {}

    shim = r"C:\nvm4w\nodejs\npx.cmd"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("shutil.which", lambda cmd: shim)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(mgr, "_send", fake_send)
    monkeypatch.setattr(mgr, "_notify", AsyncMock())

    n = await mgr._connect_stdio(
        "sequential-thinking",
        {
            "name": "sequential-thinking",
            "transport": "stdio",
            "command": [
                "npx",
                "-y",
                "@modelcontextprotocol/server-sequential-thinking",
            ],
        },
    )
    assert n >= 1
    assert captured["args"][0] == shim
    child_path = captured["kwargs"]["env"]["PATH"]
    assert child_path.startswith(r"C:\nvm4w\nodejs")


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
