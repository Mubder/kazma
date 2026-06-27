"""AsyncMCPManager — Pure-async MCP server lifecycle and tool bridge.

Manages multiple MCP server connections using ``asyncio.create_subprocess_exec``
for stdio transport and ``httpx.AsyncClient`` for SSE.  Provides a unified
interface to discover tools (``get_all_tool_schemas``) and execute them
(``execute_mcp_tool``).

UnifiedToolExecutor wraps a LocalToolRegistry + AsyncMCPManager into a single
``execute(name, args)`` interface that the LangGraph tool_worker node calls.

Architecture
════════════

    Supervisor (LLM)
         │
         ▼
    UnifiedToolExecutor.execute(name, args)
         │
         ├── name in local?  → LocalToolRegistry.execute()
         │
         └── name in mcp?    → AsyncMCPManager.execute_mcp_tool(server, name, args)
                                    │
                                    ├── stdio: asyncio subprocess JSON-RPC
                                    └── sse:   httpx POST /jsonrpc

Config format (from kazma.yaml ``mcp.servers``):
    servers:
      - name: "filesystem"
        transport: "stdio"
        command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
      - name: "web-search"
        transport: "sse"
        url: "http://localhost:8080/sse"
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# JSON-RPC helpers
# ══════════════════════════════════════════════════════════════════════════

_counter = itertools.count(1)


def _jsonrpc_request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next(_counter), "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _jsonrpc_parse(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if "error" in data:
        raise MCPBridgeError(data["error"].get("message", str(data["error"])))
    return data.get("result", {})


# ══════════════════════════════════════════════════════════════════════════
# Errors
# ══════════════════════════════════════════════════════════════════════════


class MCPBridgeError(Exception):
    """Raised when an MCP server returns a JSON-RPC error or transport fails."""


# ══════════════════════════════════════════════════════════════════════════
# Server descriptor
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class MCPServerHandle:
    """Internal handle for a connected MCP server."""

    name: str
    transport: str  # "stdio" | "sse"
    # stdio
    process: asyncio.subprocess.Process | None = None
    command: list[str] = field(default_factory=list)
    # sse
    http: httpx.AsyncClient | None = None
    url: str = ""
    # shared
    tools: list[dict[str, Any]] = field(default_factory=list)
    connected: bool = False
    read_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# ══════════════════════════════════════════════════════════════════════════
# AsyncMCPManager
# ══════════════════════════════════════════════════════════════════════════


class AsyncMCPManager:
    """Manages multiple MCP server connections with pure asyncio I/O.

    Usage::

        manager = AsyncMCPManager()
        await manager.connect_from_config([
            {"name": "fs", "transport": "stdio", "command": ["npx", "-y", "..."]},
            {"name": "web", "transport": "sse", "url": "http://localhost:8080/sse"},
        ])
        schemas = manager.get_all_tool_schemas()
        result = await manager.execute_mcp_tool("fs", "read_file", {"path": "/tmp"})
        await manager.shutdown()
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerHandle] = {}

    # ── Lifecycle ───────────────────────────────────────────────────

    async def connect_from_config(self, servers: list[dict[str, Any]]) -> int:
        """Connect to all servers from a config list.

        Args:
            servers: List of dicts, each with at least ``name`` and ``transport``.

        Returns:
            Total number of tools discovered across all servers.
        """
        total_tools = 0
        for cfg in servers:
            name = cfg.get("name", "unnamed")
            transport = cfg.get("transport", "stdio")
            try:
                if transport == "stdio":
                    count = await self._connect_stdio(name, cfg)
                elif transport == "sse":
                    count = await self._connect_sse(name, cfg)
                else:
                    logger.warning("[MCP] Unknown transport '%s' for server '%s'", transport, name)
                    continue
                total_tools += count
            except Exception as exc:
                logger.error("[MCP] Failed to connect server '%s': %s", name, exc)
        return total_tools

    async def shutdown(self) -> None:
        """Disconnect all servers and clean up processes."""
        for name, handle in self._servers.items():
            try:
                if handle.process is not None:
                    handle.process.terminate()
                    try:
                        await asyncio.wait_for(handle.process.wait(), timeout=5.0)
                    except TimeoutError:
                        handle.process.kill()
                        await handle.process.wait()
                    logger.info("[MCP] Terminated stdio process '%s'", name)

                if handle.http is not None:
                    await handle.http.aclose()
                    logger.info("[MCP] Closed SSE client '%s'", name)

                handle.connected = False
            except Exception as exc:
                logger.warning("[MCP] Error shutting down '%s': %s", name, exc)
        self._servers.clear()

    # ── Schema discovery ────────────────────────────────────────────

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-format tool schemas from all connected MCP servers.

        Each schema looks like::

            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file from the filesystem.",
                    "parameters": {"type": "object", "properties": {...}, "required": [...]}
                },
                "_mcp_server": "filesystem"  # internal routing hint
            }
        """
        schemas: list[dict[str, Any]] = []
        for handle in self._servers.values():
            if not handle.connected:
                continue
            for tool in handle.tools:
                name = tool.get("name", "")
                desc = tool.get("description", "")
                input_schema = tool.get("inputSchema", {"type": "object", "properties": {}})

                # Normalize: MCP inputSchema → OpenAI parameters
                if "type" not in input_schema:
                    input_schema["type"] = "object"

                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": desc,
                            "parameters": input_schema,
                        },
                        "_mcp_server": handle.name,  # routing hint (stripped before sending to LLM)
                    }
                )
        return schemas

    def get_tool_server_map(self) -> dict[str, str]:
        """Return a mapping of tool_name → server_name for all MCP tools."""
        mapping: dict[str, str] = {}
        for handle in self._servers.values():
            if not handle.connected:
                continue
            for tool in handle.tools:
                tool_name = tool.get("name", "")
                if tool_name:
                    mapping[tool_name] = handle.name
        return mapping

    def get_clean_schemas(self) -> list[dict[str, Any]]:
        """Return schemas with the internal ``_mcp_server`` key stripped.

        This is what gets sent to the LLM — the ``_mcp_server`` hint is
        only used internally for routing.
        """
        schemas = self.get_all_tool_schemas()
        for s in schemas:
            s.pop("_mcp_server", None)
        return schemas

    # ── Tool execution ──────────────────────────────────────────────

    async def execute_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool on a specific MCP server.

        Args:
            server_name: The MCP server name (as configured).
            tool_name: The tool name to call.
            arguments: Tool arguments.

        Returns:
            Dict with ``content`` (str) and ``is_error`` (bool).
        """
        handle = self._servers.get(server_name)
        if handle is None or not handle.connected:
            return {
                "content": f"MCP server '{server_name}' not connected.",
                "is_error": True,
            }

        start = time.monotonic()
        try:
            params: dict[str, Any] = {"name": tool_name, "arguments": arguments if arguments is not None else {}}

            result = await self._send(handle, "tools/call", params)

            duration_ms = (time.monotonic() - start) * 1000

            # Extract content from MCP result format
            content_parts: list[str] = []
            is_error = False

            if isinstance(result, dict):
                is_error = result.get("isError", False)
                for item in result.get("content", []):
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            content_parts.append(item.get("text", ""))
                        else:
                            content_parts.append(json.dumps(item, ensure_ascii=False))
                    else:
                        content_parts.append(str(item))

            content = "\n".join(content_parts) if content_parts else json.dumps(result, ensure_ascii=False)

            logger.info(
                "[MCP] Tool '%s' on '%s' → %.0fms (error=%s)",
                tool_name,
                server_name,
                duration_ms,
                is_error,
            )
            return {"content": content, "is_error": is_error}

        except MCPBridgeError as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("[MCP] Tool '%s' on '%s' failed (%.0fms): %s", tool_name, server_name, duration_ms, exc)
            return {"content": f"MCP error: {exc}", "is_error": True}

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("[MCP] Tool '%s' on '%s' crashed (%.0fms): %s", tool_name, server_name, duration_ms, exc)
            return {"content": f"Unexpected error: {exc}", "is_error": True}

    # ── Introspection ───────────────────────────────────────────────

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name belongs to any connected MCP server."""
        for handle in self._servers.values():
            if not handle.connected:
                continue
            for tool in handle.tools:
                if tool.get("name") == tool_name:
                    return True
        return False

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Return the server name that owns a tool, or None."""
        for handle in self._servers.values():
            if not handle.connected:
                continue
            for tool in handle.tools:
                if tool.get("name") == tool_name:
                    return handle.name
        return None

    def list_servers(self) -> list[dict[str, Any]]:
        """Return status info for all managed servers."""
        return [
            {
                "name": h.name,
                "transport": h.transport,
                "connected": h.connected,
                "tool_count": len(h.tools),
            }
            for h in self._servers.values()
        ]

    # ════════════════════════════════════════════════════════════════
    # Internal: stdio transport (pure asyncio)
    # ════════════════════════════════════════════════════════════════

    async def _connect_stdio(self, name: str, cfg: dict[str, Any]) -> int:
        """Spawn an MCP server as a subprocess and perform the handshake."""
        command = cfg.get("command", [])
        if not command:
            raise MCPBridgeError(f"stdio server '{name}' requires a 'command' list")

        env = {**os.environ, **cfg.get("env", {})}
        working_dir = cfg.get("working_dir")

        logger.info("[MCP] Starting stdio server '%s': %s", name, command)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=working_dir,
            )
        except FileNotFoundError as exc:
            raise MCPBridgeError(f"Command not found: {command[0]}") from exc
        except OSError as exc:
            raise MCPBridgeError(f"Failed to start process: {exc}") from exc

        handle = MCPServerHandle(
            name=name,
            transport="stdio",
            process=process,
            command=command,
        )

        # MCP handshake
        try:
            await self._send(
                handle,
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "clientInfo": {"name": "kazma-mcp-bridge", "version": "0.1.0"},
                },
            )
            await self._notify(handle, "notifications/initialized", {})
        except Exception as exc:
            process.terminate()
            raise MCPBridgeError(f"Handshake failed for '{name}': {exc}") from exc

        # Discover tools
        result = await self._send(handle, "tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        handle.tools = tools
        handle.connected = True

        self._servers[name] = handle
        logger.info("[MCP] Connected to '%s' (stdio, pid=%d, tools=%d)", name, process.pid, len(tools))
        return len(tools)

    # ════════════════════════════════════════════════════════════════
    # Internal: SSE transport
    # ════════════════════════════════════════════════════════════════

    async def _connect_sse(self, name: str, cfg: dict[str, Any]) -> int:
        """Connect to an MCP server over HTTP SSE."""
        url = cfg.get("url", "")
        if not url:
            raise MCPBridgeError(f"SSE server '{name}' requires a 'url'")

        headers = cfg.get("headers", {})
        timeout = cfg.get("timeout", 30.0)

        http = httpx.AsyncClient(
            base_url=url,
            headers=headers,
            timeout=timeout,
        )

        handle = MCPServerHandle(
            name=name,
            transport="sse",
            http=http,
            url=url,
        )

        # MCP handshake
        try:
            await self._send(
                handle,
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "clientInfo": {"name": "kazma-mcp-bridge", "version": "0.1.0"},
                },
            )
            await self._notify(handle, "notifications/initialized", {})
        except Exception as exc:
            await http.aclose()
            raise MCPBridgeError(f"Handshake failed for '{name}': {exc}") from exc

        # Discover tools
        result = await self._send(handle, "tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        handle.tools = tools
        handle.connected = True

        self._servers[name] = handle
        logger.info("[MCP] Connected to '%s' (sse, url=%s, tools=%d)", name, url, len(tools))
        return len(tools)

    # ════════════════════════════════════════════════════════════════
    # Internal: JSON-RPC transport
    # ════════════════════════════════════════════════════════════════

    async def _send(self, handle: MCPServerHandle, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a JSON-RPC request and return the result."""
        request = _jsonrpc_request(method, params)
        raw = json.dumps(request) + "\n"

        if handle.transport == "stdio":
            return await self._send_stdio(handle, raw)
        return await self._send_sse(handle, raw)

    async def _notify(self, handle: MCPServerHandle, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        raw = json.dumps(msg) + "\n"

        if handle.transport == "stdio":
            proc = handle.process
            if proc is None or proc.stdin is None:
                return
            proc.stdin.write(raw.encode())
            await proc.stdin.drain()
        elif handle.transport == "sse" and handle.http is not None:
            await handle.http.post("/notifications", content=raw, headers={"Content-Type": "application/json"})

    async def _send_stdio(self, handle: MCPServerHandle, raw: str) -> Any:
        """Send a JSON-RPC message over stdio and read the response."""
        proc = handle.process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise MCPBridgeError(f"stdio process '{handle.name}' not running")

        # Write request
        proc.stdin.write(raw.encode())
        await proc.stdin.drain()

        # Read response (one line per JSON-RPC message)
        async with handle.read_lock:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=60.0)

        if not line:
            # Check if process died
            retcode = proc.returncode
            if retcode is not None:
                stderr = ""
                if proc.stderr:
                    try:
                        stderr_bytes = await asyncio.wait_for(proc.stderr.read(4096), timeout=2.0)
                        stderr = stderr_bytes.decode(errors="replace")
                    except TimeoutError:
                        pass
                raise MCPBridgeError(f"Server '{handle.name}' exited with code {retcode}. stderr: {stderr[:500]}")
            raise MCPBridgeError(f"Server '{handle.name}' closed stdout (EOF)")

        return _jsonrpc_parse(line.decode().strip())

    async def _send_sse(self, handle: MCPServerHandle, raw: str) -> Any:
        """Send a JSON-RPC message over SSE and read the response."""
        if handle.http is None:
            raise MCPBridgeError(f"SSE client '{handle.name}' not initialized")

        resp = await handle.http.post(
            "/jsonrpc",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return _jsonrpc_parse(resp.text)


# ══════════════════════════════════════════════════════════════════════════
# UnifiedToolExecutor — single execute() for local + MCP
# ══════════════════════════════════════════════════════════════════════════


class UnifiedToolExecutor:
    """Routes tool calls to LocalToolRegistry or AsyncMCPManager.

    The LangGraph tool_worker node calls ``execute(name, args)`` and this
    class transparently dispatches to the right backend:

      1. If ``name`` is in the local registry → execute locally.
      2. If ``name`` is an MCP tool → execute via MCP.
      3. Otherwise → return an error dict.

    Usage::

        local = LocalToolRegistry(include_builtins=True)
        mcp = AsyncMCPManager()
        await mcp.connect_from_config(config["mcp"]["servers"])

        executor = UnifiedToolExecutor(local=local, mcp=mcp)

        # Single merged schema list for the LLM
        defs = executor.get_tool_definitions()

        # Transparent execution
        result = await executor.execute("file_read", {"path": "/tmp"})
        result = await executor.execute("mcp_tool_name", {"arg": "val"})
    """

    def __init__(
        self,
        local: Any = None,
        mcp: AsyncMCPManager | None = None,
    ) -> None:
        if mcp is None:
            # Always carry an MCP manager so callers can connect servers
            # after construction (e.g. KazmaAgent.connect_mcp_servers).
            mcp = AsyncMCPManager()
        self._local = local
        self._mcp = mcp

    # ── MCP server lifecycle (delegates to AsyncMCPManager) ─────────

    async def connect_server(self, server_config: dict[str, Any]) -> int:
        """Connect a single MCP server and register its tools.

        Thin compatibility shim over ``AsyncMCPManager.connect_from_config``
        so callers that previously used the old MCP-only ``ToolRegistry``
        (e.g. ``KazmaAgent.connect_mcp_servers``, the MCP settings UI)
        keep working through the unified executor.
        """
        if self._mcp is None:
            return 0
        return await self._mcp.connect_from_config([server_config])

    def list_servers(self) -> list[dict[str, Any]]:
        """Return status info for all managed MCP servers."""
        if self._mcp is None:
            return []
        return self._mcp.list_servers()

    # ── Unified schema list ─────────────────────────────────────────

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return a single merged list of OpenAI-format tool schemas.

        Local tools come first, then MCP tools.  The ``_mcp_server``
        internal key is stripped so the LLM sees a clean list.
        """
        defs: list[dict[str, Any]] = []

        # Local tools
        if self._local is not None:
            defs.extend(self._local.get_tool_definitions())

        # MCP tools (strip _mcp_server hint)
        if self._mcp is not None:
            defs.extend(self._mcp.get_clean_schemas())

        return defs

    # ── Unified execution ───────────────────────────────────────────

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by name, routing to local or MCP.

        Routing priority:
          1. Local registry (in-process, fastest)
          2. MCP manager (subprocess or SSE)

        Args:
            tool_name: The tool name as it appears in the schema.
            arguments: Tool arguments dict.

        Returns:
            Dict with ``content`` (str) and ``is_error`` (bool).
        """
        # ── Try local first ────────────────────────────────────────
        if arguments is None:
            arguments = {}
        if self._local is not None:
            local_tool = self._local.get_tool(tool_name)
            if local_tool is not None:
                logger.debug("[Unified] Routing '%s' → local", tool_name)
                return await self._local.execute(tool_name, arguments)

        # ── Try MCP ────────────────────────────────────────────────
        if self._mcp is not None and self._mcp.is_mcp_tool(tool_name):
            server_name = self._mcp.get_server_for_tool(tool_name)
            if server_name:
                logger.debug("[Unified] Routing '%s' → MCP server '%s'", tool_name, server_name)
                return await self._mcp.execute_mcp_tool(server_name, tool_name, arguments)

        # ── Not found ──────────────────────────────────────────────
        available_local = []
        available_mcp = []
        if self._local is not None:
            available_local = [t["name"] for t in self._local.list_tools()]
        if self._mcp is not None:
            available_mcp = list(self._mcp.get_tool_server_map().keys())

        all_available = available_local + available_mcp
        return {
            "content": f"Tool '{tool_name}' not found. Available: {all_available[:20]}",
            "is_error": True,
        }

    # ── Introspection ───────────────────────────────────────────────

    def list_all_tools(self) -> list[dict[str, str]]:
        """List all tools from both backends."""
        tools: list[dict[str, str]] = []
        if self._local is not None:
            for t in self._local.list_tools():
                tools.append({**t, "backend": "local"})
        if self._mcp is not None:
            for server in self._mcp.list_servers():
                for tool_name in self._mcp.get_tool_server_map():
                    if self._mcp.get_server_for_tool(tool_name) == server["name"]:
                        tools.append(
                            {
                                "name": tool_name,
                                "description": "",
                                "category": "mcp",
                                "backend": f"mcp:{server['name']}",
                            }
                        )
        return tools

    @property
    def connected(self) -> bool:
        """True if at least one backend has tools."""
        has_local = self._local is not None and self._local.tool_count > 0
        has_mcp = self._mcp is not None and any(s["connected"] for s in self._mcp.list_servers())
        return has_local or has_mcp

    @property
    def tool_count(self) -> int:
        """Total number of tools across both backends."""
        count = 0
        if self._local is not None:
            count += self._local.tool_count
        if self._mcp is not None:
            count += len(self._mcp.get_tool_server_map())
        return count

    def list_tools(self) -> list[dict[str, str]]:
        """Return a merged summary of every tool (local + MCP).

        Each entry has ``name``, ``description``, ``category`` and ``server``
        keys so callers (e.g. the MCP settings UI) can render the full tool
        inventory through this single public method.
        """
        tools: list[dict[str, str]] = []
        if self._local is not None:
            for t in self._local.list_tools():
                entry = dict(t)
                entry.setdefault("category", "local")
                entry["server"] = "local"
                tools.append(entry)
        if self._mcp is not None:
            for tool_name, server_name in self._mcp.get_tool_server_map().items():
                description = ""
                for handle in self._mcp._servers.values():
                    if handle.name != server_name:
                        continue
                    for tool in handle.tools:
                        if tool.get("name") == tool_name:
                            description = tool.get("description", "")
                            break
                    break
                tools.append(
                    {
                        "name": tool_name,
                        "description": description[:120],
                        "category": "mcp",
                        "server": server_name,
                    }
                )
        return tools

    def get_mcp_tools_for_server(self, server_name: str) -> list[dict[str, str]]:
        """Return ``[{name, description}]`` for the tools of a specific MCP server.

        Replaces the previous direct ``agent.tools._tools.values()`` access
        from the UI layer.
        """
        tools: list[dict[str, str]] = []
        if self._mcp is None:
            return tools
        for handle in self._mcp._servers.values():
            if handle.name != server_name:
                continue
            for tool in handle.tools:
                tools.append(
                    {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                    }
                )
        return tools

    def is_server_connected(self, name: str) -> bool:
        """Return True if an MCP server with ``name`` is currently connected."""
        if self._mcp is None:
            return False
        return any(s["name"] == name and s["connected"] for s in self._mcp.list_servers())

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect a single MCP server by name.

        Returns True if a server was disconnected.
        """
        if self._mcp is None:
            return False
        handle = self._mcp._servers.pop(name, None)
        if handle is None:
            return False
        try:
            if handle.process is not None:
                handle.process.terminate()
                try:
                    import asyncio as _asyncio

                    await _asyncio.wait_for(handle.process.wait(), timeout=5.0)
                except TimeoutError:
                    handle.process.kill()
                    await handle.process.wait()
            if handle.http is not None:
                await handle.http.aclose()
            handle.connected = False
            return True
        except Exception as exc:
            logger.warning("[Unified] Error disconnecting server '%s': %s", name, exc)
            return False

    async def disconnect_all(self) -> None:
        """Shutdown all backends."""
        if self._mcp is not None:
            await self._mcp.shutdown()
        # Local registry needs no cleanup
