"""Workspace Router — CRUD API for managing multiple project workspaces.

Endpoints
---------
GET    /api/workspaces              — list all registered workspaces
POST   /api/workspaces/create       — create a new workspace on the filesystem
POST   /api/workspaces/switch       — switch to a different workspace dynamically
POST   /api/workspaces/delete       — unregister (+ optional on-disk delete)
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = [
    "WorkspaceCreateRequest",
    "WorkspaceDeleteRequest",
    "WorkspaceSwitchRequest",
    "create_workspaces_router",
]


# ── Pydantic models ────────────────────────────────────────────────────

class WorkspaceCreateRequest(BaseModel):
    """Request body for creating/registering a workspace."""
    name: str
    path: str


class WorkspaceSwitchRequest(BaseModel):
    """Request body for switching active workspace."""
    workspace_id: str


class WorkspaceDeleteRequest(BaseModel):
    """Unregister a workspace; optionally wipe its directory on disk."""

    workspace_id: str
    delete_files: bool = Field(
        default=False,
        description="If true, also delete the workspace root directory (safe paths only).",
    )


def _clone_base_dirs() -> list[Path]:
    """Directories where cloned/user workspaces are allowed to be wiped."""
    bases: list[Path] = []
    env = os.environ.get("KAZMA_CLONE_DIR", "").strip()
    if env:
        bases.append(Path(env).expanduser().resolve())
    bases.append((Path.home() / "kazma-repos").resolve())
    # Optional extra allow-root for custom layouts
    allow = os.environ.get("KAZMA_WORKSPACE_ROOT", "").strip()
    if allow:
        bases.append(Path(allow).expanduser().resolve())
    return bases


def _is_safe_to_delete_files(root: Path) -> tuple[bool, str]:
    """Return (ok, reason). Refuse home, roots, and the Kazma monorepo."""
    try:
        resolved = root.expanduser().resolve()
    except Exception as exc:
        return False, f"Cannot resolve path: {exc}"

    if not resolved.exists():
        return True, "path already absent"  # registry delete still useful

    # Hard blocks
    home = Path.home().resolve()
    if resolved == home:
        return False, "refusing to delete home directory"
    if resolved == Path("/") or (
        len(str(resolved)) <= 3 and str(resolved)[1:3] in (":\\", ":/")
    ):
        return False, "refusing to delete filesystem root"
    # Never delete parent of home
    try:
        if home.parent.resolve() == resolved:
            return False, "refusing to delete parent of home"
    except Exception:
        pass

    # Never wipe the running Kazma monorepo (cwd or install root with our pyproject)
    try:
        cwd = Path.cwd().resolve()
        if resolved == cwd:
            return False, "refusing to delete process cwd (likely Kazma host)"
        pyproj = resolved / "pyproject.toml"
        if pyproj.is_file():
            text = pyproj.read_text(encoding="utf-8", errors="replace")[:2000]
            if 'name = "kazma"' in text or "name = 'kazma'" in text:
                return False, "refusing to delete Kazma monorepo (pyproject name=kazma)"
    except Exception:
        pass

    # Must live under an allowed clone/base directory
    for base in _clone_base_dirs():
        try:
            resolved.relative_to(base)
            # Also refuse deleting the base itself
            if resolved == base:
                return False, f"refusing to delete clone base {base}"
            return True, f"under {base}"
        except ValueError:
            continue

    return (
        False,
        "path is outside ~/kazma-repos (and KAZMA_CLONE_DIR / KAZMA_WORKSPACE_ROOT); "
        "unregister without delete_files, or move the clone under the allowlist",
    )


# ── Router factory ─────────────────────────────────────────────────────

def create_workspaces_router() -> APIRouter:
    """Return an APIRouter providing endpoints for workspace management."""

    router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])

    # ------------------------------------------------------------------
    # GET /api/workspaces
    # ------------------------------------------------------------------

    @router.get("")
    async def list_workspaces() -> JSONResponse:
        """Return all workspaces and indicate which one is currently active."""
        from kazma_core.stores import get_workspace_store

        try:
            ws_store = get_workspace_store()
            workspaces = ws_store.list_workspaces()
            active_ws = ws_store.get_active_workspace()
        except Exception as exc:
            logger.error("[workspaces] list_workspaces failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to retrieve workspaces.") from exc

        return JSONResponse({
            "status": "ok",
            "workspaces": workspaces,
            "active_workspace_id": active_ws["id"] if active_ws else None,
            "count": len(workspaces)
        })

    # ------------------------------------------------------------------
    # POST /api/workspaces/create
    # ------------------------------------------------------------------

    @router.post("/create", status_code=201)
    async def create_workspace(body: WorkspaceCreateRequest) -> JSONResponse:
        """Create a new workspace directory structure and register it in settings.db.

        Request body::

            {"name": "My New Project", "path": "g:/GitHubRepos/my-new-project"}
        """
        raw_name = body.name.strip()
        raw_path = body.path.strip()

        if not raw_name:
            raise HTTPException(status_code=422, detail="Workspace name must not be empty.")
        if not raw_path:
            raise HTTPException(status_code=422, detail="Workspace path must not be empty.")

        p = Path(raw_path)
        # Strict cross-platform root validation
        if not p.is_absolute():
            raise HTTPException(status_code=422, detail="Workspace path must be absolute.")

        resolved_path = p.resolve()

        # Prevent path-traversal exploits or suspicious references.
        # Block POSIX root and bare Windows drive roots (C:\, D:\, …).
        if (
            ".." in raw_path
            or ".." in str(resolved_path)
            or resolved_path == Path("/")
            or (len(str(resolved_path)) <= 3 and str(resolved_path)[1:3] == ":\\")
        ):
            raise HTTPException(status_code=403, detail="Suspicious path traversal attempt blocked.")

        # Optional confinement: if KAZMA_WORKSPACE_ROOT is set, the workspace
        # path must live beneath it. Opt-in hardening for multi-project setups.
        allow_root = os.environ.get("KAZMA_WORKSPACE_ROOT", "").strip()
        if allow_root:
            allow_resolved = Path(allow_root).resolve()
            try:
                resolved_path.relative_to(allow_resolved)
            except ValueError:
                raise HTTPException(
                    status_code=403,
                    detail="Workspace path is outside the allowed workspace root.",
                ) from None

        try:
            # Safely generate the directory structure
            os.makedirs(resolved_path, exist_ok=True)
            logger.info("[workspaces] Safely created or confirmed folder: %s", resolved_path)
        except Exception as exc:
            logger.error("[workspaces] Failed to create workspace directory: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to create workspace directory.") from exc

        from kazma_core.stores import get_workspace_store
        try:
            ws_store = get_workspace_store()
            record = ws_store.create_workspace(raw_name, str(resolved_path))
        except Exception as exc:
            logger.error("[workspaces] Failed to register workspace record: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to save workspace registration.") from exc

        return JSONResponse({
            "status": "ok",
            "workspace": record
        }, status_code=201)

    # ------------------------------------------------------------------
    # POST /api/workspaces/switch
    # ------------------------------------------------------------------

    @router.post("/switch")
    async def switch_workspace(body: WorkspaceSwitchRequest) -> JSONResponse:
        """Switch active workspace, reload configurations and align contexts."""
        ws_id = body.workspace_id.strip()
        if not ws_id:
            raise HTTPException(status_code=422, detail="Workspace ID must not be empty.")

        from kazma_core.stores import get_workspace_store
        from kazma_core.config_store import get_config_store

        ws_store = get_workspace_store()
        config_store = get_config_store()

        try:
            # Activate the workspace in the database
            success = ws_store.set_active_workspace(ws_id)
            if not success:
                raise HTTPException(status_code=404, detail=f"Workspace with ID {ws_id} not found.")

            # Retrieve active workspace info
            active_ws = ws_store.get_active_workspace()
            if not active_ws:
                raise HTTPException(status_code=500, detail="Failed to retrieve active workspace after activation.")

            root_path = active_ws["root_path"]

            # Set workspace.selected_path in ConfigStore
            config_store.set("workspace.selected_path", root_path, category="workspace")

            # Force ConfigStore to reload its configurations from the new directory root
            config_store.reload_from_root(root_path)

            logger.info("[workspaces] Successfully switched active workspace to %r at %s", active_ws["name"], root_path)

        except HTTPException:
            raise
        except Exception as exc:
            logger.error("[workspaces] Failed to switch workspace: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to switch workspace.") from exc

        return JSONResponse({
            "status": "ok",
            "active_workspace": active_ws
        })

    # ------------------------------------------------------------------
    # POST /api/workspaces/delete
    # ------------------------------------------------------------------

    @router.post("/delete")
    async def delete_workspace(body: WorkspaceDeleteRequest) -> JSONResponse:
        """Unregister a workspace; optionally delete files under clone dirs.

        Body::

            {"workspace_id": "...", "delete_files": true}

        Safety: on-disk delete is limited to paths under ``~/kazma-repos``
        (or ``KAZMA_CLONE_DIR`` / ``KAZMA_WORKSPACE_ROOT``). Never deletes
        home, drive roots, process cwd, or a tree whose pyproject is Kazma.
        """
        ws_id = (body.workspace_id or "").strip()
        if not ws_id:
            raise HTTPException(status_code=422, detail="workspace_id is required.")

        from kazma_core.stores import get_workspace_store
        from kazma_core.config_store import get_config_store
        from kazma_core.tools.file_write import configure_workspace

        store = get_workspace_store()
        record = store.get_workspace(ws_id)
        if not record:
            raise HTTPException(status_code=404, detail="Workspace not found.")

        root = Path(record["root_path"])
        was_active = bool(record.get("is_active"))
        files_deleted = False
        files_error: str | None = None

        if body.delete_files:
            ok, reason = _is_safe_to_delete_files(root)
            if not ok:
                raise HTTPException(
                    status_code=403,
                    detail=f"Cannot delete files: {reason}",
                )
            try:
                if root.exists():
                    shutil.rmtree(root)
                    files_deleted = True
                    logger.warning(
                        "[workspaces] Deleted on-disk workspace tree %s (%s)",
                        root,
                        reason,
                    )
            except Exception as exc:
                logger.exception("[workspaces] rmtree failed for %s", root)
                files_error = str(exc)[:300]
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to delete files: {files_error}",
                ) from exc

        deleted = store.delete_workspace(ws_id)
        if not deleted:
            raise HTTPException(status_code=500, detail="Failed to remove workspace record.")

        # If we removed the active workspace, activate another or clear pin
        active = store.get_active_workspace()
        if was_active:
            remaining = store.list_workspaces()
            if remaining:
                store.set_active_workspace(remaining[0]["id"])
                active = store.get_active_workspace()
                if active and active.get("root_path"):
                    try:
                        get_config_store().set(
                            "workspace.selected_path",
                            active["root_path"],
                            category="workspace",
                        )
                    except Exception:
                        pass
            else:
                # No workspaces left — pin tools to default data workspace
                fallback = Path.cwd() / "kazma-data" / "workspace"
                fallback.mkdir(parents=True, exist_ok=True)
                try:
                    configure_workspace(workspace=str(fallback))
                    get_config_store().set(
                        "workspace.selected_path",
                        str(fallback),
                        category="workspace",
                    )
                except Exception:
                    pass
                active = None

        logger.info(
            "[workspaces] Deleted workspace id=%s name=%s files=%s",
            ws_id,
            record.get("name"),
            files_deleted,
        )
        return JSONResponse({
            "status": "ok",
            "deleted": deleted,
            "files_deleted": files_deleted,
            "active_workspace": active,
            "active_workspace_id": active["id"] if active else None,
        })

    return router
