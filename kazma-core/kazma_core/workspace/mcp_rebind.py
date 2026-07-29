"""MCP workspace rebind — keep filesystem MCP rooted at the active workspace.

External stdio MCP servers (e.g. ``@modelcontextprotocol/server-filesystem``)
lock their allowed directory at **spawn time**.  Native tools follow
WorkspaceStore; MCP used to stay on a fossil path from ``kazma.yaml``.

This module:

1. Interpolates ``${KAZMA_ACTIVE_WORKSPACE}`` (and legacy bare relative
   sandbox paths for bound servers) at connect time.
2. Subscribes to :func:`notify_root_changed` and disconnects/reconnects
   workspace-bound servers when Switch Repo / clone fires.

MCP is process-global: concurrent swarm ``workspace_scope`` does **not**
rebind MCP (native tools only). Documented limitation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from kazma_core.workspace.binding import (
    get_bound_mcp_root,
    resolve_active_root,
    set_bound_mcp_root,
    subscribe_root_changed,
)

__all__ = [
    "ACTIVE_WORKSPACE_PLACEHOLDER",
    "apply_workspace_to_server_config",
    "install_mcp_workspace_rebind",
    "interpolate_command",
    "is_workspace_bound_server",
    "rebind_workspace_mcp_servers",
]

logger = logging.getLogger(__name__)

ACTIVE_WORKSPACE_PLACEHOLDER = "${KAZMA_ACTIVE_WORKSPACE}"

# Debounce rebinds when multiple switch events fire quickly
_REBIND_DEBOUNCE_S = 0.35
_last_rebind_at: float = 0.0
_rebind_lock: asyncio.Lock | None = None
_installed = False
_executor_ref: Any = None  # UnifiedToolExecutor or object with connect/disconnect


def _get_rebind_lock() -> asyncio.Lock:
    global _rebind_lock
    if _rebind_lock is None:
        _rebind_lock = asyncio.Lock()
    return _rebind_lock


def is_workspace_bound_server(cfg: dict[str, Any]) -> bool:
    """Return True if this MCP server config should track the active workspace."""
    if cfg.get("workspace_bound") is True:
        return True
    # Auto-detect filesystem MCP command templates
    command = cfg.get("command") or []
    if not isinstance(command, list):
        return False
    joined = " ".join(str(c) for c in command)
    if "server-filesystem" in joined or ACTIVE_WORKSPACE_PLACEHOLDER in joined:
        return True
    return False


def interpolate_command(command: list[Any], root: Path) -> list[str]:
    """Replace workspace placeholders in a command argv with *root*."""
    abs_root = str(root.resolve())
    out: list[str] = []
    for part in command:
        s = str(part)
        if ACTIVE_WORKSPACE_PLACEHOLDER in s:
            s = s.replace(ACTIVE_WORKSPACE_PLACEHOLDER, abs_root)
        out.append(s)
    return out


def _looks_like_legacy_sandbox_arg(arg: str) -> bool:
    """True for relative default sandbox paths we used to hardcode in yaml."""
    norm = arg.replace("\\", "/").strip().lower()
    return norm in (
        "kazma-data/workspace",
        "./kazma-data/workspace",
        "data/workspace",
        "./data/workspace",
    )


def apply_workspace_to_server_config(
    cfg: dict[str, Any],
    root: Path | None = None,
) -> dict[str, Any]:
    """Return a shallow-copied server config with workspace path substituted.

    For workspace-bound servers:
      - Expand ``${KAZMA_ACTIVE_WORKSPACE}`` in command/env
      - Force the filesystem allowed-dir arg to the absolute active root
    """
    if not is_workspace_bound_server(cfg):
        return dict(cfg)

    active = (root or resolve_active_root()).resolve()
    abs_root = str(active)
    new_cfg = dict(cfg)
    new_cfg["workspace_bound"] = True

    command = list(cfg.get("command") or [])
    if command:
        command = interpolate_command(command, active)
        # Filesystem MCP: last argv entry is the allowed directory — always pin
        # to the active root so Switch Repo never leaves a fossil jail.
        if any("server-filesystem" in str(c) for c in command):
            command[-1] = abs_root
        elif command and (
            _looks_like_legacy_sandbox_arg(command[-1])
            or ACTIVE_WORKSPACE_PLACEHOLDER in str(cfg.get("command", [])[-1:])
        ):
            command[-1] = abs_root
        new_cfg["command"] = command

    env = dict(cfg.get("env") or {})
    for k, v in list(env.items()):
        if isinstance(v, str) and ACTIVE_WORKSPACE_PLACEHOLDER in v:
            env[k] = v.replace(ACTIVE_WORKSPACE_PLACEHOLDER, abs_root)
    if env:
        new_cfg["env"] = env

    new_cfg["_resolved_workspace"] = abs_root
    return new_cfg


async def rebind_workspace_mcp_servers(
    root: Path,
    *,
    reason: str = "switch",
    executor: Any | None = None,
) -> int:
    """Disconnect and reconnect workspace-bound MCP servers at *root*.

    Returns number of servers rebound successfully.
    """
    global _last_rebind_at

    ex = executor if executor is not None else _executor_ref
    if ex is None:
        logger.debug("[MCP-Rebind] no executor registered; skip rebind reason=%s", reason)
        set_bound_mcp_root(root)
        return 0

    lock = _get_rebind_lock()
    async with lock:
        now = time.monotonic()
        if now - _last_rebind_at < _REBIND_DEBOUNCE_S and get_bound_mcp_root() == root.resolve():
            return 0
        _last_rebind_at = now

        # Discover currently connected workspace-bound servers + their configs
        # Prefer stored configs on the executor / async manager.
        configs = _collect_bound_server_configs(ex)
        if not configs:
            set_bound_mcp_root(root)
            logger.info(
                "[MCP-Rebind] no workspace-bound servers connected; recorded root=%s",
                root,
            )
            return 0

        rebound = 0
        for name, cfg in configs:
            try:
                if hasattr(ex, "disconnect_server"):
                    await ex.disconnect_server(name)
                new_cfg = apply_workspace_to_server_config(cfg, root)
                if hasattr(ex, "connect_server"):
                    await ex.connect_server(new_cfg)
                elif hasattr(ex, "_mcp") and hasattr(ex._mcp, "connect_from_config"):
                    await ex._mcp.connect_from_config([new_cfg])
                rebound += 1
                logger.info(
                    "[MCP-Rebind] rebound server '%s' → %s (reason=%s)",
                    name,
                    root,
                    reason,
                )
            except Exception as exc:
                logger.warning(
                    "[MCP-Rebind] failed to rebind '%s' to %s: %s",
                    name,
                    root,
                    exc,
                )

        set_bound_mcp_root(root)
        return rebound


def _collect_bound_server_configs(ex: Any) -> list[tuple[str, dict[str, Any]]]:
    """Best-effort: list (name, config) for connected workspace-bound MCP servers."""
    out: list[tuple[str, dict[str, Any]]] = []

    # UnifiedToolExecutor may keep original configs
    stored = getattr(ex, "_server_configs", None) or getattr(ex, "server_configs", None)
    if isinstance(stored, dict):
        for name, cfg in stored.items():
            if isinstance(cfg, dict) and is_workspace_bound_server(cfg):
                out.append((name, dict(cfg)))
        if out:
            return out

    mcp = getattr(ex, "_mcp", None) or getattr(ex, "mcp", None)
    servers = getattr(mcp, "_servers", None) if mcp is not None else None
    if isinstance(servers, dict):
        for name, handle in servers.items():
            cmd = getattr(handle, "command", None) or []
            cfg: dict[str, Any] = {
                "name": name,
                "transport": getattr(handle, "transport", "stdio") or "stdio",
                "command": list(cmd) if cmd else [],
                "workspace_bound": True,
            }
            if cfg["command"] or is_workspace_bound_server(cfg):
                # Only rebind if it looks like filesystem
                joined = " ".join(str(c) for c in cfg["command"])
                if "server-filesystem" in joined or cfg.get("workspace_bound"):
                    out.append((name, cfg))
    return out


def _on_root_changed(root: Path, reason: str) -> Any:
    """Sync callback registered on the binding bus; returns an awaitable rebind.

    Returns the bare :func:`rebind_workspace_mcp_servers` coroutine so
    :func:`notify_root_changed` can schedule it uniformly via its own
    ``create_task`` path. We intentionally do **not** pre-schedule a Task here
    — returning an already-scheduled Task would make the dispatcher try to
    wrap the Task in another task (``"a coroutine was expected, got <Task>"``).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.debug(
            "[MCP-Rebind] root changed to %s (%s) but no event loop for rebind",
            root,
            reason,
        )
        set_bound_mcp_root(root)
        return None

    return rebind_workspace_mcp_servers(
        root, reason=reason, executor=_executor_ref
    )


def install_mcp_workspace_rebind(executor: Any) -> None:
    """Register *executor* for rebinds and subscribe to workspace changes.

    Idempotent: safe to call on every agent/MCP connect.
    """
    global _executor_ref, _installed
    _executor_ref = executor
    if not _installed:
        subscribe_root_changed(_on_root_changed)
        _installed = True
        logger.info("[MCP-Rebind] workspace rebind installed")
    # Record current root for health
    try:
        set_bound_mcp_root(resolve_active_root())
    except Exception:
        pass
