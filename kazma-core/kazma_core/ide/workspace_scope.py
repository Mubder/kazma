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

logger = logging.getLogger(__name__)

# The ContextVar carries the active workspace_id (or None for "use global").
_current_workspace_id: ContextVar[str | None] = ContextVar(
    "kazma_workspace_id", default=None
)


def current_workspace_id() -> str | None:
    """Return the workspace_id active in the current task scope, or None."""
    try:
        return _current_workspace_id.get()
    except LookupError:
        return None


def resolve_workspace_root() -> Path | None:
    """Resolve the workspace root for the current scope.

    When a ``workspace_scope`` is active, returns that workspace's root
    path. Returns None when no scope is active (caller falls back to the
    global active workspace via the usual resolution).
    """
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
