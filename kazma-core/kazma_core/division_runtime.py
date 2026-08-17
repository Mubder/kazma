"""Live division sandbox + cross-division authorization.

Fail-open when no division is configured (default single-operator).
When ``KAZMA_DIVISION`` / ``agent.division`` is set, MCP servers listed
in ``kazma-permissions.yaml`` ``divisions.<name>.denied_mcp_servers``
are blocked and an :class:`AuthorizationFlow` request is minted.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

__all__ = [
    "check_division_tool",
    "current_division_context",
    "division_enforcement_on",
    "get_authorization_flow",
    "get_division_sandbox",
    "list_auth_requests",
    "mcp_server_from_tool",
    "reset_division_runtime",
]

logger = logging.getLogger(__name__)

_rbac: Any = None
_sandbox: Any = None
_flow: Any = None
_yaml_cache: dict[str, Any] | None = None


def reset_division_runtime() -> None:
    global _rbac, _sandbox, _flow, _yaml_cache
    _rbac = None
    _sandbox = None
    _flow = None
    _yaml_cache = None


def division_enforcement_on() -> bool:
    raw = (os.environ.get("KAZMA_DIVISION_ENFORCE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    return bool(current_division_context())


def current_division_context() -> tuple[str, str] | None:
    """``(user_id, division)`` or ``None`` when enforcement is off."""
    div = (os.environ.get("KAZMA_DIVISION") or "").strip()
    if not div:
        try:
            from kazma_core.config_store import get_config_store

            div = str(get_config_store().get("agent.division") or "").strip()
        except Exception:
            div = ""
    if not div:
        return None
    user = (os.environ.get("KAZMA_DIVISION_USER") or "default").strip() or "default"
    return user, div


def mcp_server_from_tool(tool_name: str) -> str:
    name = (tool_name or "").strip()
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            return parts[1]
    return ""


def _permissions_yaml() -> dict[str, Any]:
    global _yaml_cache
    if _yaml_cache is not None:
        return _yaml_cache
    path = Path(__file__).resolve().parent.parent.parent / "kazma-permissions.yaml"
    data: dict[str, Any] = {}
    try:
        import yaml

        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
    except Exception:
        logger.debug("[division] yaml load failed", exc_info=True)
    _yaml_cache = data
    return data


def _get_rbac() -> Any:
    global _rbac
    if _rbac is None:
        from kazma_core.rbac import RBACEngine

        _rbac = RBACEngine()
    return _rbac


def get_division_sandbox() -> Any:
    global _sandbox
    if _sandbox is None:
        from kazma_core.division_sandbox import DivisionSandbox

        _sandbox = DivisionSandbox(_get_rbac())
    return _sandbox


def get_authorization_flow() -> Any:
    global _flow
    if _flow is None:
        from kazma_core.authorization_flow import AuthorizationFlow

        _flow = AuthorizationFlow(_get_rbac())
    return _flow


def list_auth_requests() -> list[dict[str, Any]]:
    flow = get_authorization_flow()
    reqs = getattr(flow, "_requests", {}) or {}
    out = []
    for r in reqs.values():
        out.append(
            {
                "id": r.id,
                "user_id": r.user_id,
                "source_division": r.source_division,
                "target_division": r.target_division,
                "resource": r.resource,
                "justification": r.justification,
                "status": r.status,
                "created_at": r.created_at,
                "expires_at": r.expires_at,
            }
        )
    return out


def _mcp_allowed(server: str, division: str) -> bool:
    if not server:
        return True
    divs = (_permissions_yaml().get("divisions") or {})
    spec = divs.get(division) or {}
    denied = [str(x) for x in (spec.get("denied_mcp_servers") or [])]
    allowed = [str(x) for x in (spec.get("allowed_mcp_servers") or [])]
    if server in denied:
        return False
    if allowed and server not in allowed:
        return False
    return True


async def check_division_tool(tool_name: str) -> str | None:
    """Return an error string to block the tool, or None to allow.

    No division configured → None (fail-open).
    """
    ctx = current_division_context()
    if ctx is None:
        return None
    user, division = ctx
    try:
        rbac = _get_rbac()
        if division in rbac.divisions and not await rbac.is_user_in_division(
            user, division
        ):
            await rbac.assign_role(
                user, division, "admin", granted_by="division_runtime"
            )
    except Exception:
        logger.debug("[division] membership ensure skipped", exc_info=True)
    server = mcp_server_from_tool(tool_name)
    if server and not _mcp_allowed(server, division):
        try:
            flow = get_authorization_flow()
            req = await flow.request_access(
                user_id=user,
                source_division=division,
                target_division=division,
                resource=server,
                justification=f"tool {tool_name} needs MCP server {server}",
            )
            return (
                f"[division] MCP server {server!r} is outside {division} "
                f"allowlist. Authorization request {req.id} pending."
            )
        except Exception as exc:
            logger.debug("[division] auth request failed: %s", exc)
            return (
                f"[division] MCP server {server!r} denied for division "
                f"{division!r}"
            )
    return None
