"""Workspace binding — single source of truth for the active coding root.

Public API:
  - :func:`resolve_active_root` — one ladder used by tools, IDE, UI, MCP
  - :func:`configure_workspace` — process pin (boot / tests)
  - :func:`notify_root_changed` / :func:`subscribe_root_changed` — lifecycle bus
  - :func:`default_sandbox_root` — last-resort default workspace path
"""

from __future__ import annotations

from kazma_core.workspace.binding import (
    configure_workspace,
    default_sandbox_root,
    get_bound_mcp_root,
    notify_root_changed,
    resolve_active_root,
    set_bound_mcp_root,
    subscribe_root_changed,
    unsubscribe_root_changed,
)

__all__ = [
    "configure_workspace",
    "default_sandbox_root",
    "get_bound_mcp_root",
    "notify_root_changed",
    "resolve_active_root",
    "set_bound_mcp_root",
    "subscribe_root_changed",
    "unsubscribe_root_changed",
]
