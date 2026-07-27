"""Workspace binding — process pin, resolution ladder, and change bus.

Invariants (see plan / AGENTS.md §10A):

1. **One ladder** — :func:`resolve_active_root` is the only resolver.
2. **Switch Repo / clone** go through WorkspaceStore activation, which
   pins tools and fires :func:`notify_root_changed` so MCP can rebind.
3. No second ladders in UI/MCP config.

Precedence (high → low)::

    workspace_scope → active WorkspaceStore → process pin
    → KAZMA_WORKSPACE env → default sandbox under project data_dir
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

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

logger = logging.getLogger(__name__)

# ── Process pin (boot / tests / activate side-effect) ───────────────────

_WORKSPACE_ROOT: Path | None = None
_ALLOW_ABSOLUTE: bool = False

# ── Change bus ──────────────────────────────────────────────────────────

RootChangedCallback = Callable[[Path, str], Any]
_subscribers: list[RootChangedCallback] = []
_sub_lock = threading.Lock()

# Last MCP-bound root (observability / health; set by MCP rebind layer)
_bound_mcp_root: Path | None = None
_bound_mcp_lock = threading.Lock()


def default_sandbox_root() -> Path:
    """Last-resort default coding sandbox under project data dir.

    Prefer ``paths.data_dir()/workspace`` so default native tools and a
    workspace-bound MCP filesystem share the same empty sandbox on first
    boot — not the monorepo CWD.
    """
    try:
        from kazma_core.paths import data_dir

        root = data_dir() / "workspace"
    except Exception:
        root = Path.cwd() / "kazma-data" / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def configure_workspace(workspace: str | None = None, allow_absolute: bool = False) -> None:
    """Configure the process workspace pin and absolute-path policy.

    Args:
        workspace: Path to agent workspace. ``None`` clears the pin.
        allow_absolute: If True, absolute paths outside workspace are allowed
            by file tools that honor this flag.
    """
    global _WORKSPACE_ROOT, _ALLOW_ABSOLUTE
    _WORKSPACE_ROOT = Path(workspace).expanduser().resolve() if workspace else None
    _ALLOW_ABSOLUTE = allow_absolute


def allow_absolute_paths() -> bool:
    """Whether file tools may accept absolute paths outside the workspace."""
    return _ALLOW_ABSOLUTE


def get_process_pin() -> Path | None:
    """Return the current process pin (may be None)."""
    return _WORKSPACE_ROOT


def resolve_active_root() -> Path:
    """Resolve the active coding workspace root (single SoT ladder).

    Precedence:
      1. Per-task ``workspace_scope`` (swarm concurrent multi-repo).
      2. Active WorkspaceStore row (Switch Repo / clone — user intent).
      3. Process pin from :func:`configure_workspace`.
      4. ``KAZMA_WORKSPACE`` env.
      5. :func:`default_sandbox_root`.
    """
    global _WORKSPACE_ROOT

    # 1. Per-task scope
    try:
        from kazma_core.ide.workspace_scope import resolve_workspace_root

        scoped = resolve_workspace_root()
        if scoped is not None:
            return scoped
    except Exception:
        pass

    # 2. Active WorkspaceStore
    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("root_path"):
            active_path = Path(active["root_path"]).expanduser().resolve()
            if _WORKSPACE_ROOT is None or _WORKSPACE_ROOT.resolve() != active_path:
                _WORKSPACE_ROOT = active_path
            return active_path
    except Exception:
        pass

    # 3. Process pin
    if _WORKSPACE_ROOT is not None:
        return _WORKSPACE_ROOT

    # 4. Env
    env_ws = os.environ.get("KAZMA_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    # 5. Default sandbox
    return default_sandbox_root()


def subscribe_root_changed(callback: RootChangedCallback) -> None:
    """Register a listener for workspace root changes (sync or async)."""
    with _sub_lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe_root_changed(callback: RootChangedCallback) -> None:
    """Remove a previously registered root-change listener."""
    with _sub_lock:
        try:
            _subscribers.remove(callback)
        except ValueError:
            pass


def notify_root_changed(root: str | Path, *, reason: str = "switch") -> None:
    """Pin tools to *root* and notify all subscribers (e.g. MCP rebind).

    Safe to call from WorkspaceStore activation. Exceptions in subscribers
    are logged and do not roll back the pin.
    """
    path = Path(root).expanduser().resolve()
    configure_workspace(workspace=str(path))
    logger.info("[WorkspaceBinding] root=%s reason=%s", path, reason)

    with _sub_lock:
        subs = list(_subscribers)

    for cb in subs:
        try:
            result = cb(path, reason)
            # Fire-and-forget coroutine if subscriber is async
            if hasattr(result, "__await__"):
                try:
                    import asyncio

                    loop = asyncio.get_running_loop()
                    loop.create_task(result)  # type: ignore[arg-type]
                except RuntimeError:
                    # No running loop — sync contexts cannot await; log once.
                    logger.debug(
                        "[WorkspaceBinding] async subscriber skipped (no event loop): %s",
                        getattr(cb, "__name__", cb),
                    )
        except Exception as exc:
            logger.warning(
                "[WorkspaceBinding] subscriber %s failed: %s",
                getattr(cb, "__name__", cb),
                exc,
            )


def set_bound_mcp_root(root: Path | None) -> None:
    """Record the root currently bound into workspace-scoped MCP servers."""
    global _bound_mcp_root
    with _bound_mcp_lock:
        _bound_mcp_root = root.resolve() if root is not None else None


def get_bound_mcp_root() -> Path | None:
    """Return the last MCP-bound workspace root (for health/UI)."""
    with _bound_mcp_lock:
        return _bound_mcp_root
