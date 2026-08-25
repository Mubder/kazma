"""Kazma MCP — Model Context Protocol bridge layer.

Exposes AsyncMCPManager for managing MCP server connections
and a UnifiedToolExecutor that routes to local or MCP tools.
"""

from kazma_core.mcp.manager import (
    AsyncMCPManager,
    UnifiedToolExecutor,
    get_active_mcp_manager,
    set_active_mcp_manager,
)

__all__ = [
    "AsyncMCPManager",
    "UnifiedToolExecutor",
    "get_active_mcp_manager",
    "set_active_mcp_manager",
]
