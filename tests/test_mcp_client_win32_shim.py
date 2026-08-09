"""Regression test: MCPClient must resolve .cmd/.bat shims on Windows.

``subprocess.Popen(["npx", ...])`` raises FileNotFoundError on Windows even
when ``npx.cmd`` is on PATH, because CreateProcess does not consult PATHEXT.
The client must resolve the executable via ``shutil.which`` first (mirroring
the fix in ``mcp/manager.py``).
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from kazma_core.mcp_client import MCPClient, MCPServerConfig


@pytest.mark.asyncio
async def test_stdio_resolves_cmd_shim_on_win32() -> None:
    client = MCPClient()
    cfg = MCPServerConfig(
        name="fs",
        transport="stdio",
        command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )
    with (
        patch.object(sys, "platform", "win32"),
        patch("kazma_core.mcp_client.shutil.which", return_value=r"C:\nodejs\npx.cmd") as mock_which,
        patch("kazma_core.mcp_client.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value = MagicMock()
        await client._connect_stdio(cfg)

    mock_which.assert_called_once()
    spawned = mock_popen.call_args[0][0]
    assert spawned[0] == r"C:\nodejs\npx.cmd"
    assert spawned[1:] == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]


@pytest.mark.asyncio
async def test_stdio_leaves_command_untouched_when_unresolved() -> None:
    client = MCPClient()
    cfg = MCPServerConfig(name="x", transport="stdio", command=["missing-bin"])
    with (
        patch.object(sys, "platform", "win32"),
        patch("kazma_core.mcp_client.shutil.which", return_value=None),
        patch("kazma_core.mcp_client.subprocess.Popen", side_effect=FileNotFoundError),
    ):
        with pytest.raises(Exception, match="Command not found: missing-bin"):
            await client._connect_stdio(cfg)
