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
        command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "${KAZMA_ACTIVE_WORKSPACE}"]
      - name: "web-search"
        transport: "sse"
        url: "http://localhost:8080/sse"
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from kazma_core.chaos import InjectionTarget, chaos_injection

__all__ = [
    "AsyncMCPManager",
    "MCPBridgeError",
    "MCPServerHandle",
    "UnifiedToolExecutor",
    "classify_mcp_tool",
    "get_active_mcp_manager",
    "set_active_mcp_manager",
]

logger = logging.getLogger(__name__)

_active_mcp_manager: AsyncMCPManager | None = None


def get_active_mcp_manager() -> AsyncMCPManager | None:
    """Process-wide manager set by :class:`UnifiedToolExecutor`."""
    return _active_mcp_manager


def set_active_mcp_manager(manager: AsyncMCPManager | None) -> None:
    global _active_mcp_manager
    _active_mcp_manager = manager

# ══════════════════════════════════════════════════════════════════════════
# MCP tool classification — danger-tier detection by name pattern
# ══════════════════════════════════════════════════════════════════════════

# Keywords that indicate an MCP tool is danger-tier (requires HITL approval).
# MCP tool names are runtime-discovered and cannot be in a static set like
# local tools, so we classify by name pattern instead.
_DANGER_KEYWORDS = (
    "write", "delete", "remove", "exec", "run", "shell", "bash",
    "command", "kill", "terminate", "install", "deploy", "upload",
    "download", "fetch", "request", "post", "put", "patch",
    "replace", "rename", "create", "update", "modify", "alter",
    "truncate", "drop", "grant", "revoke", "clear", "purge",
    "wipe", "overwrite", "reset",
    # Secret-exfil patterns (must not be treated as safe "get_*")
    "secret", "password", "passwd", "credential", "token", "apikey",
    "api_key", "private_key", "privatekey", "auth", "vault",
)

# Keywords that indicate a safe read-only tool (never requires approval).
_SAFE_KEYWORDS = (
    "read", "list", "search", "get", "info", "status", "check",
    "describe", "query", "count", "exists", "help", "tree",
    "directory_tree", "file_tree", "find", "show", "cat", "view",
)

# Even with a "safe" verb, these substrings force danger (audit H6)
_SENSITIVE_READ_KEYWORDS = (
    "secret", "password", "passwd", "credential", "token", "apikey",
    "api_key", "private", "vault", "auth", "cookie", "session_key",
)

# Canonical mutator vocabulary (audit fix — MED-HIGH): mirrored from
# ``kazma_core.safety.side_effects._MUTATOR_TOKENS`` so MCP classification and
# the commitment side-effect registry agree on what a mutator looks like.
# Missing verbs here let safe-token blends like ``mcp__kv__query_set``
# classify SAFE despite being mutators. Live-imported when available; this
# mirror is the import-degradation fallback.
_MUTATOR_TOKENS_FALLBACK = frozenset({
    "write", "save", "delete", "remove", "drop", "exec", "run", "spawn",
    "send", "schedule", "cancel", "config", "install", "deploy", "update",
    "create", "merge", "push", "pull", "commit", "set", "put", "post",
    "apply", "grant", "revoke", "reset", "clear", "wipe", "override",
})


def _mutator_tokens() -> frozenset[str]:
    """Return the canonical mutator vocabulary (lazy import — no cycle)."""
    try:
        from kazma_core.safety.side_effects import _MUTATOR_TOKENS

        return _MUTATOR_TOKENS
    except Exception:
        return _MUTATOR_TOKENS_FALLBACK


def _mcp_raw_tool_name(tool_name: str) -> str:
    """Strip ``mcp__{server}__`` namespace so classification ignores server slugs.

    Namespaced names like ``mcp__get_status_svc__frobnicate`` used to match
    safe keywords in the *server* segment (``get``, ``status``) and skip
    HITL in non-prod. Always classify the raw tool leaf only.
    """
    name = (tool_name or "").strip()
    if name.lower().startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2]
    return name


_FS_PATH_KEYS = ("path", "paths", "directory", "filePath", "filepath",
                  "source", "destination", "src", "dst", "target", "folder")
# Filesystem verbs that are writes but not always in `_MUTATOR_TOKENS`
# (append/patch/edit/unix short names). Unioned with the side_effects SoT.
_FS_WRITE_EXTRA = frozenset({
    "edit", "move", "rename", "mkdir", "rm", "rmdir", "touch", "copy", "cp",
    "append", "patch",
})


def _write_keywords() -> frozenset[str]:
    """Path-access write vocabulary — mutator SoT plus filesystem extras."""
    return frozenset(_mutator_tokens()) | _FS_WRITE_EXTRA


def _mcp_path_mode(raw_tool_name: str) -> str:
    """Return ``write`` or ``read`` for MCP path-grant checks (M-14)."""
    name_lower = (raw_tool_name or "").lower()
    tokens = set(re.split(r"[^a-z0-9]+", name_lower)) - {""}
    write_vocab = _write_keywords()
    if tokens & write_vocab:
        return "write"
    if any(kw in name_lower for kw in ("rm", "cp", "mkdir", "rmdir")):
        return "write"
    return "read"


def _resource_path_from_uri(uri: str) -> str | None:
    """Return a filesystem path for file: / path-shaped resource URIs."""
    u = (uri or "").strip()
    if not u:
        return None
    if u.lower().startswith("file:"):
        try:
            from urllib.parse import unquote, urlparse

            parsed = urlparse(u)
            path = unquote(parsed.path or "")
            if parsed.netloc and parsed.netloc not in (".", "localhost"):
                # file://host/path — keep host as Windows drive if one letter
                if len(parsed.netloc) == 1:
                    path = f"{parsed.netloc}:{path}"
            return path or None
        except Exception:
            return u[5:].lstrip("/") or None
    if u.startswith("/") or (len(u) > 2 and u[1] == ":"):
        return u
    return None


def _gate_mcp_path_access(
    raw_tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """Gate MCP calls that carry path-like arguments through check_path_access.

    Returns a denial dict (with ``is_error=True``) if any path is outside the
    workspace and no grant covers it, or ``None`` to allow the call. This closes
    the hole where MCP ``filesystem`` tools bypassed the path-grant system
    entirely (their own allowlist was the only gate).
    """
    # Collect path-like arguments.
    paths: list[str] = []
    for key in _FS_PATH_KEYS:
        val = arguments.get(key)
        if val is None:
            continue
        items = val if isinstance(val, list) else [val]
        paths.extend(str(v) for v in items if isinstance(v, (str, int)) and str(v).strip())
    if not paths:
        return None  # no path args → not a filesystem op

    mode = _mcp_path_mode(raw_tool_name)

    try:
        from kazma_core.safety.hitl import get_current_thread_id
        from kazma_core.workspace.path_policy import check_path_access
    except ImportError:
        # Fail-closed: this gate is the ONLY filesystem-containment check for
        # MCP tools. If the path-policy module cannot be imported (broken/partial
        # install, import cycle), deny rather than allow out-of-workspace access —
        # matches the fail-closed posture of tool_registry / vision_analyze /
        # ShellTool, which all deny when the workspace module is unavailable.
        return {
            "content": (
                "Access denied: workspace safety module unavailable — "
                "MCP path access blocked (fail-closed)."
            ),
            "is_error": True,
            "outcome": "hard",
        }

    thread_id = get_current_thread_id() or ""
    for p in paths:
        result = check_path_access(p, mode, thread_id=thread_id)
        if not result.allowed:
            logger.warning(
                "[MCP] Path access denied for '%s': %s (%s)",
                raw_tool_name, p, result.reason,
            )
            return {
                "content": (
                    f"Access denied: '{p}' is outside the workspace and no "
                    f"grant covers it ({result.reason})."
                ),
                "is_error": True,
                "outcome": "hard",
            }
    return None


def classify_mcp_tool(tool_name: str) -> str:
    """Classify an MCP tool by name pattern.

    Returns:
        "danger" — tool name contains a danger keyword (write/exec/delete/etc.)
        "safe"   — tool name contains only safe keywords (read/list/get/etc.)
        "unknown" — neither pattern matches (treat as danger by default for safety)

    Classification uses the **raw** tool name (after stripping
    ``mcp__server__`` prefix) so server slugs cannot bleach unknown tools.
    """
    name_lower = _mcp_raw_tool_name(tool_name).lower()
    tokens = set(re.split(r"[^a-z0-9]+", name_lower))
    # Danger wins on ANY danger keyword, sensitive-read token, or canonical
    # mutator token (audit fix): a mutator verb blended with a safe noun
    # (``query_set``, ``kv_save``) must not bleach to safe. Mutators match
    # whole tokens; keyword scans stay substring-based as before.
    has_danger = (
        any(kw in name_lower for kw in _DANGER_KEYWORDS)
        or any(kw in name_lower for kw in _SENSITIVE_READ_KEYWORDS)
        or bool(tokens & set(_mutator_tokens()))
    )
    if has_danger:
        return "danger"
    # Safe verbs need whole token matches.  A substring match would classify
    # arbitrary names such as ``frobnicate`` (contains ``cat``) as safe and
    # bypass the default-unknown HITL gate.
    has_safe = any(kw in tokens for kw in _SAFE_KEYWORDS)
    if has_safe:
        return "safe"
    return "unknown"


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
    transport: str  # "stdio" | "sse" | "streamable_http"
    # stdio
    process: asyncio.subprocess.Process | None = None
    command: list[str] = field(default_factory=list)
    # sse / streamable_http
    http: httpx.AsyncClient | None = None
    url: str = ""
    # streamable_http only — session id returned by the server, sent back on
    # subsequent requests via the ``Mcp-Session-Id`` header.
    session_id: str = ""
    # shared
    tools: list[dict[str, Any]] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    prompts: list[dict[str, Any]] = field(default_factory=list)
    connected: bool = False
    read_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timeout: float = 60.0
    # trust level: "trusted" (no HITL), "approval_required" (HITL for danger tools)
    trust: str = "approval_required"


# ══════════════════════════════════════════════════════════════════════════
# Windows selector-loop stdio adapter
# ══════════════════════════════════════════════════════════════════════════


class _SyncWriterAdapter:
    """Expose the ``write``/``drain`` pair the bridge uses on a blocking pipe."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, data: bytes) -> None:
        self._stream.write(data)

    async def drain(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._stream.flush)


class _SyncReaderAdapter:
    """Async ``readline``/``read`` over a blocking pipe via executor threads."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    # _drain_stderr probes for read1 (BufferedReader) — delegate synchronously.
    def read1(self, n: int) -> bytes:
        if hasattr(self._stream, "read1"):
            return self._stream.read1(n)
        return self._stream.read(n)

    async def readline(self) -> bytes:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._stream.readline)

    async def read(self, n: int = -1) -> bytes:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._stream.read, n)


class _SyncProcessAdapter:
    """Adapt a blocking ``subprocess.Popen`` to the async stdio interface.

    Kazma forces ``WindowsSelectorEventLoopPolicy`` (psycopg compat), and a
    SelectorEventLoop cannot host asyncio subprocesses on Windows —
    ``asyncio.create_subprocess_exec`` raises ``NotImplementedError``. This
    adapter runs the same JSON-RPC-over-pipes protocol on blocking pipes via
    executor threads, the transport shape ``MCPClient`` already uses, so the
    stdio path works on every event-loop policy.
    """

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc
        self.stdin = _SyncWriterAdapter(proc.stdin)
        self.stdout = _SyncReaderAdapter(proc.stdout)
        self.stderr = _SyncReaderAdapter(proc.stderr)

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def returncode(self) -> int | None:
        return self._proc.poll()

    def terminate(self) -> None:
        self._proc.terminate()

    def kill(self) -> None:
        self._proc.kill()

    async def wait(self) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._proc.wait)


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

    _MAX_SCOPED = 4

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerHandle] = {}
        # Connection setup deliberately remains best-effort for application
        # startup.  Retain failures so strict callers (for example, a
        # connection test) can report the actual reason instead of "0 tools".
        self._connection_errors: dict[str, str] = {}
        # MCP OAuth: WWW-Authenticate Bearer challenges captured from 401
        # handshake failures, keyed by server name. The UI reads these to
        # offer an OAuth login button.
        self._oauth_challenges: dict[str, str] = {}
        # Original configs (pre-scoped) so a per-task workspace can spawn
        # a clone instead of fail-closing on a root mismatch.
        self._server_templates: dict[str, dict[str, Any]] = {}
        # LRU of (server_name, resolved_root) → handle. Not listed in
        # list_servers(); execute_mcp_tool routes to them internally.
        self._scoped: OrderedDict[tuple[str, str], MCPServerHandle] = OrderedDict()

    def oauth_challenge(self, name: str) -> str | None:
        """Return the captured OAuth challenge header for *name*, if any."""
        return self._oauth_challenges.get(name)

    # ── Lifecycle ───────────────────────────────────────────────────

    @property
    def connection_errors(self) -> dict[str, str]:
        """Return failures from the most recent :meth:`connect_from_config` call."""
        return dict(self._connection_errors)

    async def connect_from_config(
        self,
        servers: list[dict[str, Any]],
        *,
        raise_on_error: bool = False,
    ) -> int:
        """Connect to all servers from a config list.

        Args:
            servers: List of dicts, each with at least ``name`` and ``transport``.

        Returns:
            Total number of tools discovered across all servers.
        """
        total_tools = 0
        self._connection_errors.clear()
        for cfg in servers:
            if not isinstance(cfg, dict):
                name = "unnamed"
                message = "server configuration must be an object"
                self._connection_errors[name] = message
                logger.error("[MCP] Failed to connect server '%s': %s", name, message)
                continue

            name = str(cfg.get("name") or "unnamed")
            if "__scoped_" not in name:
                self._server_templates[name] = dict(cfg)
            transport = cfg.get("transport", "stdio")
            try:
                # A repeated startup/start request must not orphan the prior
                # child process or HTTP client.  A healthy handle is already
                # serving the configured tools, so report it idempotently.
                existing = self._servers.get(name)
                if existing is not None:
                    self.list_servers()
                    if existing.connected:
                        total_tools += len(existing.tools)
                        continue
                    await self.disconnect_server(name)

                if transport == "stdio":
                    count = await self._connect_stdio(name, cfg)
                elif transport == "sse":
                    count = await self._connect_sse(name, cfg)
                elif transport in ("streamable_http", "streamable-http", "http"):
                    count = await self._connect_streamable_http(name, cfg)
                else:
                    logger.warning("[MCP] Unknown transport '%s' for server '%s'", transport, name)
                    self._connection_errors[name] = f"unsupported transport '{transport}'"
                    continue
                total_tools += count
                self._connection_errors.pop(name, None)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                self._connection_errors[name] = message
                logger.error("[MCP] Failed to connect server '%s': %s", name, message)
        # End-of-batch summary (audit M5): per-server errors were already
        # logged at ERROR, but with many servers the operator had to grep the
        # log to know which subset failed. One summary line closes the loop.
        if self._connection_errors:
            logger.warning(
                "[MCP] %d server(s) failed to connect: %s — tools from these "
                "servers are unavailable until fixed (see /mcp settings page)",
                len(self._connection_errors),
                "; ".join(f"{n} ({m[:80]})" for n, m in self._connection_errors.items()),
            )
            # 60 failures across eight days, never surfaced anywhere the
            # operator would see. Tools silently vanish from the agent's
            # repertoire and it plans around capabilities it no longer has.
            try:
                from kazma_core.observability.ops_alerts import alert

                alert(
                    "mcp.servers_unavailable",
                    f"{len(self._connection_errors)} MCP server(s) unavailable "
                    f"— their tools are gone until fixed.",
                    "; ".join(
                        f"{n}: {m[:60]}"
                        for n, m in list(self._connection_errors.items())[:5]
                    ),
                    severity="warn",
                )
            except Exception:
                pass
        if raise_on_error and self._connection_errors:
            details = "; ".join(
                f"{name}: {message}" for name, message in self._connection_errors.items()
            )
            raise MCPBridgeError(f"MCP connection failed: {details}")
        return total_tools

    async def shutdown(self) -> None:
        """Disconnect all servers and clean up processes."""
        for name in list(self._servers):
            await self.disconnect_server(name)
        for key, handle in list(self._scoped.items()):
            try:
                await self._close_handle(handle)
            except Exception:
                logger.debug("[MCP] scoped shutdown %s failed", key, exc_info=True)
        self._scoped.clear()

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect one server, safely handling an already-dead transport."""
        handle = self._servers.pop(name, None)
        if handle is None:
            return False
        await self._close_handle(handle)
        return True

    async def _close_handle(self, handle: MCPServerHandle) -> None:
        """Release a subprocess/HTTP client exactly once, without masking cleanup."""
        process = handle.process
        if process is not None:
            try:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5.0)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                logger.info("[MCP] Terminated stdio process '%s'", handle.name)
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.warning("[MCP] Error closing stdio server '%s': %s", handle.name, exc)
            finally:
                handle.process = None

        http = handle.http
        if http is not None:
            try:
                await http.aclose()
                logger.info("[MCP] Closed HTTP client '%s'", handle.name)
            except Exception as exc:
                logger.warning("[MCP] Error closing HTTP client '%s': %s", handle.name, exc)
            finally:
                handle.http = None

        handle.connected = False

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
                if not isinstance(tool, dict):
                    logger.warning(
                        "[MCP] Ignoring malformed tool descriptor from '%s'", handle.name
                    )
                    continue
                raw_name = tool.get("name", "")
                desc = tool.get("description", "")
                input_schema = tool.get("inputSchema")
                if not isinstance(input_schema, dict):
                    input_schema = {"type": "object", "properties": {}}
                else:
                    input_schema = dict(input_schema)

                # Normalize: MCP inputSchema → OpenAI parameters
                if "type" not in input_schema:
                    input_schema["type"] = "object"

                # NAMESPACE the tool name as mcp__<server>__<tool> so it can
                # never collide with a built-in tool (e.g. Playwright MCP's
                # 'browser_click' vs the browser_automation skill's
                # 'browser_click'). Without this, providers that require
                # unique tool names (DeepSeek, OpenAI) reject the whole
                # request with HTTP 400 'Tool names must be unique' and the
                # agent loses ALL tools for the turn (the
                # 'agent-stopped-talking' / raw-markup-in-reply symptom).
                namespaced = f"mcp__{handle.name}__{raw_name}" if raw_name else raw_name

                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": namespaced,
                            "description": desc,
                            "parameters": input_schema,
                        },
                        "_mcp_server": handle.name,        # routing hint (stripped before sending to LLM)
                        "_mcp_raw_name": raw_name,          # original name for execution routing
                    }
                )
        return schemas

    def get_tool_server_map(self) -> dict[str, str]:
        """Return a mapping of namespaced_tool_name → server_name.

        Keys are the ``mcp__<server>__<tool>`` form the LLM sees (matching
        :meth:`get_all_tool_schemas`), so execute_mcp_tool can route a
        model-emitted call back to the right server.
        """
        mapping: dict[str, str] = {}
        for handle in self._servers.values():
            if not handle.connected:
                continue
            for tool in handle.tools:
                if not isinstance(tool, dict):
                    continue
                raw_name = tool.get("name", "")
                if raw_name:
                    namespaced = f"mcp__{handle.name}__{raw_name}"
                    mapping[namespaced] = handle.name
        return mapping

    def get_clean_schemas(self) -> list[dict[str, Any]]:
        """Return schemas with internal routing keys stripped.

        This is what gets sent to the LLM — the ``_mcp_server`` and
        ``_mcp_raw_name`` hints are only used internally for routing.
        The ``function.name`` stays in its namespaced ``mcp__<server>__<tool>``
        form so it's unique across all servers + built-in tools.
        """
        schemas = self.get_all_tool_schemas()
        for s in schemas:
            s.pop("_mcp_server", None)
            s.pop("_mcp_raw_name", None)
        return schemas

    # ── Spec surfaces (resources / prompts / sampling / roots) ──────

    async def list_resources(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """``resources/list`` on one or all connected servers. Best-effort."""
        out: list[dict[str, Any]] = []
        names = [server_name] if server_name else list(self._servers)
        for name in names:
            if not name:
                continue
            handle = self._servers.get(name)
            if handle is None or not handle.connected:
                continue
            routed, scope_err = await self._route_workspace_scope(name, handle)
            if scope_err is not None:
                logger.debug("[MCP] resources/list skipped: %s", scope_err.get("content"))
                continue
            handle = routed
            try:
                result = await self._send(handle, "resources/list", {})
                resources = result.get("resources") if isinstance(result, dict) else []
                if not isinstance(resources, list):
                    resources = []
                handle.resources = resources
                for item in resources:
                    if isinstance(item, dict):
                        row = dict(item)
                        row["_mcp_server"] = name
                        out.append(row)
            except Exception as exc:
                logger.debug("[MCP] resources/list %s: %s", name, exc)
        return out

    async def read_resource(self, server_name: str, uri: str) -> dict[str, Any]:
        """``resources/read`` — body is prompt-fenced untrusted data."""
        from kazma_core.mcp.spec_client import extract_resource_text, fence_resource

        handle = self._servers.get(server_name)
        if handle is None or not handle.connected:
            return {
                "content": f"MCP server '{server_name}' not connected.",
                "is_error": True,
            }
        routed, scope_err = await self._route_workspace_scope(server_name, handle)
        if scope_err is not None:
            return scope_err
        handle = routed
        target = (uri or "").strip()
        if not target:
            return {"content": "uri is required", "is_error": True}
        path_arg = _resource_path_from_uri(target)
        if path_arg:
            denial = _gate_mcp_path_access("resources_read", {"path": path_arg})
            if denial is not None:
                return denial
        try:
            result = await self._send(handle, "resources/read", {"uri": target})
        except Exception as exc:
            return {"content": str(exc), "is_error": True}
        text = extract_resource_text(result)
        return {
            "content": fence_resource(text, server=server_name, uri=target),
            "is_error": False,
        }

    async def list_prompts(self, server_name: str | None = None) -> list[dict[str, Any]]:
        """``prompts/list`` on one or all connected servers. Best-effort."""
        out: list[dict[str, Any]] = []
        names = [server_name] if server_name else list(self._servers)
        for name in names:
            if not name:
                continue
            handle = self._servers.get(name)
            if handle is None or not handle.connected:
                continue
            try:
                result = await self._send(handle, "prompts/list", {})
                prompts = result.get("prompts") if isinstance(result, dict) else []
                if not isinstance(prompts, list):
                    prompts = []
                handle.prompts = prompts
                for item in prompts:
                    if isinstance(item, dict):
                        row = dict(item)
                        row["_mcp_server"] = name
                        out.append(row)
            except Exception as exc:
                logger.debug("[MCP] prompts/list %s: %s", name, exc)
        return out

    async def get_prompt(
        self,
        server_name: str,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``prompts/get`` — returned as user-visible text, not system inject."""
        handle = self._servers.get(server_name)
        if handle is None or not handle.connected:
            return {
                "content": f"MCP server '{server_name}' not connected.",
                "is_error": True,
            }
        prompt_name = (name or "").strip()
        if not prompt_name:
            return {"content": "prompt name is required", "is_error": True}
        params: dict[str, Any] = {"name": prompt_name}
        if arguments:
            params["arguments"] = arguments
        try:
            result = await self._send(handle, "prompts/get", params)
        except Exception as exc:
            return {"content": str(exc), "is_error": True}
        messages = result.get("messages") if isinstance(result, dict) else None
        lines: list[str] = [f"MCP prompt {server_name}/{prompt_name} (user-visible, not instructions):"]
        if isinstance(messages, list):
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role") or "user"
                content = msg.get("content")
                if isinstance(content, dict):
                    content = content.get("text") or str(content)
                lines.append(f"[{role}] {content}")
        elif isinstance(result, dict) and result.get("description"):
            lines.append(str(result["description"]))
        else:
            lines.append(str(result))
        return {"content": "\n".join(lines), "is_error": False, "messages": messages}

    async def _reply_server_request(
        self, handle: MCPServerHandle, data: dict[str, Any]
    ) -> str:
        from kazma_core.mcp.spec_client import handle_mcp_server_request, jsonrpc_reply

        method = str(data.get("method") or "")
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        result, error = await handle_mcp_server_request(
            method, params, server_name=handle.name
        )
        return jsonrpc_reply(data.get("id"), result=result, error=error)

    # ── Per-task workspace MCP instances ────────────────────────────

    async def _route_workspace_scope(
        self,
        server_name: str,
        handle: MCPServerHandle,
    ) -> tuple[MCPServerHandle, dict[str, Any] | None]:
        """Route a call onto a scoped clone when the task root differs.

        Kill-switch: ``KAZMA_MCP_SCOPE_GUARD=0``. Non-workspace-bound
        servers skip. Spawn failure (or missing template) fail-closes.
        """
        try:
            if os.environ.get("KAZMA_MCP_SCOPE_GUARD", "1").strip().lower() in (
                "0", "false", "no", "off",
            ):
                return handle, None

            from kazma_core.ide.workspace_scope import resolve_workspace_root
            from kazma_core.workspace.binding import get_bound_mcp_root

            scoped_root = resolve_workspace_root()
            bound_root = get_bound_mcp_root()
            if scoped_root is None or bound_root is None:
                return handle, None
            if Path(bound_root).resolve() == Path(scoped_root).resolve():
                return handle, None

            template = self._server_templates.get(server_name)
            if template is not None:
                from kazma_core.workspace.mcp_rebind import is_workspace_bound_server

                if not is_workspace_bound_server(template):
                    return handle, None

            scoped = await self._get_or_spawn_scoped(server_name, Path(scoped_root))
            if scoped is not None and scoped.connected:
                return scoped, None

            return handle, {
                "content": (
                    f"MCP server '{server_name}' is bound to the active "
                    f"workspace ({bound_root}) but this task targets a "
                    f"different workspace ({scoped_root}). Per-workspace "
                    "MCP instance could not be started — switch the "
                    "active workspace, or dispatch without a per-task "
                    "workspace_id. (KAZMA_MCP_SCOPE_GUARD=0 disables "
                    "this guard.)"
                ),
                "is_error": True,
            }
        except Exception:
            logger.warning(
                "[MCP] scope guard check failed — denying call (fail-closed)",
                exc_info=True,
            )
            return handle, {
                "content": (
                    "MCP workspace scope guard failed closed: could not verify "
                    "this task's workspace against the bound MCP root. Bind the "
                    "active workspace, retry, or set KAZMA_MCP_SCOPE_GUARD=0."
                ),
                "is_error": True,
            }

    async def _get_or_spawn_scoped(
        self,
        server_name: str,
        scoped_root: Path,
    ) -> MCPServerHandle | None:
        """Spawn (or reuse) an LRU-capped clone rooted at *scoped_root*."""
        try:
            root_key = str(Path(scoped_root).resolve())
        except Exception:
            root_key = str(scoped_root)
        cache_key = (server_name, root_key)
        existing = self._scoped.get(cache_key)
        if existing is not None and existing.connected:
            self._scoped.move_to_end(cache_key)
            return existing
        if existing is not None:
            try:
                await self._close_handle(existing)
            except Exception:
                logger.debug("[MCP] close stale scoped handle failed", exc_info=True)
            self._scoped.pop(cache_key, None)

        template = self._server_templates.get(server_name)
        if not template:
            return None
        try:
            from kazma_core.workspace.mcp_rebind import (
                apply_workspace_to_server_config,
                is_workspace_bound_server,
            )
        except Exception:
            return None
        if not is_workspace_bound_server(template):
            return None

        digest = hashlib.sha256(root_key.encode("utf-8", errors="replace")).hexdigest()[:8]
        alias = f"{server_name}__scoped_{digest}"
        cfg = apply_workspace_to_server_config(dict(template), root=Path(scoped_root))
        cfg["name"] = alias
        transport = cfg.get("transport", "stdio")
        try:
            if transport == "stdio":
                await self._connect_stdio(alias, cfg)
            elif transport == "sse":
                await self._connect_sse(alias, cfg)
            elif transport in ("streamable_http", "streamable-http", "http"):
                await self._connect_streamable_http(alias, cfg)
            else:
                return None
        except Exception:
            logger.warning(
                "[MCP] scoped spawn failed server=%s root=%s",
                server_name,
                root_key,
                exc_info=True,
            )
            return None
        spawned = self._servers.pop(alias, None)
        if spawned is None or not spawned.connected:
            return None
        while len(self._scoped) >= self._MAX_SCOPED:
            _old_key, old_handle = self._scoped.popitem(last=False)
            try:
                await self._close_handle(old_handle)
            except Exception:
                logger.debug("[MCP] LRU evict scoped handle failed", exc_info=True)
        self._scoped[cache_key] = spawned
        logger.info(
            "[MCP] scoped instance server=%s root=%s alias=%s",
            server_name,
            root_key,
            alias,
        )
        return spawned

    async def execute_mcp_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool on a specific MCP server.

        Args:
            server_name: The MCP server name (as configured).
            tool_name: The tool name to call. Accepts BOTH the namespaced
                form (``mcp__<server>__<tool>``, what the LLM sees after
                :meth:`get_all_tool_schemas` namespaces) and the raw form
                (``<tool>``, what the server expects). The namespace prefix
                is stripped before sending so the server recognises the call.
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

        routed, scope_err = await self._route_workspace_scope(server_name, handle)
        if scope_err is not None:
            return scope_err
        handle = routed

        # Strip the mcp__<server>__ namespace prefix if present — the LLM
        # emits the namespaced form (to avoid collisions), but the server
        # only knows its own raw tool names.
        raw_tool_name = tool_name
        prefix = f"mcp__{server_name}__"
        if tool_name.startswith(prefix):
            raw_tool_name = tool_name[len(prefix):]
        elif tool_name.startswith("mcp__"):
            # Namespaced for a different server — strip just the last segment.
            raw_tool_name = tool_name.split("__", 2)[-1] if "__" in tool_name else tool_name

        # Gate filesystem path access BEFORE dispatching to the MCP server.
        # Closes the hole where MCP filesystem tools bypassed the path-grant
        # system (their own allowlist was the only gate).
        denial = _gate_mcp_path_access(raw_tool_name, arguments or {})
        if denial is not None:
            return denial

        start = time.monotonic()
        try:
            params: dict[str, Any] = {"name": raw_tool_name, "arguments": arguments if arguments is not None else {}}

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
            try:
                from kazma_core.agent.tool_loop_breaker import classify_mcp_error

                outcome = classify_mcp_error(content, is_error=is_error).value
            except Exception:
                outcome = "hard" if is_error else "ok"
            return {"content": content, "is_error": is_error, "outcome": outcome}

        except MCPBridgeError as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("[MCP] Tool '%s' on '%s' failed (%.0fms): %s", tool_name, server_name, duration_ms, exc)
            exc_s = str(exc)
            # Huge single-line JSON-RPC (directory_tree on monorepo) is a
            # capacity issue, not tool death — guide the model to native tools.
            if (
                "chunk exceed" in exc_s.lower()
                or "separator is not found" in exc_s.lower()
            ):
                return {
                    "content": (
                        f"MCP error: response too large for stdio framing "
                        f"({raw_tool_name}). Prefer native file_list/file_search "
                        f"on a shallow path instead of full-tree MCP calls. "
                        f"Detail: {exc_s[:400]}"
                    ),
                    "is_error": True,
                    "outcome": "policy",
                }
            return {"content": f"MCP error: {exc}", "is_error": True, "outcome": "hard"}

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("[MCP] Tool '%s' on '%s' crashed (%.0fms): %s", tool_name, server_name, duration_ms, exc)
            return {"content": f"Unexpected error: {exc}", "is_error": True, "outcome": "hard"}

    # ── Introspection ───────────────────────────────────────────────

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name belongs to any connected MCP server.

        Accepts both the namespaced form (``mcp__<server>__<tool>``, what
        the LLM emits after :meth:`get_all_tool_schemas` namespaces) and
        the raw form (``<tool>``).
        """
        # Fast path: namespaced form → split once, check the named server.
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__", 2)
            if len(parts) == 3:
                server, raw = parts[1], parts[2]
                handle = self._servers.get(server)
                if handle and handle.connected:
                    return any(
                        isinstance(t, dict) and t.get("name") == raw
                        for t in handle.tools
                    )
            return False
        # Raw form → scan all servers.
        for handle in self._servers.values():
            if not handle.connected:
                continue
            for tool in handle.tools:
                if isinstance(tool, dict) and tool.get("name") == tool_name:
                    return True
        return False

    def get_server_for_tool(self, tool_name: str) -> str | None:
        """Return the server name that owns a tool, or None.

        Accepts both the namespaced form (``mcp__<server>__<tool>``) and
        the raw form (``<tool>``).
        """
        # Fast path: namespaced form → the server is encoded in the name.
        if tool_name.startswith("mcp__"):
            parts = tool_name.split("__", 2)
            if len(parts) == 3:
                server, raw = parts[1], parts[2]
                handle = self._servers.get(server)
                if handle and handle.connected:
                    if any(
                        isinstance(t, dict) and t.get("name") == raw
                        for t in handle.tools
                    ):
                        return handle.name
            return None
        # Raw form → scan all servers.
        for handle in self._servers.values():
            if not handle.connected:
                continue
            for tool in handle.tools:
                if isinstance(tool, dict) and tool.get("name") == tool_name:
                    return handle.name
        return None

    def list_servers(self) -> list[dict[str, Any]]:
        """Return status info for all managed servers.

        O3 fix: for stdio transport, verify the process is still alive before
        reporting connected=True. If the process has exited (returncode is not
        None), mark it disconnected to avoid the "reconnect lie" where status
        says connected but the server is dead.
        """
        result = []
        for h in self._servers.values():
            # Health check: stdio transport - verify process is still alive
            if h.transport == "stdio" and h.process is not None:
                if h.process.returncode is not None:
                    # Process has exited - mark disconnected
                    if h.connected:
                        logger.warning(
                            "[MCP] Server '%s' process exited (returncode=%s) — marking disconnected",
                            h.name, h.process.returncode
                        )
                        h.connected = False

            result.append({
                "name": h.name,
                "transport": h.transport,
                "connected": h.connected,
                "tool_count": len(h.tools),
                "trust": h.trust,
            })
        return result

    def get_server_trust(self, server_name: str) -> str:
        """Return the trust level for a server (``trusted`` or ``approval_required``)."""
        handle = self._servers.get(server_name)
        return handle.trust if handle else "approval_required"

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

        # ── MCP stdio auth: inject auth into environment ────────────────
        # MCP stdio servers can receive auth via environment variables
        # or via command-line arguments (e.g., --api-key)
        auth = cfg.get("auth", {})
        if auth.get("type") == "env" and auth.get("name") and auth.get("value"):
            # Set auth token in environment for the subprocess
            env[auth["name"]] = auth["value"]
        elif auth.get("type") == "arg" and auth.get("name") and auth.get("value"):
            # Inject as command-line argument (--arg value)
            # Find insertion point after executable, before other args
            command = [command[0]] + [auth["name"], auth["value"]] + command[1:]

        logger.info("[MCP] Starting stdio server '%s': %s", name, command)

        # MCP JSON-RPC is newline-delimited. A single tool result (e.g.
        # filesystem directory_tree on a large monorepo) is often one giant
        # JSON line. asyncio's default StreamReader limit is 64 KiB, which
        # raises: "Separator is not found, and chunk exceed the limit" and
        # poisons the pipe for all later calls. Raise the limit for MCP only.
        try:
            stdio_limit = int(
                (os.environ.get("KAZMA_MCP_STDIO_LIMIT") or str(16 * 1024 * 1024)).strip()
            )
        except ValueError:
            stdio_limit = 16 * 1024 * 1024
        stdio_limit = max(256 * 1024, min(stdio_limit, 64 * 1024 * 1024))  # 256 KiB–64 MiB

        try:
            # Windows fix: asyncio.create_subprocess_exec does NOT resolve
            # .cmd/.bat shim extensions (e.g. npx → npx.cmd), raising a bare
            # FileNotFoundError. Resolve via shutil.which first so the full
            # path (including the .cmd extension) is passed. On Linux/macOS
            # this is a no-op (which returns the path as-is for executables).
            if sys.platform == "win32":
                import shutil as _shutil

                resolved = _shutil.which(command[0])
                if not resolved and command[0].lower() in ("npx", "npx.cmd"):
                    # Node updates can leave PATH pointing at a removed
                    # install dir (2026-09-03: npx vanished machine-wide
                    # mid-update). Probe the canonical Windows locations
                    # before giving up.
                    import os as _os

                    nvm_link = (
                        _os.environ.get("NVM_SYMLINK")
                        or r"C:\nvm4w\nodejs"
                    )
                    for cand in (
                        _os.path.join(
                            _os.environ.get("ProgramFiles", r"C:\Program Files"),
                            "nodejs", "npx.cmd",
                        ),
                        _os.path.join(nvm_link, "npx.cmd"),
                        _os.path.join(
                            _os.environ.get("APPDATA", ""), "npm", "npx.cmd",
                        ),
                    ):
                        if cand and _os.path.isfile(cand):
                            resolved = cand
                            break
                if resolved:
                    command = [resolved] + list(command[1:])
                    # The resolved shim's siblings must be findable by the
                    # CHILD too: npx.cmd shells out to `node`, and on
                    # nvm-windows both live in the symlink dir — which a
                    # stale-PATH server process doesn't have. Prepending
                    # the resolved dir fixes '"node" is not recognized'
                    # handshake deaths (2026-09-03).
                    _resolved_dir = _os.path.dirname(resolved)
                    if _resolved_dir:
                        env["PATH"] = (
                            _resolved_dir
                            + _os.pathsep
                            + str(env.get("PATH") or _os.environ.get("PATH", ""))
                        )

            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=working_dir,
                    limit=stdio_limit,
                )
            except NotImplementedError:
                # Windows SelectorEventLoop (forced for psycopg compat) cannot
                # host asyncio subprocesses. Fall back to blocking Popen +
                # executor-thread adapter — same JSON-RPC protocol, works on
                # every event-loop policy.
                loop = asyncio.get_running_loop()
                popen: subprocess.Popen[bytes] = await loop.run_in_executor(
                    None,
                    lambda: subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=env,
                        cwd=working_dir,
                    ),
                )
                process = _SyncProcessAdapter(popen)  # type: ignore[assignment]
        except FileNotFoundError as exc:
            hint = ""
            if command[0].lower().startswith(("npx", "node")):
                hint = (
                    " — Node.js/npx is not installed or not on the server's "
                    "PATH. Install Node.js (npx ships with it; the standalone "
                    "'npm i -g npx' package is deprecated and can shadow it), "
                    "then restart the Kazma server."
                )
            raise MCPBridgeError(
                f"Command not found: {command[0]}{hint}"
            ) from exc
        except OSError as exc:
            raise MCPBridgeError(f"Failed to start process: {exc}") from exc

        # Keep one timeout for the handshake and every later request.  A
        # server-specific setting must not silently revert to the generic 60s
        # limit immediately after it connects.
        request_timeout = self._resolve_timeout(
            cfg.get("timeout"),
            env_value=os.environ.get("KAZMA_MCP_TIMEOUT_MS"),
            default=90.0,
            env_is_milliseconds=True,
        )
        handle = MCPServerHandle(
            name=name,
            transport="stdio",
            process=process,
            command=command,
            timeout=request_timeout,
        )

        try:
            # MCP handshake.  The readline wait in ``_send`` defaults to 60s,
            # which is too short for ``npx`` cold starts (npm fetch of a fresh
            # package can take 30-90s on slow networks).  Allow a per-server
            # override via ``cfg["timeout"]`` or the ``KAZMA_MCP_TIMEOUT_MS``
            # env; default 90s for stdio.
            from kazma_core.mcp.spec_client import client_initialize_params

            await self._send(
                handle,
                "initialize",
                client_initialize_params("2024-11-05"),
                timeout=handle.timeout,
            )
            await self._notify(handle, "notifications/initialized", {})
        except TimeoutError:
            # npx cold-start or server never replied.  Capture stderr so the
            # user can see WHY (npm fetch failure / missing dependency /
            # bad config) instead of an opaque empty error.
            stderr_tail = await self._drain_stderr(handle, max_bytes=1024)
            reason = "timed out waiting for initialize response"
            if stderr_tail:
                reason += f" — server stderr: {stderr_tail.strip()[:400]}"
            await self._close_handle(handle)
            raise MCPBridgeError(f"Handshake failed for '{name}': {reason}") from None
        except Exception as exc:
            stderr_tail = await self._drain_stderr(handle, max_bytes=1024)
            msg = f"Handshake failed for '{name}': {exc}"
            if stderr_tail:
                msg += f" — server stderr: {stderr_tail.strip()[:400]}"
            await self._close_handle(handle)
            raise MCPBridgeError(msg) from exc

        try:
            # Discovering tools is part of connection setup.  Do not leave a
            # successfully-handshaken child process behind if this fails.
            result = await self._send(handle, "tools/list", {})
            tools = result.get("tools", []) if isinstance(result, dict) else []
            if not isinstance(tools, list):
                raise MCPBridgeError("tools/list returned a non-list 'tools' field")
            handle.tools = tools
            handle.connected = True
            handle.trust = cfg.get("trust", "approval_required")

            self._servers[name] = handle
            logger.info("[MCP] Connected to '%s' (stdio, pid=%d, tools=%d)", name, process.pid, len(tools))
            return len(tools)
        except Exception:
            await self._close_handle(handle)
            raise

    # ════════════════════════════════════════════════════════════════
    # Internal: SSE transport
    # ════════════════════════════════════════════════════════════════

    async def _connect_sse(self, name: str, cfg: dict[str, Any]) -> int:
        """Connect to an MCP server over HTTP SSE."""
        url = cfg.get("url", "")
        if not url:
            raise MCPBridgeError(f"SSE server '{name}' requires a 'url'")

        headers = dict(cfg.get("headers", {}))
        timeout = self._resolve_timeout(cfg.get("timeout"), default=30.0)

        # ── Inject auth headers from the first-class auth field ──────
        auth = cfg.get("auth", {})
        if auth.get("type") == "bearer" and auth.get("token"):
            headers["Authorization"] = "Bearer " + str(auth["token"])
        elif auth.get("type") == "header" and auth.get("name") and auth.get("value"):
            headers[auth["name"]] = auth["value"]

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
            from kazma_core.mcp.spec_client import client_initialize_params

            await self._send(
                handle,
                "initialize",
                client_initialize_params("2024-11-05"),
            )
            await self._notify(handle, "notifications/initialized", {})
        except Exception as exc:
            await http.aclose()
            raise MCPBridgeError(f"Handshake failed for '{name}': {exc}") from exc

        try:
            # Discovering tools is part of connection setup.  Close the HTTP
            # pool on a malformed/erroring tools/list response.
            result = await self._send(handle, "tools/list", {})
            tools = result.get("tools", []) if isinstance(result, dict) else []
            if not isinstance(tools, list):
                raise MCPBridgeError("tools/list returned a non-list 'tools' field")
            handle.tools = tools
            handle.connected = True
            handle.trust = cfg.get("trust", "approval_required")

            self._servers[name] = handle
            logger.info("[MCP] Connected to '%s' (sse, url=%s, tools=%d)", name, url, len(tools))
            return len(tools)
        except Exception:
            await self._close_handle(handle)
            raise

    # ════════════════════════════════════════════════════════════════
    # Internal: JSON-RPC transport
    # ════════════════════════════════════════════════════════════════

    async def _send(
        self,
        handle: MCPServerHandle,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send a JSON-RPC request and return the result.

        Args:
            timeout: Optional per-call override (seconds) for the response
                read.  Used by the stdio handshake to give ``npx`` cold
                starts enough time to fetch the package before the default
                read timeout fires.
        """
        request = _jsonrpc_request(method, params)
        raw = json.dumps(request) + "\n"

        if handle.transport == "stdio":
            return await self._send_stdio(handle, raw, timeout=timeout)
        if handle.transport == "streamable_http":
            return await self._send_streamable_http(handle, raw)
        return await self._send_sse(handle, raw)

    @staticmethod
    def _resolve_timeout(
        configured: Any,
        *,
        default: float,
        env_value: str | None = None,
        env_is_milliseconds: bool = False,
    ) -> float:
        """Resolve a positive timeout without letting malformed config leak resources."""
        value = configured
        if value is None and env_value:
            value = env_value
        try:
            seconds = float(value) if value is not None else default
            if env_value and configured is None and env_is_milliseconds:
                seconds /= 1000.0
            if seconds > 0:
                return seconds
        except (TypeError, ValueError):
            pass
        return default

    async def _drain_stderr(self, handle: MCPServerHandle, *, max_bytes: int = 2048) -> str:
        """Best-effort non-blocking read of a stdio server's stderr.

        Used by the handshake failure path so the user can see WHY an MCP
        server didn't respond (npm fetch failure, missing dependency, bad
        config, expired token) instead of an opaque empty error message.
        Returns up to ``max_bytes`` of whatever is buffered.  Never raises.
        """
        proc = getattr(handle, "process", None)
        if proc is None or not getattr(proc, "stderr", None):
            return ""
        try:
            # Use asyncio.to_thread to avoid blocking the event loop
            stderr = proc.stderr
            read_result: dict[str, str] = {"data": ""}

            def _sync_read() -> None:
                try:
                    if hasattr(stderr, "read1"):
                        data = stderr.read1(max_bytes)
                    else:
                        data = stderr.read(max_bytes)
                    if isinstance(data, bytes):
                        read_result["data"] = data.decode("utf-8", errors="replace")
                    elif data:
                        read_result["data"] = str(data)
                except Exception:
                    pass

            # ``read1`` on a pipe blocks until output arrives.  This is used
            # while reporting a failure, so diagnostics must never turn a
            # bounded handshake timeout into an unbounded hang.
            await asyncio.wait_for(asyncio.to_thread(_sync_read), timeout=0.25)
            return read_result["data"]
        except Exception:
            return ""

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
        elif handle.transport == "streamable_http" and handle.http is not None:
            # Streamable HTTP accepts notifications as POSTs that expect no
            # JSON-RPC result (HTTP 202). Best-effort.
            headers = {"Content-Type": "application/json"}
            if handle.session_id:
                headers["Mcp-Session-Id"] = handle.session_id
            try:
                await handle.http.post("", content=raw, headers=headers)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[MCP] streamable_http notify failed for '%s': %s", handle.name, exc)

    async def _send_stdio(self, handle: MCPServerHandle, raw: str, *, timeout: float | None = None) -> Any:
        """Send a JSON-RPC message over stdio and read the response.

        Args:
            timeout: Optional per-call read timeout (seconds). Defaults to
                60s — the stdio handshake passes a longer value (90s by
                default, ``KAZMA_MCP_TIMEOUT_MS``) so ``npx`` cold starts
                that fetch packages on first run don't get killed mid-fetch.
        """
        proc = handle.process
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise MCPBridgeError(f"stdio process '{handle.name}' not running")

        # stdio is a single bidirectional byte stream.  MCP responses can be
        # out of order, while this compact bridge does not keep an ID→future
        # reader.  Keep write+read as one transaction so concurrent tool calls
        # cannot consume one another's responses.
        read_timeout = timeout or handle.timeout
        async with handle.read_lock:
            try:
                proc.stdin.write(raw.encode())
                await proc.stdin.drain()
                while True:
                    line = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=read_timeout,
                    )
                    if not line:
                        handle.connected = False
                        retcode = proc.returncode
                        if retcode is not None:
                            stderr = ""
                            if proc.stderr:
                                try:
                                    stderr_bytes = await asyncio.wait_for(
                                        proc.stderr.read(4096), timeout=2.0
                                    )
                                    stderr = stderr_bytes.decode(errors="replace")
                                except TimeoutError:
                                    pass
                            raise MCPBridgeError(
                                f"Server '{handle.name}' exited with code {retcode}. "
                                f"stderr: {stderr[:500]}"
                            )
                        raise MCPBridgeError(
                            f"Server '{handle.name}' closed stdout (EOF)"
                        )
                    text = line.decode().strip()
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError:
                        return _jsonrpc_parse(text)
                    if (
                        isinstance(data, dict)
                        and "method" in data
                        and "result" not in data
                        and "error" not in data
                    ):
                        # Server-initiated request (sampling / roots / ping)
                        # while we wait for our JSON-RPC result.
                        if "id" in data:
                            reply = await self._reply_server_request(handle, data)
                            proc.stdin.write(reply.encode())
                            await proc.stdin.drain()
                        continue
                    return _jsonrpc_parse(text)
            except MCPBridgeError:
                raise
            except Exception as exc:
                # On read error, attempt to dump stderr and re-raise with diagnostic
                stderr_snippet = await self._drain_stderr(handle, max_bytes=2048)
                exc_s = str(exc)
                # Framing/limit failures leave stdout mid-message — mark dead so
                # the next call reconnects instead of failing instantly at 0ms.
                if (
                    "chunk exceed" in exc_s.lower()
                    or "separator is not found" in exc_s.lower()
                ):
                    logger.error(
                        "[MCP] stdio framing overflow for '%s' (raise KAZMA_MCP_STDIO_LIMIT "
                        "or avoid huge directory_tree). Marking disconnected.",
                        handle.name,
                    )
                # A timed-out/failed read leaves an unknown response in the
                # pipe.  Future requests cannot be correlated safely.
                handle.connected = False
                raise MCPBridgeError(
                    f"stdio read error for '{handle.name}': {exc}\nstderr: {stderr_snippet[:500]}"
                ) from exc

    async def _send_sse(self, handle: MCPServerHandle, raw: str) -> Any:
        """Send a JSON-RPC message over SSE and read the response."""
        if handle.http is None:
            raise MCPBridgeError(f"SSE client '{handle.name}' not initialized")

        try:
            resp = await handle.http.post(
                "/jsonrpc",
                content=raw,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return _jsonrpc_parse(resp.text)
        except httpx.HTTPError as exc:
            handle.connected = False
            detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    detail = exc.response.text.strip().replace("\n", " ")[:400]
                except Exception:
                    pass
            suffix = f" — response: {detail}" if detail else ""
            raise MCPBridgeError(
                f"SSE request to '{handle.name}' failed: {exc}{suffix}"
            ) from exc

    # ── Streamable HTTP transport (MCP 2025-03-26 spec) ──────────────
    #
    # The Streamable HTTP transport uses a SINGLE POST endpoint. The server
    # responds with an SSE stream (text/event-stream) carrying JSON-RPC
    # messages as ``data:`` frames, or a plain JSON body for simple
    # responses. A server-assigned ``Mcp-Session-Id`` header (returned on
    # initialize) must be echoed on subsequent requests.

    async def _connect_streamable_http(self, name: str, cfg: dict[str, Any]) -> int:
        """Connect to an MCP server over the Streamable HTTP transport."""
        url = cfg.get("url", "")
        if not url:
            raise MCPBridgeError(f"Streamable HTTP server '{name}' requires a 'url'")

        headers = dict(cfg.get("headers", {}))
        headers.setdefault("Accept", "application/json, text/event-stream")
        timeout = self._resolve_timeout(cfg.get("timeout"), default=30.0)

        auth = cfg.get("auth", {})
        if auth.get("type") == "bearer" and auth.get("token"):
            headers["Authorization"] = "Bearer " + str(auth["token"])
        elif auth.get("type") == "header" and auth.get("name") and auth.get("value"):
            headers[auth["name"]] = auth["value"]

        # OAuth 2.1 (MCP authorization spec): when no static token is set,
        # reuse a stored OAuth token (auto-refreshed when expired). A static
        # token always wins so operators can pin a PAT when preferred.
        if "Authorization" not in headers:
            try:
                from kazma_core.mcp.oauth import get_valid_token

                oauth_token = await get_valid_token(name)
                if oauth_token:
                    headers["Authorization"] = f"Bearer {oauth_token}"
            except Exception as exc:  # noqa: BLE001
                logger.debug("[MCP] OAuth token lookup skipped for '%s': %s", name, exc)

        http = httpx.AsyncClient(
            base_url=url,
            headers=headers,
            timeout=timeout,
        )

        handle = MCPServerHandle(
            name=name,
            transport="streamable_http",
            http=http,
            url=url,
        )

        # MCP handshake — initialize captures the session id from the response.
        try:
            from kazma_core.mcp.spec_client import client_initialize_params

            await self._send(
                handle,
                "initialize",
                client_initialize_params("2025-03-26"),
            )
            await self._notify(handle, "notifications/initialized", {})
        except Exception as exc:
            # MCP OAuth discovery: when the server answers 401 with a Bearer
            # resource_metadata challenge, remember it so the UI can offer an
            # OAuth login instead of a dead "401" message.
            resp = getattr(exc, "response", None)
            if resp is not None and getattr(resp, "status_code", None) == 401:
                www_auth = resp.headers.get("www-authenticate", "")
                if "bearer" in www_auth.lower():
                    self._oauth_challenges[name] = www_auth
            await http.aclose()
            raise MCPBridgeError(f"Handshake failed for '{name}': {exc}") from exc

        try:
            result = await self._send(handle, "tools/list", {})
            tools = result.get("tools", []) if isinstance(result, dict) else []
            if not isinstance(tools, list):
                raise MCPBridgeError("tools/list returned a non-list 'tools' field")
            handle.tools = tools
            handle.connected = True
            handle.trust = cfg.get("trust", "approval_required")

            self._servers[name] = handle
            logger.info(
                "[MCP] Connected to '%s' (streamable_http, url=%s, session=%s, tools=%d)",
                name, url, handle.session_id or "?", len(tools),
            )
            return len(tools)
        except Exception:
            await self._close_handle(handle)
            raise

    async def _send_streamable_http(self, handle: MCPServerHandle, raw: str) -> Any:
        """POST a JSON-RPC request to the streamable HTTP endpoint.

        Reads the response as either an SSE stream (collecting ``data:``
        frames until the JSON-RPC result) or a plain JSON body. Captures and
        stores the ``Mcp-Session-Id`` header for subsequent requests.
        """
        if handle.http is None:
            raise MCPBridgeError(f"Streamable HTTP client '{handle.name}' not initialized")

        headers = {"Content-Type": "application/json"}
        if handle.session_id:
            headers["Mcp-Session-Id"] = handle.session_id

        async with handle.read_lock:
            resp = await handle.http.post("", content=raw, headers=headers)
            # Persist the session id once we see it.
            sid = resp.headers.get("mcp-session-id")
            if sid and not handle.session_id:
                handle.session_id = sid
            resp.raise_for_status()

            ctype = (resp.headers.get("content-type") or "").lower()
            if "text/event-stream" in ctype:
                return self._parse_sse_stream(resp.text, handle)
            # Plain JSON response.
            return _jsonrpc_parse(resp.text)

    @staticmethod
    def _parse_sse_stream(body: str, handle: MCPServerHandle) -> Any:
        """Extract the JSON-RPC result from an SSE response body.

        Streamable HTTP may interleave notifications, pings, and the final
        result. We scan ``data:`` frames and return the first JSON-RPC object
        that carries a ``result`` or ``error`` for our request id.
        """
        import json as _json

        for chunk in body.split("\n\n"):
            data_lines = []
            for line in chunk.splitlines():
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if not data_lines:
                continue
            try:
                payload = _json.loads("\n".join(data_lines))
            except _json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and ("result" in payload or "error" in payload):
                if "error" in payload:
                    err = payload["error"]
                    raise MCPBridgeError(
                        f"MCP error {err.get('code')}: {err.get('message', '')}"
                    )
                return payload.get("result")
        # No result frame found — return an empty result so callers degrade.
        return {}


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
        rbac: Any = None,
    ) -> None:
        if mcp is None:
            # Always carry an MCP manager so callers can connect servers
            # after construction (e.g. KazmaAgent.connect_mcp_servers).
            mcp = AsyncMCPManager()
        self._local = local
        self._mcp = mcp
        set_active_mcp_manager(mcp)
        # Original configs (pre-rebind templates) for workspace rebind
        self._server_configs: dict[str, dict[str, Any]] = {}

        if rbac is None:
            from kazma_core.rbac import RBACEngine
            rbac = RBACEngine()
        self._rbac = rbac

    @property
    def mcp(self) -> AsyncMCPManager:
        """The underlying MCP manager.

        UnifiedToolExecutor delegates *some* MCP surface (is_server_connected,
        connect_server) but not all of it -- ``connect_from_config`` and
        ``connection_errors`` live only on the manager. A caller that needs
        those had to reach for ``_mcp``, and reaching for a private is how the
        reconnect supervisor was first wired to an object that silently
        answered "no errors, nothing connected" forever.
        """
        return self._mcp

    # ── MCP server lifecycle (delegates to AsyncMCPManager) ─────────

    async def connect_server(self, server_config: dict[str, Any]) -> int:
        """Connect a single MCP server and register its tools.

        Workspace-bound servers (filesystem MCP) have their allowed root
        substituted to the active workspace before spawn. Templates are
        stored so Switch Repo can rebind without losing the original shape.
        """
        if self._mcp is None:
            return 0

        # Install rebind bus once (idempotent)
        try:
            from kazma_core.workspace.mcp_rebind import (
                apply_workspace_to_server_config,
                install_mcp_workspace_rebind,
                is_workspace_bound_server,
            )

            install_mcp_workspace_rebind(self)
            name = str(server_config.get("name") or "unnamed")
            # Keep a template without a resolved absolute path for rebind
            template = dict(server_config)
            self._server_configs[name] = template
            cfg = (
                apply_workspace_to_server_config(template)
                if is_workspace_bound_server(template)
                else dict(server_config)
            )
        except Exception as exc:
            logger.debug("[Unified] workspace MCP bind skipped: %s", exc)
            cfg = dict(server_config)

        return await self._mcp.connect_from_config([cfg])

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

    @chaos_injection(InjectionTarget.TOOL_EXECUTOR)
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
        if arguments is None:
            arguments = {}

        # Extract and pop RBAC context keys to prevent signature mismatch downstream
        user_id = arguments.pop("user_id", arguments.pop("_user_id", None))
        division = arguments.pop("division", arguments.pop("_division", None))
        resource = arguments.pop("resource", arguments.pop("_resource", None))
        action = arguments.pop("action", arguments.pop("_action", None))

        try:
            from kazma_core.division_runtime import check_division_tool

            _div_err = await check_division_tool(tool_name)
            if _div_err:
                return {"content": _div_err, "is_error": True}
        except Exception:
            logger.debug("[Unified] division check skipped", exc_info=True)

        if user_id:
            resolved_div = division or "general_trading"
            resolved_res = resource or tool_name
            resolved_act = action or "execute"
            perm = await self._rbac.check_permission(
                user_id=user_id,
                division=resolved_div,
                resource=resolved_res,
                action=resolved_act,
            )
            if not perm.allowed:
                return {
                    "content": f"Access Denied: {perm.reason}",
                    "is_error": True,
                }

        # ── Try local first ────────────────────────────────────────
        if self._local is not None:
            local_tool = self._local.get_tool(tool_name)
            if local_tool is not None:
                logger.debug("[Unified] Routing '%s' → local", tool_name)
                return await self._local.execute(tool_name, arguments)

        # ── Try MCP ────────────────────────────────────────────────
        if self._mcp is not None and self._mcp.is_mcp_tool(tool_name):
            server_name = self._mcp.get_server_for_tool(tool_name)
            if server_name:
                # ── HITL gate for MCP tools ──────────────────────────
                # MCP tools are runtime-discovered and bypass the graph's
                # static interrupt() gate. Classify by name pattern and
                # route danger-tier tools through the swarm bus for approval.
                # Skip if the graph already approved (double-gating prevention).
                # Skip if the server is explicitly trusted (trust: trusted).
                # We NEVER trust _hitl_approved from LLM args (prompt-injection
                # risk); only the ContextVar set by graph_builder is honored.
                arguments.pop("_hitl_approved", None)
                try:
                    from kazma_core.agent.tool_hooks import apply_pre_tool_hooks

                    _denied, arguments = await apply_pre_tool_hooks(tool_name, arguments)
                    if _denied is not None:
                        return _denied
                except Exception:
                    logger.debug("[Unified] pre-tool hook failed", exc_info=True)
                from kazma_core.agent.tool_registry import _hitl_approved_ctx

                _hitl_already_approved = _hitl_approved_ctx.get()
                if not _hitl_already_approved:
                    try:
                        from kazma_core.safety.hitl import get_current_thread_id
                        from kazma_core.safety.yolo import is_yolo_active

                        _tid = get_current_thread_id()
                        if _tid and is_yolo_active(_tid):
                            _hitl_already_approved = True
                    except Exception:
                        logger.debug("[Unified] YOLO check skipped", exc_info=True)
                _server_trusted = (
                    self._mcp.get_server_trust(server_name) == "trusted"
                )
                if not _hitl_already_approved and not _server_trusted:
                    # Audit M10 / WP-3.6: untrusted MCP — force HITL for all
                    # tools except explicit allowlist. "safe" name patterns are
                    # not enough (list_keys, get_env, export_data, …).
                    import os as _os_mcp

                    allow_raw = (
                        _os_mcp.environ.get("KAZMA_MCP_SAFE_ALLOWLIST") or ""
                    ).strip()
                    allowlist = {
                        a.strip().lower()
                        for a in allow_raw.split(",")
                        if a.strip()
                    }
                    tier = classify_mcp_tool(tool_name)
                    prod = (_os_mcp.environ.get("KAZMA_PRODUCTION") or "").lower() in (
                        "1", "true", "on", "yes",
                    )
                    # Production: HITL for every tool not on the allowlist.
                    # Dev/default: danger + unknown only (safe name patterns skip).
                    if prod:
                        force_hitl = tool_name.lower() not in allowlist
                    else:
                        force_hitl = tier in ("danger", "unknown")
                    if force_hitl:
                        try:
                            import json as _json

                            from kazma_core.swarm.safety import get_safety

                            safety = get_safety()
                            if safety.enabled:
                                # force_danger=True: MCP tool names (write_file,
                                # run_command, …) are not in the static
                                # _EXTENDED_DANGER set (file_write, shell_exec).
                                approved = await safety.check(
                                    tool_name=tool_name,
                                    tool_args=_json.dumps(arguments, default=str)[:200],
                                    task_id=str(arguments.get("task_id", "")),
                                    worker_name=f"mcp:{server_name}",
                                    force_danger=True,
                                )
                                if not approved:
                                    return {
                                        "content": f"MCP tool '{tool_name}' denied by HITL approval gate.",
                                        "is_error": True,
                                    }
                        except Exception as _e:
                            logger.debug("Safety check failed for MCP tool %s: %s", tool_name, _e)
                            return {
                                "content": f"MCP tool '{tool_name}' blocked — SafetyMiddleware unavailable.",
                                "is_error": True,
                            }

                logger.debug("[Unified] Routing '%s' → MCP server '%s'", tool_name, server_name)
                _mcp_result = await self._mcp.execute_mcp_tool(server_name, tool_name, arguments)
                try:
                    from kazma_core.agent.tool_hooks import apply_post_tool_hooks

                    return await apply_post_tool_hooks(tool_name, arguments, _mcp_result)
                except Exception:
                    logger.debug("[Unified] post-tool hook failed", exc_info=True)
                    return _mcp_result

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
                    raw_tool_name = tool_name.removeprefix(
                        f"mcp__{server_name}__"
                    )
                    for tool in handle.tools:
                        if (
                            isinstance(tool, dict)
                            and tool.get("name") == raw_tool_name
                        ):
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

    async def disconnect_server(self, name: str, *, forget_config: bool = True) -> bool:
        """Disconnect a single MCP server by name.

        Returns True if a server was disconnected.
        """
        if self._mcp is None:
            return False
        disconnected = await self._mcp.disconnect_server(name)
        if forget_config:
            # A manually stopped/deleted workspace-bound server must not be
            # resurrected by the next workspace-switch rebind event.
            self._server_configs.pop(name, None)
        return disconnected

    async def disconnect_all(self) -> None:
        """Shutdown all backends."""
        if self._mcp is not None:
            await self._mcp.shutdown()
        self._server_configs.clear()
        # Local registry needs no cleanup
