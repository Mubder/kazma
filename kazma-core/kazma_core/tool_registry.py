"""Tool Registry — Bridges MCP servers to LLM function-calling format.

Manages connected MCP servers, lists their tools in OpenAI function-calling
format, and executes tool calls through the appropriate MCP client.

Usage:
    registry = ToolRegistry()
    await registry.connect_server({"name": "web", "transport": "sse", "url": "..."})
    tools = registry.get_tool_definitions()  # OpenAI format
    result = await registry.execute("web_search", {"query": "hello"})
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kazma_core.mcp_client import MCPClient, MCPServerConfig, MCPError

logger = logging.getLogger(__name__)


@dataclass
class RegisteredTool:
    """A tool available through an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


class ToolRegistry:
    """Manages MCP server connections and bridges tools to LLM format."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}  # server_name -> client
        self._tools: dict[str, RegisteredTool] = {}  # tool_name -> tool
        self._connected: bool = False
        self._skills_manifest = None
        self._load_skills()  # Load skills from kazma-skills/manifests/

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    def _load_skills(self) -> None:
        """Load skills from kazma-skills/manifests/ directory."""
        try:
            from kazma_skills.manifest import SkillManifest
            # Resolve the path to kazma-skills/manifests/
            manifest_path = Path(__file__).resolve().parent.parent.parent / "kazma-skills" / "manifests"
            self._skills_manifest = SkillManifest()
            self._skills_manifest._load_directory(manifest_path)
            logger.info("Loaded %d skills from manifests", len(self._skills_manifest.list_tools()))
        except ImportError:
            logger.warning("kazma_skills module not found — skills disabled")
        except Exception as e:
            logger.error("Failed to load skills: %s", e)

    def get_skill_arabic_name(self, tool_name: str) -> str:
        """Get the Arabic name for a tool (if defined in skills manifest)."""
        if not self._skills_manifest:
            return tool_name
        return self._skills_manifest.get_arabic_name(tool_name)

    def get_skill_prompt_chain(self, tool_name: str) -> str:
        """Get the Arabic prompt chain for a tool (if defined in skills manifest)."""
        if not self._skills_manifest:
            return ""
        return self._skills_manifest.get_arabic_prompt(tool_name)

    def get_skill_cultural_context(self, tool_name: str) -> dict[str, Any]:
        """Get cultural formatting rules for a tool (if defined in skills manifest)."""
        if not self._skills_manifest:
            return {}
        return self._skills_manifest.get_cultural_context(tool_name)

    async def connect_server(self, server_config: dict[str, Any]) -> int:
        """Connect to an MCP server and register its tools.

        Args:
            server_config: Dict with keys: name, transport, command/url, etc.

        Returns:
            Number of tools registered from this server.
        """
        name = server_config.get("name", "unnamed")
        client = MCPClient()

        try:
            await client.connect(server_config)
            tools = await client.list_tools()
        except (MCPError, Exception) as e:
            logger.warning("Failed to connect MCP server '%s': %s", name, e)
            return 0

        self._clients[name] = client

        count = 0
        for tool in tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                continue

            registered = RegisteredTool(
                name=tool_name,
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {"type": "object", "properties": {}}),
                server_name=name,
            )
            self._tools[tool_name] = registered
            count += 1

        self._connected = bool(self._tools)
        logger.info("Registered %d tools from MCP server '%s'", count, name)
        return count

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return all registered tools in OpenAI function-calling format.

        Returns:
            List of tool dicts compatible with OpenAI's tools parameter.
        """
        definitions = []
        for tool in self._tools.values():
            definitions.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            })
        return definitions

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by name through its MCP server.

        Args:
            tool_name: The tool name as registered.
            arguments: Tool arguments.

        Returns:
            Dict with 'content' (str) and 'is_error' (bool).
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return {"content": f"Tool '{tool_name}' not found in registry.", "is_error": True}

        client = self._clients.get(tool.server_name)
        if client is None or not client.connected:
            return {"content": f"MCP server '{tool.server_name}' not connected.", "is_error": True}

        try:
            result = await client.call_tool(tool_name, arguments)
            # Extract text content from MCP result
            content_parts = []
            for item in result.get("content", []):
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        content_parts.append(item.get("text", ""))
                    else:
                        content_parts.append(str(item))
                else:
                    content_parts.append(str(item))

            return {
                "content": "\n".join(content_parts) if content_parts else str(result),
                "is_error": result.get("isError", False),
            }
        except MCPError as e:
            logger.error("Tool '%s' execution failed: %s", tool_name, e)
            return {"content": f"Tool execution error: {e}", "is_error": True}
        except Exception as e:
            logger.error("Unexpected error executing tool '%s': %s", tool_name, e)
            return {"content": f"Unexpected error: {e}", "is_error": True}

    async def disconnect_all(self) -> None:
        """Disconnect all MCP servers."""
        for name, client in self._clients.items():
            try:
                await client.disconnect()
                logger.info("Disconnected MCP server '%s'", name)
            except Exception as e:
                logger.warning("Error disconnecting '%s': %s", name, e)
        self._clients.clear()
        self._tools.clear()
        self._connected = False

    def list_servers(self) -> list[str]:
        """Return names of connected MCP servers."""
        return list(self._clients.keys())

    def list_tools(self) -> list[dict[str, str]]:
        """Return a summary of all registered tools."""
        return [
            {"name": t.name, "description": t.description[:100], "server": t.server_name}
            for t in self._tools.values()
        ]
