"""Workspace Router — CRUD API for managing multiple project workspaces.

Endpoints
---------
GET    /api/workspaces          — list all registered workspaces
POST   /api/workspaces/create   — create a new workspace on the filesystem
POST   /api/workspaces/switch   — switch to a different workspace dynamically
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


# ── Pydantic models ────────────────────────────────────────────────────

class WorkspaceCreateRequest(BaseModel):
    """Request body for creating/registering a workspace."""
    name: str
    path: str


class WorkspaceSwitchRequest(BaseModel):
    """Request body for switching active workspace."""
    workspace_id: str


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

        # Prevent path-traversal exploits or suspicious references
        if ".." in raw_path or ".." in str(resolved_path) or resolved_path == Path("/"):
            raise HTTPException(status_code=403, detail="Suspicious path traversal attempt blocked.")

        try:
            # Safely generate the directory structure
            os.makedirs(resolved_path, exist_ok=True)
            logger.info("[workspaces] Safely created or confirmed folder: %s", resolved_path)
        except Exception as exc:
            logger.error("[workspaces] Failed to create workspace directory: %s", exc)
            raise HTTPException(status_code=500, detail=f"Failed to create workspace directory: {exc}") from exc

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
            raise HTTPException(status_code=500, detail=f"Failed to switch workspace: {exc}") from exc

        return JSONResponse({
            "status": "ok",
            "active_workspace": active_ws
        })

    return router
