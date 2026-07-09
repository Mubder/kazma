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
        """Select a project folder as the active workspace.

        The path must be an absolute, existing directory. The folder is
        registered as a workspace (if not already) and **activated** so
        Project Files, Git Status, and GitHub Telemetry all follow it
        atomically (same code path as ``/api/workspaces/switch``).

        Returns:
            ``{"status": "ok", "path": "<resolved absolute path>", "workspace_id": "..."}``

        Raises:
            422: If the path is not absolute or does not exist.
            403: If the path is outside KAZMA_WORKSPACE_ROOT (when set).
        """
        raw = body.path.strip()
        if not raw:
            raise HTTPException(status_code=422, detail="Path must not be empty.")

        p = Path(raw)
        if not p.is_absolute():
            raise HTTPException(status_code=422, detail="Path must be absolute.")

        resolved = p.resolve()
        if not resolved.exists():
            raise HTTPException(status_code=422, detail="Path does not exist.")
        if not resolved.is_dir():
            raise HTTPException(status_code=422, detail="Path is not a directory.")

        # Optional confinement: if KAZMA_WORKSPACE_ROOT is set, the selected
        # path must live beneath it. Opt-in hardening for multi-project setups.
        allow_root = os.environ.get("KAZMA_WORKSPACE_ROOT", "").strip()
        if allow_root:
            allow_resolved = Path(allow_root).resolve()
            try:
                resolved.relative_to(allow_resolved)
            except ValueError:
                raise HTTPException(
                    status_code=403,
                    detail="Path is outside the allowed workspace root.",
                ) from None

        from kazma_core.config_store import get_config_store
        from kazma_core.stores import get_workspace_store

        try:
            store = get_workspace_store()
            config_store = get_config_store()

            # Reuse an existing workspace whose root matches, else register it.
            ws_id = None
            for ws in store.list_workspaces():
                if Path(str(ws.get("root_path", ""))).resolve() == resolved:
                    ws_id = ws["id"]
                    break
            if ws_id is None:
                name = resolved.name or "workspace"
                record = store.create_workspace(name, str(resolved))
                ws_id = record["id"]

            # Activate it — this flips is_active, sets selected_path, and
            # reloads config from the new root. All 3 cards follow this.
            store.set_active_workspace(ws_id)
            config_store.set("workspace.selected_path", str(resolved), category="workspace")
            try:
                config_store.reload_from_root(str(resolved))
            except Exception:
                logger.debug("[workspace/select] reload_from_root failed", exc_info=True)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("[workspace/select] Failed to activate workspace: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to set workspace.") from exc

        logger.info("[workspace/select] Active workspace set to: %s (id=%s)", resolved, ws_id)
        return {"status": "ok", "path": str(resolved), "workspace_id": ws_id}

    # ------------------------------------------------------------------
    # GET /api/workspace/suggest — filesystem autocomplete for the path input
    # ------------------------------------------------------------------

    @router.get("/suggest")
    async def suggest_dirs(path: str = "") -> JSONResponse:
        """Return up to 15 child directories matching the typed path prefix.

        Used by the "Select Folder" input for click-to-navigate autocomplete
        (browsers can't open a native OS folder dialog from a web page).
        Respects the optional ``KAZMA_WORKSPACE_ROOT`` confinement.

        Filtering: the last path segment the user is typing becomes a
        prefix filter — e.g. typing ``G:/Git`` resolves the parent ``G:/``
        and filters children starting with ``Git``.
        """
        raw = (path or "").strip()
        if not raw:
            return JSONResponse({"suggestions": []})

        # Split into the existing parent dir + the partial segment being typed.
        p = Path(raw)
        typed_segment = p.name.lower()  # the part the user is currently typing
        try:
            resolved = p.resolve()
        except Exception:
            return JSONResponse({"suggestions": []})

        # If the full typed path is an existing dir, list its children (no
        # prefix filter — the user completed a valid dir). Otherwise list the
        # parent's children filtered by the typed segment.
        if resolved.is_dir():
            base = resolved
            prefix = ""
        else:
            base = resolved.parent
            prefix = typed_segment

        # Confinement check.
        allow_root = os.environ.get("KAZMA_WORKSPACE_ROOT", "").strip()
        if allow_root:
            try:
                base.relative_to(Path(allow_root).resolve())
            except ValueError:
                return JSONResponse({"suggestions": []})

        suggestions: list[dict[str, str]] = []
        try:
            for child in sorted(base.iterdir(), key=lambda c: c.name.lower()):
                if len(suggestions) >= 15:
                    break
                if not child.is_dir() or child.name.startswith("."):
                    continue
                # Prefix-filter on the typed segment (case-insensitive).
                if prefix and not child.name.lower().startswith(prefix):
                    continue
                suggestions.append({"name": child.name, "path": str(child)})
        except (PermissionError, OSError):
            pass
        return JSONResponse({"suggestions": suggestions})

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
