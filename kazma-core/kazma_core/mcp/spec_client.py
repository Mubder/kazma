"""MCP 2026 client surfaces beyond tools: resources, prompts, sampling, roots.

Kazma remains an MCP *client* (ACP hosts Kazma in editors). Resources are
fenced untrusted data. Prompts are user-visible. Sampling and elicitation
never skip HITL — default deny without an approval context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "authorize_mcp_sampling",
    "client_initialize_params",
    "complete_mcp_sample",
    "extract_resource_text",
    "fence_resource",
    "handle_mcp_server_request",
    "jsonrpc_reply",
    "list_sampling_pending",
    "resolve_sampling_hitl",
    "workspace_roots",
]

# In-place HITL for sampling/createMessage. Graph interrupt() would unwind
# the MCP stdio JSON-RPC wait; this future stays on the stack until the
# existing Approve button (POST /api/approve/{thread_id}) resolves it.
_sampling_pending: dict[str, dict[str, Any]] = {}
_sampling_lock = asyncio.Lock()


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


def sampling_enabled() -> bool:
    return os.environ.get("KAZMA_MCP_SAMPLING", "0").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


def _sampling_timeout() -> float:
    try:
        return max(0.05, float(os.environ.get("KAZMA_MCP_SAMPLING_TIMEOUT", "60")))
    except (TypeError, ValueError):
        return 60.0


def authorize_mcp_sampling(params: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Synchronous pre-check. Default off. Does not auto-sample."""
    _ = params
    if not sampling_enabled():
        return False, (
            "MCP sampling/createMessage is denied by default. "
            "Set KAZMA_MCP_SAMPLING=1 and approve via HITL — Kazma will not "
            "auto-sample for a server."
        )
    return True, ""


def list_sampling_pending() -> list[dict[str, Any]]:
    """HITL-card rows for GET /api/pending-approvals (no secrets)."""
    rows: list[dict[str, Any]] = []
    for item in list(_sampling_pending.values()):
        payload = dict(item.get("payload") or {})
        payload.setdefault("thread_id", item.get("thread_id"))
        payload.setdefault("tool_name", "mcp_sampling")
        payload.setdefault("tool", "mcp_sampling")
        payload.setdefault("kind", "mcp_sampling")
        rows.append(payload)
    return rows


def resolve_sampling_hitl(thread_id: str, approved: bool) -> bool:
    """Resolve an in-flight sampling wait. True if a waiter was waiting."""
    key = (thread_id or "").strip()
    item = _sampling_pending.get(key)
    if item is None:
        return False
    fut = item.get("future")
    if isinstance(fut, asyncio.Future) and not fut.done():
        fut.set_result(bool(approved))
        return True
    return False


def _preview_messages(params: dict[str, Any] | None) -> str:
    messages = (params or {}).get("messages") or []
    bits: list[str] = []
    for msg in messages[:4]:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, dict):
            content = content.get("text") or ""
        elif isinstance(content, list):
            content = " ".join(
                str(p.get("text") or "") for p in content if isinstance(p, dict)
            )
        bits.append(str(content or "")[:200])
    return " | ".join(bits)[:400]


async def request_sampling_hitl(
    params: dict[str, Any] | None = None,
    *,
    server_name: str = "",
) -> tuple[bool, str]:
    """Wait in-place for HITL. Does not call LangGraph interrupt() (stdio)."""
    ok, reason = authorize_mcp_sampling(params)
    if not ok:
        return False, reason

    thread = ""
    try:
        from kazma_core.safety.hitl import get_current_thread_id

        thread = str(get_current_thread_id() or "")
    except Exception:
        thread = ""
    if not thread:
        return False, (
            "MCP sampling requires HITL (no thread/approval context). "
            "Not auto-run."
        )

    payload = {
        "type": "hitl_approval",
        "kind": "mcp_sampling",
        "tool": "mcp_sampling",
        "tool_name": "mcp_sampling",
        "thread_id": thread,
        "args": {
            "server": server_name,
            "preview": _preview_messages(params),
        },
        "message": (
            f"MCP server '{server_name or '?'}' wants Kazma to sample "
            "(call our LLM, no tools). Approve once. Not auto-run."
        ),
    }
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[bool] = loop.create_future()
    async with _sampling_lock:
        _sampling_pending[thread] = {"payload": payload, "future": fut, "thread_id": thread}

    bus_task: asyncio.Task[bool | None] | None = None
    try:
        from kazma_core.swarm.bus import NullBusAdapter, get_message_bus
        from kazma_core.swarm.safety import get_safety

        bus = get_message_bus()
        if not isinstance(getattr(bus, "adapter", None), NullBusAdapter):
            safety = get_safety()

            async def _bus() -> bool | None:
                try:
                    return bool(
                        await safety.check(
                            "mcp_sampling",
                            tool_args=str(payload["args"])[:200],
                            force_danger=True,
                        )
                    )
                except Exception:
                    return None

            bus_task = asyncio.create_task(_bus())
    except Exception:
        bus_task = None

    waiters: list[asyncio.Future[Any] | asyncio.Task[Any]] = [fut]
    if bus_task is not None:
        waiters.append(bus_task)
    try:
        done, _pending = await asyncio.wait(
            waiters,
            timeout=_sampling_timeout(),
            return_when=asyncio.FIRST_COMPLETED,
        )
    except Exception:
        done = set()
    finally:
        async with _sampling_lock:
            _sampling_pending.pop(thread, None)
        if bus_task is not None and not bus_task.done():
            bus_task.cancel()

    approved = False
    for item in done:
        try:
            val = item.result()
        except Exception:
            continue
        if val is True:
            approved = True
            break
    if approved:
        return True, ""
    return False, "HITL denied or timed out MCP sampling"


def _mcp_messages_to_openai(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for msg in raw:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")
        if role not in ("user", "assistant", "system"):
            role = "user"
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, dict):
            text = str(content.get("text") or "")
        elif isinstance(content, list):
            text = "\n".join(
                str(p.get("text") or "") for p in content if isinstance(p, dict)
            )
        out.append({"role": role, "content": text})
    return out


async def complete_mcp_sample(params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call the active LLM **without tools**. Meaning stays in Kazma's client."""
    params = params or {}
    messages = _mcp_messages_to_openai(params.get("messages"))
    if not messages:
        messages = [{"role": "user", "content": "Respond briefly."}]
    max_tokens = 1024
    try:
        max_tokens = int((params.get("maxTokens") or params.get("max_tokens") or 1024))
    except (TypeError, ValueError):
        max_tokens = 1024
    from kazma_core.llm_stream import invoke_llm_chat
    from kazma_core.model_registry import get_model_registry

    client = get_model_registry().get_client()
    resp = await invoke_llm_chat(
        client,
        messages,
        tools=None,
        max_tokens=max_tokens,
    )
    text = str(getattr(resp, "content", None) or "")
    model = str(getattr(resp, "model", None) or "")
    return {
        "role": "assistant",
        "content": {"type": "text", "text": text},
        "model": model or "kazma",
        "stopReason": "endTurn",
    }


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
        ok, reason = await request_sampling_hitl(params, server_name=server_name)
        if not ok:
            return None, reason
        try:
            result = await complete_mcp_sample(params)
            return result, None
        except Exception as exc:
            logger.warning("[MCP] sampling completion failed: %s", exc)
            return None, f"MCP sampling LLM call failed: {exc}"
    if name in ("elicitation/create", "elicitation/request"):
        ok, reason = authorize_mcp_elicitation(params)
        if not ok:
            return None, reason
        return None, "MCP elicitation has no auto-complete path"
    return None, f"Unsupported server request '{name}'"
