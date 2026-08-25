"""MCP 2026 client surfaces beyond tools: resources, prompts, sampling, roots.

Kazma remains an MCP *client* (ACP hosts Kazma in editors). Resources are
fenced untrusted data. Prompts are user-visible. Sampling and elicitation
never skip HITL — default deny without an approval context.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "authorize_mcp_sampling",
    "client_initialize_params",
    "extract_resource_text",
    "fence_resource",
    "handle_mcp_server_request",
    "jsonrpc_reply",
    "workspace_roots",
]


def client_initialize_params(protocol_version: str = "2024-11-05") -> dict[str, Any]:
    """Handshake params advertising tools + resources + prompts + sampling + roots."""
    return {
        "protocolVersion": protocol_version,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
            "sampling": {},
            "roots": {"listChanged": True},
            "elicitation": {},
        },
        "clientInfo": {"name": "kazma-mcp-bridge", "version": "0.1.0"},
    }


def extract_resource_text(result: Any) -> str:
    """Pull text (or a short binary note) from a resources/read result."""
    if not isinstance(result, dict):
        return str(result or "")
    contents = result.get("contents")
    if not isinstance(contents, list):
        text = result.get("text")
        return str(text or "")
    parts: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        if item.get("text"):
            parts.append(str(item["text"]))
        elif item.get("blob"):
            mime = item.get("mimeType") or "application/octet-stream"
            parts.append(f"[binary resource mime={mime} omitted]")
    return "\n".join(parts)


def fence_resource(text: str, *, server: str, uri: str = "") -> str:
    """Wrap MCP resource bytes as untrusted observation data."""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    source = f"mcp_resource:{server}"
    if uri:
        source = f"{source}:{uri[:80]}"
    return format_untrusted_block(text or "", source=source)


def workspace_roots() -> list[dict[str, str]]:
    """MCP roots/list payload — the active workspace only."""
    root = ""
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = str(resolve_active_root() or "")
    except Exception:
        root = ""
    if not root:
        return []
    try:
        uri = Path(root).resolve().as_uri()
    except Exception:
        uri = f"file://{root.replace(os.sep, '/')}"
    return [{"uri": uri, "name": "workspace"}]


def authorize_mcp_sampling(params: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Fail-closed. Servers must not drive our LLM without HITL.

    Default off (``KAZMA_MCP_SAMPLING`` unset). Even when on, there is no
    auto-sample path — an approval context is required.
    """
    _ = params
    flag = os.environ.get("KAZMA_MCP_SAMPLING", "0").strip().lower()
    if flag not in ("1", "true", "on", "yes"):
        return False, (
            "MCP sampling/createMessage is denied by default. "
            "Set KAZMA_MCP_SAMPLING=1 and approve via HITL — Kazma will not "
            "auto-sample for a server."
        )
    try:
        from kazma_core.safety.hitl import get_current_thread_id

        thread = get_current_thread_id()
    except Exception:
        thread = None
    if not thread:
        return False, (
            "MCP sampling requires HITL approval (no thread/approval context). "
            "Not auto-run."
        )
    return False, (
        "MCP sampling requires HITL approval on this thread; "
        "auto-sample is never allowed."
    )


def authorize_mcp_elicitation(params: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Elicitation cannot skip HITL. Default deny."""
    _ = params
    return False, (
        "MCP elicitation/create requires HITL. Kazma does not auto-fill "
        "server-driven forms."
    )


def jsonrpc_reply(
    req_id: Any,
    *,
    result: Any | None = None,
    error: str | None = None,
) -> str:
    """Serialize a JSON-RPC 2.0 response line (newline-terminated)."""
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error:
        msg["error"] = {"code": -32000, "message": error}
    else:
        msg["result"] = result if result is not None else {}
    return json.dumps(msg) + "\n"


async def handle_mcp_server_request(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    server_name: str = "",
) -> tuple[Any, str | None]:
    """Handle a server-initiated JSON-RPC request.

    Returns ``(result, error_message)``. Sampling/elicitation always error
    unless a future HITL resume path sets an approval (none today).
    """
    _ = server_name
    params = params or {}
    name = (method or "").strip()
    if name == "ping":
        return {}, None
    if name == "roots/list":
        return {"roots": workspace_roots()}, None
    if name == "sampling/createMessage":
        ok, reason = authorize_mcp_sampling(params)
        if not ok:
            return None, reason
        return None, "MCP sampling has no auto-complete path"
    if name in ("elicitation/create", "elicitation/request"):
        ok, reason = authorize_mcp_elicitation(params)
        if not ok:
            return None, reason
        return None, "MCP elicitation has no auto-complete path"
    return None, f"Unsupported server request '{name}'"
