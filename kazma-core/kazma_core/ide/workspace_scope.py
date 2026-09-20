"""Per-task workspace targeting (Phase 3).

Before this module, every tool that resolves the workspace (``file_write``,
``shell_exec``, the git/github tools) read the **process-wide** active
workspace via ``WorkspaceStore`` or ``KAZMA_WORKSPACE``. That meant two
concurrent swarm tasks targeting two different repos would collide — the
second global switch silently re-pointed the first task's tool calls.

This module introduces a ``ContextVar`` carrying an optional ``workspace_id``
that, when set, overrides the global active workspace for the duration of a
task's execution. The pattern:

    async with workspace_scope(task.workspace_id):
        result = await worker.dispatch(...)

Inside the scope, ``current_workspace_id()`` returns the id, and
``resolve_workspace_root()`` returns that workspace's root path. The
file/exec/git tools consult ``resolve_workspace_root()`` and pin to it.
When no scope is active (the common, single-workspace case), they fall
back to the global active workspace — fully backward compatible.

Why a ContextVar and not a thread-local:
    The swarm dispatch path is fully ``async`` and may span
    ``asyncio.gather`` boundaries. ``ContextVar`` propagates correctly
    across ``await`` points within one task, which a ``threading.local``
    would not. (Note: explicit propagation is still required when spawning
    *new* asyncio tasks — ``asyncio.create_task`` copies the context, so
    the var travels with it.)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import AsyncIterator

__all__ = [
    "current_workspace_id",
    "current_workspace_path",
    "pin_workspace",
    "pin_workspace_path",
    "reset_workspace",
    "reset_workspace_path",
    "resolve_workspace_root",
    "workspace_path_scope",
    "workspace_scope",
]

logger = logging.getLogger(__name__)

# The ContextVar carries the active workspace_id (or None for "use global").
_current_workspace_id: ContextVar[str | None] = ContextVar(
    "kazma_workspace_id", default=None
)

#: A workspace root given as a PATH rather than a registered workspace id.
#:
#: The id-based scope above requires a row in ``WorkspaceStore``. Some callers
#: declare their scope as a bare directory and have no row to point at — the
#: IDE MCP server is the one that mattered: it is constructed as
#: ``MCPServer(root=...)`` and documents that root as confinement, but every
#: tool it dispatches went through ``IdeService``, whose ``root`` property
#: re-resolves from the process-wide active workspace on every access. The
#: server's own root was therefore advisory for three of its seven tools
#: (``list_files``, ``run_command``, ``git_status`` did not even read the
#: parameter) and fought IdeService's root for the rest. Two confinement
#: tests were marked ``xfail`` over it and shipped that way.
#:
#: The fix is a rung on the SAME ladder, not a second mechanism: a path scope
#: sits beside the id scope at precedence 1 in
#: :func:`kazma_core.workspace.binding.resolve_active_root`, so a caller that
#: declares a root gets it honoured by every tool that already consults the
#: SoT — which is all of them. Inventing a parallel "MCP root" check in the
#: MCP server would have been the second precedence ladder that
#: ``_resolve_workspace_root`` explicitly warns against.
_current_workspace_path: ContextVar[str | None] = ContextVar(
    "kazma_workspace_path", default=None
)


def current_workspace_id() -> str | None:
    """Return the workspace_id active in the current task scope, or None."""
    try:
        return _current_workspace_id.get()
    except LookupError:
        return None


def pin_workspace(workspace_id: str | None):
    """Sync pin for SSE/WS turns. Returns a reset token or None."""
    if not workspace_id:
        return None
    return _current_workspace_id.set(str(workspace_id))


def reset_workspace(token) -> None:
    if token is None:
        return
    _current_workspace_id.reset(token)


def current_workspace_path() -> str | None:
    """Return the workspace PATH pinned in the current scope, or None."""
    try:
        return _current_workspace_path.get()
    except LookupError:
        return None


def pin_workspace_path(path: str | Path | None):
    """Sync pin for a path-declared workspace. Returns a token or None."""
    if not path:
        return None
    return _current_workspace_path.set(str(Path(path).expanduser().resolve()))


def reset_workspace_path(token) -> None:
    if token is None:
        return
    _current_workspace_path.reset(token)


@asynccontextmanager
async def workspace_path_scope(path: str | Path | None) -> AsyncIterator[None]:
    """Pin a workspace ROOT PATH for the duration of the block.

    The path-flavoured twin of :func:`workspace_scope`, for callers whose
    scope is a directory rather than a ``WorkspaceStore`` id. No-op when
    *path* is falsy, so callers may wrap unconditionally.
    """
    token = pin_workspace_path(path)
    if token is None:
        yield
        return
    try:
        yield
    finally:
        reset_workspace_path(token)


def resolve_workspace_root() -> Path | None:
    """Resolve the workspace root for the current scope.

    When a ``workspace_scope`` is active, returns that workspace's root
    path. Returns None when no scope is active (caller falls back to the
    global active workspace via the usual resolution).

    An explicit PATH scope wins over an id scope. A caller that names a
    concrete directory has been more specific than one that names a row
    whose ``root_path`` could be edited underneath it, and the path scope
    is what a confinement claim is made of.
    """
    ws_path = current_workspace_path()
    if ws_path:
        return Path(ws_path)
    ws_id = current_workspace_id()
    if not ws_id:
        return None
    try:
        from kazma_core.stores import get_workspace_store

        for ws in get_workspace_store().list_workspaces():
            if ws.get("id") == ws_id:
                rp = ws.get("root_path")
                if rp:
                    return Path(rp).resolve()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[workspace_scope] resolve for %s failed: %s", ws_id, exc)
    return None


@asynccontextmanager
async def workspace_scope(workspace_id: str | None) -> AsyncIterator[None]:
    """Async context manager that pins a workspace_id for the duration.

    Usage::

        async with workspace_scope(task.workspace_id):
            await worker.dispatch(...)

    When ``workspace_id`` is None this is a no-op (preserves the global
    active workspace) — so callers can always wrap unconditionally.
    """
    if not workspace_id:
        # No targeting → run with whatever the caller's context already had.
        yield
        return
    token = _current_workspace_id.set(workspace_id)
    logger.debug("[workspace_scope] entered scope for workspace %s", workspace_id)
    try:
        yield
    finally:
        _current_workspace_id.reset(token)
        logger.debug("[workspace_scope] exited scope for workspace %s", workspace_id)
