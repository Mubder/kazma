"""Tool Sandbox — Executes MCP tools within an isolated, permission-checked context.

Every external tool call MUST pass through the sandbox before reaching the MCP
server.  The sandbox enforces a two-tier permission model:

1. **Deny list** — tools that are never allowed (checked first).
2. **Allow list** — tools (or ``*`` for wildcard) that are explicitly permitted.

Denied tools are rejected with ``PermissionError`` before the MCP server is
reached.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from kazma_core.mcp_client import MCPClient

__all__ = ["ToolSandbox"]

logger = logging.getLogger(__name__)

# Patterns that are NEVER allowed regardless of allowlist — these look like
# shell injection or dangerous system operations.
_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r";\s*(rm|mkfs|dd|format)\b", re.IGNORECASE),
    re.compile(r"\|\s*(rm|mkfs|dd|format)\b", re.IGNORECASE),
    re.compile(r"`[^`]*`", re.IGNORECASE),  # backtick subshell
    re.compile(r"\$\([^)]*\)", re.IGNORECASE),  # $() subshell
]


class ToolSandbox:
    """Executes MCP tools in an isolated sandbox with permission checks.

    The sandbox acts as a gatekeeper between the agent and the MCP server.
    All tool invocations MUST go through :meth:`execute`.

    Args:
        allowed_tools: Tool names that are permitted.  Use ``["*"]`` to
            allow all tools (useful for development only).
        denied_tools: Tool names that are always blocked, even if they
            appear in *allowed_tools*.
    """

    def __init__(
        self,
        allowed_tools: list[str] | None = None,
        denied_tools: list[str] | None = None,
    ) -> None:
        self.allowed: set[str] = set(allowed_tools) if allowed_tools else set()
        self.denied: set[str] = set(denied_tools) if denied_tools else set()

    # -- public API --------------------------------------------------------

    async def execute(
        self,
        client: MCPClient,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a tool if allowed, otherwise reject.

        Checks performed (in order):
        1. Dangerous-pattern scan on tool name and string argument values.
        2. Deny-list check.
        3. Allow-list check (wildcard ``*`` grants all non-denied tools).

        Args:
            client: Connected ``MCPClient`` to route the call through.
            tool_name: Name of the tool to execute.
            args: Optional arguments for the tool.

        Returns:
            The tool result dict from the MCP server.

        Raises:
            PermissionError: If the tool is denied or not in the allow list.
        """
        self._check_dangerous(tool_name, args)
        self._check_denied(tool_name)
        self._check_allowed(tool_name)

        logger.info("Sandboxed tool call: %s (server=%s)", tool_name, client.server_name)
        return await client.call_tool(tool_name, args or {})

    def is_allowed(self, tool_name: str) -> bool:
        """Check whether a tool would pass the sandbox without executing it."""
        if tool_name in self.denied:
            return False
        return tool_name in self.allowed or "*" in self.allowed

    # -- internal ----------------------------------------------------------

    def _check_dangerous(self, tool_name: str, args: dict[str, Any] | None) -> None:
        """Reject tools or arguments that contain dangerous patterns."""
        # Check the tool name itself
        for pat in _DANGEROUS_PATTERNS:
            if pat.search(tool_name):
                raise PermissionError(f"Tool name contains dangerous pattern: {tool_name}")

        # Check string values in arguments
        if args:
            for key, value in args.items():
                if isinstance(value, str):
                    for pat in _DANGEROUS_PATTERNS:
                        if pat.search(value):
                            raise PermissionError(f"Argument '{key}' contains dangerous pattern in tool '{tool_name}'")

    def _check_denied(self, tool_name: str) -> None:
        if tool_name in self.denied:
            raise PermissionError(f"Tool '{tool_name}' is explicitly denied")

    def _check_allowed(self, tool_name: str) -> None:
        if tool_name not in self.allowed and "*" not in self.allowed:
            raise PermissionError(f"Tool '{tool_name}' is not in the allowlist")
