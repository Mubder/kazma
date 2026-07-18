"""Bookmark router — CRUD API for project bookmarks.

Endpoints
---------
GET    /api/bookmarks              — list all bookmarks
POST   /api/bookmarks              — create a bookmark
GET    /api/bookmarks/{id}         — fetch a single bookmark
PATCH  /api/bookmarks/{id}         — update a bookmark (partial)
DELETE /api/bookmarks/{id}         — delete a bookmark

The backing store is :class:`~kazma_core.stores.bookmarks.BookmarkStore`
which shares ``kazma-data/settings.db`` with ConfigStore.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

__all__ = [
    "BookmarkCreateRequest",
    "BookmarkUpdateRequest",
    "create_bookmarks_router",
]


# ── Pydantic models ────────────────────────────────────────────────────

class BookmarkCreateRequest(BaseModel):
    """Request body for creating a bookmark."""
    name: str
    type: str = "file"
    target: str

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("file", "url"):
            raise ValueError("type must be 'file' or 'url'")
        return v


class BookmarkUpdateRequest(BaseModel):
    """Request body for updating a bookmark (all fields optional)."""
    name: str | None = None
    type: str | None = None
    target: str | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ("file", "url"):
            raise ValueError("type must be 'file' or 'url'")
        return v


# ── Router factory ─────────────────────────────────────────────────────

def create_bookmarks_router() -> APIRouter:
    """Return an APIRouter providing CRUD endpoints for bookmarks."""

    router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])

    # ------------------------------------------------------------------
    # GET /api/bookmarks
    # ------------------------------------------------------------------

    @router.get("")
    async def list_bookmarks() -> JSONResponse:
        """Return all bookmarks ordered by creation ID."""
        from kazma_core.stores import get_bookmark_store

        try:
            bookmarks = get_bookmark_store().list_bookmarks()
        except Exception as exc:
            logger.error("[bookmarks] list_bookmarks failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to retrieve bookmarks.") from exc
        return JSONResponse({"bookmarks": bookmarks, "count": len(bookmarks)})

    # ------------------------------------------------------------------
    # POST /api/bookmarks
    # ------------------------------------------------------------------

    @router.post("", status_code=201)
    async def create_bookmark(body: BookmarkCreateRequest) -> JSONResponse:
        """Create a new bookmark.

        Request body::

            {"name": "Kazma Core", "type": "file", "target": "/path/to/project"}

        Returns the created bookmark with its assigned ``id``.
        """
        from kazma_core.stores import get_bookmark_store

        try:
            record = get_bookmark_store().create_bookmark(
                name=body.name,
                type_str=body.type,
                target=body.target,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("[bookmarks] create_bookmark failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to create bookmark.") from exc
        return JSONResponse({"bookmark": record}, status_code=201)

    # ------------------------------------------------------------------
    # GET /api/bookmarks/{bookmark_id}
    # ------------------------------------------------------------------

    @router.get("/{bookmark_id}")
    async def get_bookmark(bookmark_id: int) -> JSONResponse:
        """Retrieve a single bookmark by ID."""
        from kazma_core.stores import get_bookmark_store

        try:
            record: dict[str, Any] | None = get_bookmark_store().get_bookmark(bookmark_id)
        except Exception as exc:
            logger.error("[bookmarks] get_bookmark failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to retrieve bookmark.") from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"Bookmark {bookmark_id} not found.")
        return JSONResponse({"bookmark": record})

    # ------------------------------------------------------------------
    # PATCH /api/bookmarks/{bookmark_id}
    # ------------------------------------------------------------------

    @router.patch("/{bookmark_id}")
    async def update_bookmark(bookmark_id: int, body: BookmarkUpdateRequest) -> JSONResponse:
        """Partially update a bookmark.  Only provided fields are changed."""
        from kazma_core.stores import get_bookmark_store

        try:
            record = get_bookmark_store().update_bookmark(
                bookmark_id,
                name=body.name,
                type_str=body.type,
                target=body.target,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("[bookmarks] update_bookmark failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to update bookmark.") from exc
        if record is None:
            raise HTTPException(status_code=404, detail=f"Bookmark {bookmark_id} not found.")
        return JSONResponse({"bookmark": record})

    # ------------------------------------------------------------------
    # DELETE /api/bookmarks/{bookmark_id}
    # ------------------------------------------------------------------

    @router.delete("/{bookmark_id}", status_code=204)
    async def delete_bookmark(bookmark_id: int) -> None:
        """Delete a bookmark by ID.  Returns 204 No Content on success."""
        from kazma_core.stores import get_bookmark_store

        try:
            deleted = get_bookmark_store().delete_bookmark(bookmark_id)
        except Exception as exc:
            logger.error("[bookmarks] delete_bookmark failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to delete bookmark.") from exc
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Bookmark {bookmark_id} not found.")

    return router
