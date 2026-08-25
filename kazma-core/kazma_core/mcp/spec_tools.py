"""Native tools wrapping MCP resources/prompts (fenced, user-visible)."""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "mcp_get_prompt",
    "mcp_list_prompts",
    "mcp_list_resources",
    "mcp_read_resource",
]


def _manager():
    from kazma_core.mcp.manager import get_active_mcp_manager

    return get_active_mcp_manager()


async def mcp_list_resources(server: str = "") -> str:
    """List MCP resources. Empty server = all connected servers."""
    mgr = _manager()
    if mgr is None:
        return "Error: MCP manager is not connected."
    rows = await mgr.list_resources(server.strip() or None)
    if not rows:
        return "No MCP resources listed (server may not implement resources/list)."
    lines = []
    for row in rows:
        uri = row.get("uri") or ""
        name = row.get("name") or ""
        srv = row.get("_mcp_server") or server
        lines.append(f"{srv}: {name} {uri}".strip())
    return "\n".join(lines)


async def mcp_read_resource(server: str, uri: str) -> str:
    """Read one MCP resource. Result is fenced untrusted data."""
    mgr = _manager()
    if mgr is None:
        return "Error: MCP manager is not connected."
    result = await mgr.read_resource((server or "").strip(), (uri or "").strip())
    return str(result.get("content") or "")


async def mcp_list_prompts(server: str = "") -> str:
    """List MCP prompts. Empty server = all connected servers."""
    mgr = _manager()
    if mgr is None:
        return "Error: MCP manager is not connected."
    rows = await mgr.list_prompts(server.strip() or None)
    if not rows:
        return "No MCP prompts listed (server may not implement prompts/list)."
    lines = []
    for row in rows:
        name = row.get("name") or ""
        desc = row.get("description") or ""
        srv = row.get("_mcp_server") or server
        lines.append(f"{srv}: {name} — {desc}".strip(" —"))
    return "\n".join(lines)


async def mcp_get_prompt(server: str, name: str, arguments: str = "") -> str:
    """Fetch an MCP prompt as user-visible text (not system instructions)."""
    mgr = _manager()
    if mgr is None:
        return "Error: MCP manager is not connected."
    args: dict[str, Any] | None = None
    raw = (arguments or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                args = parsed
        except json.JSONDecodeError:
            args = None
    result = await mgr.get_prompt((server or "").strip(), (name or "").strip(), args)
    return str(result.get("content") or "")
