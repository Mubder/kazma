"""Kazma MCP — Model Context Protocol bridge layer.

Exposes AsyncMCPManager for managing MCP server connections
and a UnifiedToolExecutor that routes to local or MCP tools.
"""

from kazma_core.mcp.manager import AsyncMCPManager, UnifiedToolExecutor

__all__ = ["AsyncMCPManager", "UnifiedToolExecutor"]
