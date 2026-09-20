"""Native tools wrapping MCP resources/prompts (fenced, user-visible).

``AsyncMCPManager`` returns ``{"content": ..., "is_error": bool}`` and these
wrappers used to return ``result["content"]`` alone, dropping the flag. A
transport failure, a disconnected server or a server-side JSON-RPC error then
arrived at the model looking exactly like a successful fetch:
``LocalToolRegistry`` reported ``is_error=False`` and the supervisor's retry
never saw a failure to retry.

The flag is restored **out of band**, via the ``Error:`` prefix that
``tool_registry`` already recognises (alongside ``⚠️`` and ``Safety:``), and
the failure body is **still fenced**. Both halves matter and they pull in
opposite directions:

* ``fence_untrusted(text, is_error=True)`` returns text RAW, on the contract
  that an error message is the caller's own words. That contract does not
  hold here. An MCP failure body can be ``str(exc)`` wrapping a JSON-RPC
  ``error.message`` the *server* wrote, so marking it ``is_error`` and
  passing it through would hand attacker-authored text to the model
  unfenced — the same shape as the ``Error:``-prefix bypass closed on
  2026-09-13.
* Fencing it without the prefix keeps the injection contained but leaves the
  failure invisible, which is the bug above.

So: our own prefix, then the server's words inside a fence. The prefix is
ours and unforgeable from inside the fence; the body is untrusted and framed
as such.
"""

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


def _failed(result: Any) -> bool:
    """True when the manager flagged this call as failed."""
    return isinstance(result, dict) and bool(result.get("is_error"))


def _error_out(what: str, result: Any, *, source: str) -> str:
    """Render a failed MCP call: our prefix, the server's words fenced.

    The ``Error:`` prefix is what ``LocalToolRegistry`` reads to set
    ``is_error`` on the tool result, so the supervisor can retry. It must be
    at position 0 and must be ours.
    """
    from kazma_core.safety.prompt_fence import fence_untrusted

    body = str((result or {}).get("content") or "") if isinstance(result, dict) else str(result or "")
    detail = fence_untrusted(body, source=source) if body.strip() else ""
    return f"Error: {what} failed." + (f"\n{detail}" if detail else "")


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
    from kazma_core.safety.prompt_fence import fence_untrusted

    return fence_untrusted(
        "\n".join(lines), source=f"mcp_resources:{server or 'all'}"
    )


async def mcp_read_resource(server: str, uri: str) -> str:
    """Read one MCP resource. Result is fenced untrusted data."""
    mgr = _manager()
    if mgr is None:
        return "Error: MCP manager is not connected."
    result = await mgr.read_resource((server or "").strip(), (uri or "").strip())
    if _failed(result):
        return _error_out(
            f"MCP resource read {server}/{uri}", result, source=f"mcp_resource:{server}"
        )
    # Already wrapped by AsyncMCPManager.read_resource → fence_resource.
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
    from kazma_core.safety.prompt_fence import fence_untrusted

    return fence_untrusted(
        "\n".join(lines), source=f"mcp_prompts:{server or 'all'}"
    )


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
    if _failed(result):
        return _error_out(
            f"MCP prompt fetch {server}/{name}", result, source=f"mcp_prompt:{server}/{name}"
        )
    from kazma_core.safety.prompt_fence import fence_untrusted

    return fence_untrusted(
        str(result.get("content") or ""),
        source=f"mcp_prompt:{server}/{name}",
    )
