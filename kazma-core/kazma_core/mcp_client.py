"""MCP Client — ephemeral one-off connections for MCP server diagnostics.

Supports both stdio and SSE transports per the Model Context Protocol
(JSON-RPC 2.0). Each MCPClient instance manages a single server connection.

**Role note (audit M33 — do not "unify" away):** the production tool path
runs through :mod:`kazma_core.mcp.manager` (``AsyncMCPManager`` — registry,
reconnect, line limits). This client exists deliberately as a SECOND
implementation for the Settings "Test connection" flow
(``mcp_ui.api_test_server`` / ``api_test_config``): it spins up a throwaway
subprocess, captures the server's **stderr** (surfaced in the UI so a 0-tool
server explains itself), and disconnects without touching the live manager
registry. The manager does not expose subprocess stderr — migrating this
path onto it would regress that diagnostics feature. If you change the
stdio protocol handling here, mirror the change in ``mcp/manager.py`` (and
vice versa; see ``KAZMA_MCP_STDIO_LIMIT`` there).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, replace
from typing import Any

_STDIO_BYTES = 16 * 1024 * 1024

import httpx

from kazma_core.http_tls import shared_ssl_context
from kazma_core.mcp.child_env import mcp_child_env
from kazma_core.mcp.secrets import MCPSecretUnavailable, redacted_argv, resolve, secret_values

__all__ = ["MCPClient", "MCPConnectionError", "MCPError", "MCPServerConfig"]

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
    # 90s default — long enough for npx cold starts that fetch the package
    # on first invocation (can take 30-60s on slow networks). Override per
    # server via the ``timeout`` field, or globally via KAZMA_MCP_TIMEOUT_MS.
    timeout: float = 90.0
    # auth: first-class auth config for SSE servers.
    # {"type": "bearer", "token": "..."} → Authorization: Bearer {token}
    # {"type": "header", "name": "X-API-Key", "value": "..."} → custom header
    auth: dict[str, str] = field(default_factory=dict)
    # trust level: "trusted" (no HITL), "approval_required" (HITL for danger tools),
    # "sandboxed" (future — restricted). Defaults to "approval_required".
    trust: str = "approval_required"


def _with_secrets(cfg: MCPServerConfig) -> MCPServerConfig:
    """*cfg* with its vault pointers replaced by the secrets (kazma_core.mcp.secrets).

    Test reads the stored configuration, which holds pointers; the manager's
    transports resolve the same way (``AsyncMCPManager._with_secrets``).
    """
    view = {"name": cfg.name, "env": cfg.env, "headers": cfg.headers, "auth": cfg.auth, "url": cfg.url}
    try:
        done = resolve(view)
    except MCPSecretUnavailable as exc:
        raise MCPConnectionError(str(exc)) from exc
    if done is view:
        return cfg
    return replace(cfg, env=done["env"], headers=done["headers"], auth=done["auth"], url=done["url"])


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
        self._process_tree: Any = None
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
        cfg = _with_secrets(cfg)

        self._config = cfg

        try:
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
        except asyncio.CancelledError:
            await self.disconnect()
            raise
        except Exception:
            # A test/start failure must never leave a child process or HTTP
            # connection alive for the caller to discover and clean up.
            await self.disconnect()
            raise

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
            if self._process_tree is not None:
                self._process_tree.close()
            try:
                self._process.stdin.close()  # type: ignore[union-attr]
            except Exception as exc:
                logger.debug("Failed to close MCP stdin: %s", exc)
            try:
                self._process.terminate()
                await asyncio.to_thread(self._process.wait, timeout=5)
            except Exception as exc:
                logger.debug("MCP terminate failed: %s, trying kill", exc)
                try:
                    self._process.kill()
                    await asyncio.to_thread(self._process.wait, timeout=5)
                except Exception as kill_exc:
                    logger.warning("Failed to kill MCP process: %s", kill_exc)
            self._process = None
            self._process_tree = None

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

        # The manager's rule (audit H-4): the allowlisted basics plus the
        # server's own env and auth, never Kazma's whole environment. This
        # line was ``{**os.environ, **cfg.env}`` until 2026-09-30, so Test
        # handed a server being tried out the vault key and every API key.
        env = mcp_child_env(cfg.name, {"env": cfg.env, "auth": cfg.auth})

        command = list(cfg.command)
        # Windows fix: subprocess.Popen does NOT resolve .cmd/.bat shim
        # extensions (e.g. npx → npx.cmd), raising FileNotFoundError even
        # though the tool is on PATH. Resolve via shutil.which first so the
        # full path (including the extension) is passed. Mirrors the fix in
        # mcp/manager.py; no-op on Linux/macOS.
        if sys.platform == "win32":
            resolved = shutil.which(command[0], path=env.get("PATH"))
            if resolved:
                command[0] = resolved

        try:
            # Off the event loop: starting a process blocks (AGENTS.md §23).
            from kazma_core.security.process_budget import start_process_async

            self._process, self._process_tree = await start_process_async(
                command, cwd=cfg.working_dir, env=env,
            )
        except FileNotFoundError as exc:
            raise MCPConnectionError(f"Command not found: {cfg.command[0]}") from exc
        except OSError as exc:
            raise MCPConnectionError(f"Failed to start process: {exc}") from exc

        logger.debug(
            "Spawned stdio process: pid=%s cmd=%s",
            self._process.pid,
            redacted_argv(command, secret_values({"env": cfg.env, "auth": cfg.auth})),
        )

    async def _connect_sse(self, cfg: MCPServerConfig) -> None:
        if not cfg.url:
            raise MCPConnectionError("SSE transport requires a URL")

        # Merge first-class auth into headers (parity with mcp/manager.py)
        headers = dict(cfg.headers or {})
        auth = cfg.auth or {}
        if auth.get("type") == "bearer" and auth.get("token"):
            headers["Authorization"] = "Bearer " + str(auth["token"])
        elif auth.get("type") == "header" and auth.get("name") and auth.get("value") is not None:
            headers[str(auth["name"])] = str(auth["value"])

        self._http = httpx.AsyncClient(
            base_url=cfg.url,
            headers=headers,
            timeout=cfg.timeout,
            verify=shared_ssl_context(),
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
            if len(raw.encode()) > _STDIO_BYTES:
                raise MCPConnectionError("MCP notification exceeds its byte budget")
            try:
                async with asyncio.timeout(self._config.timeout):
                    await asyncio.to_thread(proc.stdin.write, raw.encode())
                async with asyncio.timeout(self._config.timeout):
                    await asyncio.to_thread(proc.stdin.flush)
            except TimeoutError as exc:
                self._connected = False
                raise MCPConnectionError(
                    f"stdio notification to '{self.server_name}' timed out after "
                    f"{self._config.timeout:g}s"
                ) from exc
        elif self._config.transport == "sse" and self._http is not None:
            await self._http.post("/notifications", content=raw, headers={"Content-Type": "application/json"})

    async def _send_stdio(self, raw: str) -> Any:
        proc = self._process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise MCPConnectionError("stdio process not running")
        if len(raw.encode()) > _STDIO_BYTES:
            raise MCPConnectionError("MCP request exceeds its byte budget")

        # stdio responses have no background JSON-RPC dispatcher here, so
        # serialize a complete write/read transaction to preserve response
        # ownership when callers execute tools concurrently.
        async with self._read_lock:
            try:
                # Match the manager: Python 3.11 wait_for can consume caller
                # cancellation when a worker completes in the same tick.
                timeout = self._config.timeout if self._config else 90.0
                async with asyncio.timeout(timeout):
                    await asyncio.to_thread(proc.stdin.write, raw.encode())
                async with asyncio.timeout(timeout):
                    await asyncio.to_thread(proc.stdin.flush)
                async with asyncio.timeout(timeout):
                    line = await asyncio.to_thread(proc.stdout.readline, _STDIO_BYTES + 1)
            except TimeoutError as exc:
                self._connected = False
                timeout = self._config.timeout if self._config else 90.0
                raise MCPConnectionError(
                    f"stdio request to '{self.server_name}' timed out after {timeout:g}s"
                ) from exc

        if len(line) > _STDIO_BYTES:
            raise MCPConnectionError("MCP response exceeds its byte budget")
        if not line:
            # The subprocess exited before answering. Read whatever it wrote
            # to stderr so operators get the *real* cause (bad arg, missing
            # dir, npm error) instead of an opaque EOF.
            stderr_hint = ""
            proc = self._process
            if proc is not None and proc.stderr is not None:
                try:
                    read = getattr(proc.stderr, "read1", proc.stderr.read)
                    stderr_hint = (await asyncio.wait_for(asyncio.to_thread(read, 4096), timeout=0.5)).decode(errors="replace").strip()
                except (TimeoutError, OSError):
                    stderr_hint = ""
            detail = f": {stderr_hint}" if stderr_hint else " (no stderr output)"
            raise MCPConnectionError(f"Server closed stdout (EOF){detail}")

        return _jsonrpc_response(line.decode().strip())

    async def _send_sse(self, raw: str, method: str) -> Any:
        if self._http is None:
            raise MCPConnectionError("SSE client not initialised")

        try:
            resp = await self._http.post(
                "/jsonrpc",
                content=raw,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return _jsonrpc_response(resp.text)
        except httpx.HTTPError as exc:
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    detail = exc.response.text.strip().replace("\n", " ")[:400]
                except Exception:
                    pass
            suffix = f" — response: {detail}" if detail else ""
            raise MCPConnectionError(f"SSE request failed: {exc}{suffix}") from exc

    # -- helpers -----------------------------------------------------------

    def _assert_connected(self) -> None:
        if not self._connected:
            raise MCPError("Not connected to any MCP server")
