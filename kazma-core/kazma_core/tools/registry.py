"""Dynamic Tool Registry — permission-gated tool execution for **swarm workers**.

Canonical split (do not confuse with the agent path):

* **Agent / LangGraph** — ``kazma_core.agent.tool_registry.LocalToolRegistry``
  (builtins: ``shell_exec``, file tools, etc.).
* **Swarm workers** — this module (``ToolRegistry`` / ``get_tool_registry``).
  Shell is registered as both ``shell`` and ``shell_exec`` for HITL name parity.

Tools use ``PermissionLevel`` (READ_ONLY, SYSTEM_EXEC, FULL_ACCESS).
The Orchestrator checks the worker role before granting access.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ── Permission model ──────────────────────────────────────────────────


class PermissionLevel(StrEnum):
    READ_ONLY = "read_only"          # ls, cat, git status
    SYSTEM_EXEC = "system_exec"      # git commit, pip install
    FULL_ACCESS = "full_access"      # shell_exec, docker


# ── Tool result ────────────────────────────────────────────────────────


@dataclass(slots=True)
class ToolResult:
    """Structured result from a tool execution."""
    tool_name: str
    success: bool
    output: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0
    permission: PermissionLevel = PermissionLevel.READ_ONLY
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Base tool ──────────────────────────────────────────────────────────


class BaseTool:
    """Abstract tool with permission gating."""

    name: str = "base"
    permission: PermissionLevel = PermissionLevel.READ_ONLY

    async def execute(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError


# ── Shell Tool (sandboxed) ─────────────────────────────────────────────


class ShellTool(BaseTool):
    """Restricted shell execution with AST safety scanning.

    Blocks dangerous patterns (rm -rf, os.system, etc.) using the same
    AST scanner from hardening.py.  Captures stdout/stderr into a
    structured ToolResult.
    """

    name = "shell"
    permission = PermissionLevel.SYSTEM_EXEC

    # Read-only / safe commands always allowed
    _READ_ONLY_COMMANDS = {
        "ls", "cat", "head", "tail", "grep", "find", "wc", "sort",
        "uniq", "echo", "date", "whoami", "pwd", "env", "df", "du",
        "free", "uptime", "uname", "hostname", "ps", "pgrep",
        "git", "tar", "gzip", "gunzip", "zip", "unzip",
        "jq", "tr", "cut", "mkdir", "cp", "mv", "touch",
        "hermes", "kazma", "uv", "pytest", "ruff", "mypy",
    }

    # Commands that need AST-level safety scan
    _RESTRICTED_COMMANDS = {
        "python", "python3", "pip", "pip3", "node", "npm", "npx",
        "docker", "docker-compose", "curl", "wget",
    }

    @classmethod
    def is_safe(cls, command: str) -> bool:
        """Check if a shell command is safe to execute.

        Returns True if the command:
        - Starts with a read-only binary, OR
        - Passes AST safety scanning (no os.system, eval, etc.)
        """
        import shlex
        try:
            args = shlex.split(command)
        except ValueError:
            return False
        if not args:
            return False
        binary = args[0].split("/")[-1]
        if binary in cls._READ_ONLY_COMMANDS:
            return True
        if binary in cls._RESTRICTED_COMMANDS:
            # Extra safety: scan for dangerous patterns in the full command
            return not cls._has_dangerous_pattern(command)
        return False

    @staticmethod
    def _has_dangerous_pattern(command: str) -> bool:
        """Scan for dangerous shell patterns: rm -rf, eval, os.system, etc."""
        dangerous = [
            "rm -rf", "rm -r", "mkfs.", "dd if=", ">/dev/sda",
            "chmod 777", "chown root", "sudo ",
            "os.system", "eval(", "exec(",
        ]
        cmd_lower = command.lower()
        return any(d in cmd_lower for d in dangerous)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute a shell command with safety gates."""
        command = kwargs.get("command", "")
        timeout = int(kwargs.get("timeout", 30))
        import shlex
        import time as _time

        if not self.is_safe(command):
            return ToolResult(
                tool_name=self.name,
                success=False,
                output="Blocked: unsafe command pattern detected",
                exit_code=-1,
                permission=self.permission,
            )

        try:
            args = shlex.split(command)
        except ValueError as exc:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output=f"Invalid command: {exc}",
                exit_code=-1,
                permission=self.permission,
            )

        t0 = _time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            return ToolResult(
                tool_name=self.name,
                success=False,
                output=f"Command timed out after {timeout}s",
                exit_code=-1,
                duration_ms=(_time.perf_counter() - t0) * 1000,
                permission=self.permission,
            )

        duration_ms = (_time.perf_counter() - t0) * 1000
        output = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        return ToolResult(
            tool_name=self.name,
            success=proc.returncode == 0,
            output=output or err or "(no output)",
            stderr=err,
            exit_code=proc.returncode or 0,
            duration_ms=duration_ms,
            permission=self.permission,
        )


# ── Tool Registry ──────────────────────────────────────────────────────


class ToolRegistry:
    """Permission-gated tool registry for swarm workers."""
    _instance: ToolRegistry | None = None

    def __new__(cls) -> ToolRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools: dict[str, BaseTool] = {}
            cls._instance._init_defaults()
        return cls._instance

    def _init_defaults(self) -> None:
        # Register under both "shell" (legacy) and "shell_exec" (HITL danger name)
        # so SafetyMiddleware._EXTENDED_DANGER and swarm workers agree.
        self._tools["shell"] = ShellTool()
        shell_exec = ShellTool()
        shell_exec.name = "shell_exec"
        self._tools["shell_exec"] = shell_exec
        self._register_builtin_tools()

    def _register_builtin_tools(self) -> None:
        """Register all pre-existing tools as registry entries."""
        # Register directly — simpler than tuple mapping
        self._register_builtin("web_search", "kazma_core.tools.web_search", "web_search",
                               PermissionLevel.READ_ONLY, "DuckDuckGo web search")
        self._register_builtin("file_read", "kazma_core.tools.file_read", "file_read",
                               PermissionLevel.READ_ONLY, "Read local files")
        self._register_builtin("file_write", "kazma_core.tools.file_write", "file_write",
                               PermissionLevel.SYSTEM_EXEC, "Write files to disk")
        self._register_builtin("read_url", "kazma_core.tools.read_url", "read_url",
                               PermissionLevel.READ_ONLY, "Read web pages")
        self._register_builtin("vision_analyze", "kazma_core.tools.vision_analyze", "analyze_image",
                               PermissionLevel.READ_ONLY, "AI vision analysis")

    def _register_builtin(self, tool_name: str, module_path: str, fn_name: str,
                           perm: PermissionLevel, desc: str) -> None:
        """Import and wrap a single built-in tool."""
        try:
            mod = __import__(module_path, fromlist=[fn_name])
            fn = getattr(mod, fn_name)
            wrapped = _make_tool_class(tool_name, fn, perm, desc)
            self._tools[tool_name] = wrapped()
        except ImportError:
            logger.debug("[ToolRegistry] built-in '%s' not available", tool_name)

    @property
    def shell(self) -> ShellTool:
        return self._tools["shell"]  # type: ignore[return-value]

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.info("[ToolRegistry] Tool '%s' registered (permission=%s)", tool.name, tool.permission)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def can_use(self, worker_role: str, tool_name: str) -> bool:
        """Check if a worker role can use a tool."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return False
        # Orchestrator and root roles get full access
        if worker_role in ("orchestrator", "root"):
            return True
        # Researcher/analyst roles: read-only only
        if worker_role in ("researcher", "analyst", "bridge"):
            return tool.permission == PermissionLevel.READ_ONLY
        # Builder/developer roles: up to system_exec
        if worker_role in ("builder", "developer"):
            return tool.permission in (PermissionLevel.READ_ONLY, PermissionLevel.SYSTEM_EXEC)
        return False

    def list_available(self, worker_role: str) -> list[str]:
        """Return tool names available to a worker role."""
        return [name for name in self._tools if self.can_use(worker_role, name)]

    def list_tools(self) -> list[dict[str, Any]]:
        """Return all registered tools as a JSON-safe list."""
        return [
            {
                "name": t.name,
                "permission": t.permission.value,
                "id": f"kazma-tool://{t.name}",
                "description": getattr(t, "description", ""),
                "enabled": True,
                "security_score": 100,
                "certification_level": "basic",
                "capabilities": [t.name],
                "tags": [t.permission.value],
            }
            for t in self._tools.values()
        ]


def _make_tool_class(name: str, fn, perm: PermissionLevel, desc: str) -> type[BaseTool]:
    """Create a lightweight BaseTool wrapper around an async function."""
    from typing import Any as _Any

    class _Wrapped(BaseTool):
        async def execute(self, **kwargs: _Any) -> ToolResult:
            try:
                output = await fn(**kwargs)
                return ToolResult(
                    tool_name=self.name,
                    success=True,
                    output=str(output),
                    permission=self.permission,
                )
            except Exception as exc:
                return ToolResult(
                    tool_name=self.name,
                    success=False,
                    output=str(exc),
                    permission=self.permission,
                )
    _Wrapped.name = name
    _Wrapped.permission = perm
    _Wrapped.description = desc
    return _Wrapped


# ── Singleton access ───────────────────────────────────────────────────


def get_tool_registry() -> ToolRegistry:
    """Return the shared ToolRegistry singleton."""
    return ToolRegistry()
