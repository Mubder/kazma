"""MCP Client — Connects to MCP servers and executes tools.

Supports both stdio and SSE transports per the Model Context Protocol
(JSON-RPC 2.0). Each MCPClient instance manages a single server connection.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

_request_counter = itertools.count(1)


def _next_id() -> int:
    return next(_request_counter)


def _jsonrpc_request(method: str, params: dict[str, Any] | None = None) -> dict:
    """Build a JSON-RPC 2.0 request payload."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": _next_id(), "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _jsonrpc_response(text: str) -> dict[str, Any]:
    """Parse a JSON-RPC 2.0 response from raw text."""
    data = json.loads(text)
    if "error" in data:
        raise MCPError(data["error"].get("message", str(data["error"])))
    return data.get("result", {})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MCPError(Exception):
    """Raised when an MCP server returns a JSON-RPC error or the transport fails."""


class MCPConnectionError(MCPError):
    """Raised when the client cannot establish or maintain a connection."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """Configuration for connecting to an MCP server."""

    name: str
    transport: str = "stdio"  # "stdio" | "sse"
    # stdio fields
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    working_dir: str | None = None
    # sse fields
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------


class MCPClient:
    """Connects to an MCP server and executes tools over JSON-RPC 2.0.

    Supports two transports:
    * **stdio** — spawns a child process and communicates via stdin/stdout.
    * **sse** — connects to a remote server over HTTP SSE.
    """

    def __init__(self) -> None:
        self._config: MCPServerConfig | None = None
        self._connected: bool = False
        self._tools: list[dict[str, Any]] = []
        self._process: subprocess.Popen[bytes] | None = None
        self._http: httpx.AsyncClient | None = None
        self._read_lock: asyncio.Lock = asyncio.Lock()

    # -- public API --------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_name(self) -> str:
        return self._config.name if self._config else ""

    async def connect(self, server_config: dict[str, Any] | MCPServerConfig) -> bool:
        """Connect to an MCP server.

        Args:
            server_config: Either a ``MCPServerConfig`` or a plain dict with
                the same keys.

        Returns:
            ``True`` if the connection succeeded and the server responded to
            ``initialize``.

        Raises:
            MCPConnectionError: If the transport layer fails.
        """
        if isinstance(server_config, MCPServerConfig):
            cfg = server_config
        else:
            cfg = MCPServerConfig(**server_config)

        self._config = cfg

        if cfg.transport == "stdio":
            await self._connect_stdio(cfg)
        elif cfg.transport == "sse":
            await self._connect_sse(cfg)
        else:
            raise MCPConnectionError(f"Unsupported transport: {cfg.transport}")

        # MCP handshake: send initialize, then initialized notification
        result = await self._send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "clientInfo": {"name": "kazma-mcp-client", "version": "0.1.0"},
            },
        )
        if not isinstance(result, dict):
            raise MCPConnectionError("Server returned non-dict initialize result")

        await self._notify("notifications/initialized", {})
        self._connected = True
        logger.info("Connected to MCP server '%s' via %s", cfg.name, cfg.transport)
        return True

    async def list_tools(self) -> list[dict[str, Any]]:
        """List available tools from the connected server.

        Returns:
            List of tool descriptors as dicts with at least ``name`` and
            ``description`` keys.

        Raises:
            MCPError: If the client is not connected or the call fails.
        """
        self._assert_connected()
        result = await self._send("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        self._tools = tools
        logger.info("Listed %d tools from server '%s'", len(tools), self.server_name)
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a tool on the connected server.

        Args:
            name: Tool name as reported by ``list_tools``.
            arguments: Keyword arguments for the tool.

        Returns:
            The tool result dict (may contain ``content`` and/or ``isError``).

        Raises:
            MCPError: If the call fails or the server reports an error.
        """
        self._assert_connected()
        params: dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = arguments
        result = await self._send("tools/call", params)
        logger.debug("Tool '%s' executed on server '%s'", name, self.server_name)
        return result if isinstance(result, dict) else {"content": str(result)}

    async def disconnect(self) -> None:
        """Cleanly disconnect from the server."""
        if self._process is not None:
            try:
                self._process.stdin.close()  # type: ignore[union-attr]
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._http is not None:
            await self._http.aclose()
            self._http = None

        name = self.server_name
        self._connected = False
        self._config = None
        self._tools = []
        logger.info("Disconnected from MCP server '%s'", name)

    # -- internal: stdio transport -----------------------------------------

    async def _connect_stdio(self, cfg: MCPServerConfig) -> None:
        if not cfg.command:
            raise MCPConnectionError("stdio transport requires a non-empty command")

        env = {**os.environ, **cfg.env}

        try:
            self._process = subprocess.Popen(
                cfg.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cfg.working_dir,
                env=env,
            )
        except FileNotFoundError as exc:
            raise MCPConnectionError(f"Command not found: {cfg.command[0]}") from exc
        except OSError as exc:
            raise MCPConnectionError(f"Failed to start process: {exc}") from exc

        logger.debug("Spawned stdio process: pid=%s cmd=%s", self._process.pid, cfg.command)

    async def _connect_sse(self, cfg: MCPServerConfig) -> None:
        if not cfg.url:
            raise MCPConnectionError("SSE transport requires a URL")

        self._http = httpx.AsyncClient(
            base_url=cfg.url,
            headers=cfg.headers,
            timeout=cfg.timeout,
        )
        logger.debug("Created SSE HTTP client for %s", cfg.url)

    # -- internal: JSON-RPC transport --------------------------------------

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        if self._config is None:
            raise MCPConnectionError("Not connected")

        request = _jsonrpc_request(method, params)
        raw = json.dumps(request) + "\n"

        if self._config.transport == "stdio":
            return await self._send_stdio(raw)
        return await self._send_sse(raw, method)

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if self._config is None:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        raw = json.dumps(msg) + "\n"

        if self._config.transport == "stdio":
            proc = self._process
            if proc is None or proc.stdin is None:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, proc.stdin.write, raw.encode())
            await loop.run_in_executor(None, proc.stdin.flush)
        elif self._config.transport == "sse" and self._http is not None:
            await self._http.post("/notifications", content=raw, headers={"Content-Type": "application/json"})

    async def _send_stdio(self, raw: str) -> Any:
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise MCPConnectionError("stdio process not running")

        # Write in a thread to avoid blocking the event loop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, proc.stdin.write, raw.encode())
        await loop.run_in_executor(None, proc.stdin.flush)

        # Read response line (blocking read in executor)
        async with self._read_lock:
            line = await loop.run_in_executor(None, proc.stdout.readline)

        if not line:
            raise MCPConnectionError("Server closed stdout (EOF)")

        return _jsonrpc_response(line.decode().strip())

    async def _send_sse(self, raw: str, method: str) -> Any:
        if self._http is None:
            raise MCPConnectionError("SSE client not initialised")

        resp = await self._http.post(
            "/jsonrpc",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return _jsonrpc_response(resp.text)

    # -- helpers -----------------------------------------------------------

    def _assert_connected(self) -> None:
        if not self._connected:
            raise MCPError("Not connected to any MCP server")
