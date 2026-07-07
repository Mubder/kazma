"""Workspace router — project folder selection and file-tree scanning.

Endpoints
---------
POST /api/workspace/select
    Save the absolute path of a project folder to ConfigStore so the
    dashboard file browser always opens in the selected project.

GET  /api/workspace/tree
    Recursively scan the selected project folder using ``os.scandir`` and
    return a structured JSON node tree limited to ``max_depth`` levels.

Security
--------
All paths are resolved with ``Path.resolve()`` and validated to remain
inside the selected workspace root before any directory entry is emitted.
Path traversal attempts (e.g., ``../../etc``) are blocked with a 403
response.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Paths that are never included in tree scans ────────────────────────
_SKIP_NAMES = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    "node_modules", ".venv", "venv", ".env", ".tox",
    "dist", "build", ".eggs", "*.egg-info",
})


# ── Helpers ──────────────────────────────────────────────────────────────

def _is_within(target: Path, root: Path) -> bool:
    """Return True only if *target* (already resolved) is inside *root*."""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _scan_tree(
    directory: Path,
    root: Path,
    depth: int,
    max_depth: int,
) -> list[dict[str, Any]]:
    """Recursively scan *directory* up to *max_depth* levels.

    Args:
        directory: Directory to scan (must already be inside *root*).
        root: The authorised workspace root used for containment checks.
        depth: Current recursion depth (starts at 0 at the workspace root).
        max_depth: Maximum recursion depth (inclusive).

    Returns:
        A sorted list of node dicts with the following keys:
        ``name``, ``path`` (relative to root, forward slashes),
        ``is_dir``, ``size`` (bytes or ``None`` for dirs), ``children``
        (list of nodes or ``None`` for files and dirs at max depth).
    """
    nodes: list[dict[str, Any]] = []
    try:
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
    except (PermissionError, OSError) as exc:
        logger.debug("[workspace/tree] Cannot scan %s: %s", directory, exc)
        return nodes

    for entry in entries:
        # Skip hidden files/dirs and known noise directories
        if entry.name.startswith(".") or entry.name in _SKIP_NAMES:
            continue

        entry_path = Path(entry.path).resolve()

        # Containment guard — skip symlinks that escape the root
        if not _is_within(entry_path, root):
            logger.warning(
                "[workspace/tree] Skipping escaped path: %s (root=%s)", entry_path, root
            )
            continue

        rel_path = str(entry_path.relative_to(root)).replace("\\", "/")

        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue

        if is_dir:
            children: list[dict[str, Any]] | None
            if depth < max_depth:
                children = _scan_tree(entry_path, root, depth + 1, max_depth)
            else:
                children = None  # indicates "has children but not expanded"
            nodes.append(
                {
                    "name": entry.name,
                    "path": rel_path,
                    "is_dir": True,
                    "size": None,
                    "children": children,
                }
            )
        else:
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = None
            nodes.append(
                {
                    "name": entry.name,
                    "path": rel_path,
                    "is_dir": False,
                    "size": size,
                    "children": None,
                }
            )

    return nodes


# ── Pydantic model ────────────────────────────────────────────────────────

class WorkspaceSelectRequest(BaseModel):
    """Request body for POST /api/workspace/select."""
    path: str


# ── Router factory ────────────────────────────────────────────────────────

def create_workspace_select_router() -> APIRouter:
    """Return an APIRouter providing workspace selection and tree scan endpoints."""

    router = APIRouter(prefix="/api/workspace", tags=["workspace"])

    # ------------------------------------------------------------------
    # POST /api/workspace/select
    # ------------------------------------------------------------------

    @router.post("/select")
    async def select_workspace(body: WorkspaceSelectRequest) -> dict[str, Any]:
        """Persist the selected project folder path into ConfigStore.

        The path must be an absolute, existing directory.  On success the
        key ``workspace.selected_path`` is updated in the singleton
        ConfigStore.

        Returns:
            ``{"status": "ok", "path": "<resolved absolute path>"}``

        Raises:
            422: If the path is not absolute or does not exist.
        """
        raw = body.path.strip()
        if not raw:
            raise HTTPException(status_code=422, detail="Path must not be empty.")

        p = Path(raw)
        if not p.is_absolute():
            raise HTTPException(status_code=422, detail="Path must be absolute.")

        resolved = p.resolve()
        if not resolved.exists():
            raise HTTPException(status_code=422, detail=f"Path does not exist: {resolved}")
        if not resolved.is_dir():
            raise HTTPException(status_code=422, detail=f"Path is not a directory: {resolved}")

        try:
            from kazma_core.config_store import get_config_store
            get_config_store().set("workspace.selected_path", str(resolved), category="workspace")
        except Exception as exc:
            logger.error("[workspace/select] Failed to persist path: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to save workspace path.") from exc

        logger.info("[workspace/select] Workspace set to: %s", resolved)
        return {"status": "ok", "path": str(resolved)}

    # ------------------------------------------------------------------
    # GET /api/workspace/tree
    # ------------------------------------------------------------------

    @router.get("/tree")
    async def workspace_tree(max_depth: int = 3) -> JSONResponse:
        """Return a recursive directory tree for the active project folder.

        Query params:
            max_depth — maximum recursion depth (default 3, capped at 8).

        The selected path is read from ``ConfigStore`` key
        ``workspace.selected_path``.  If no path has been configured a
        helpful 404 is returned.

        All paths in the response are relative to the workspace root and
        use forward slashes.

        Returns:
            ``{"root": "<absolute path>", "tree": [...]}``
        """
        max_depth = min(max(1, max_depth), 8)

        try:
            from kazma_core.stores import get_workspace_store
            active_ws = get_workspace_store().get_active_workspace()
            if active_ws:
                raw_root = active_ws["root_path"]
            else:
                from kazma_core.config_store import get_config_store
                raw_root = get_config_store().get("workspace.selected_path")
        except Exception as exc:
            logger.error("[workspace/tree] WorkspaceStore/ConfigStore unavailable: %s", exc)
            raise HTTPException(status_code=500, detail="Store unavailable.") from exc

        if not raw_root:
            raise HTTPException(
                status_code=404,
                detail="No workspace selected. POST /api/workspace/select first.",
            )

        root = Path(str(raw_root)).resolve()
        if not root.exists() or not root.is_dir():
            raise HTTPException(
                status_code=404,
                detail=f"Selected workspace path no longer exists: {root}",
            )

        tree = _scan_tree(root, root, depth=0, max_depth=max_depth)
        return JSONResponse({"root": str(root), "tree": tree})

    return router
