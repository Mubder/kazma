"""Bookmark router — the Workspace page's bookmarks.

Endpoints
---------
GET    /api/bookmarks              — list all bookmarks
POST   /api/bookmarks              — create a bookmark
DELETE /api/bookmarks/{id}         — delete a bookmark

The backing store is :class:`~kazma_core.stores.bookmarks.BookmarkStore`
which shares ``kazma-data/settings.db`` with ConfigStore. Every route is a
plain ``def``: the store is SQLite, so FastAPI runs them in its threadpool,
never on the event loop. A ``GET``/``PATCH`` of one bookmark were removed on
2026-10-01: nothing called them (``tests/test_api_route_callers.py``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from kazma_core.errors import validation_error

logger = logging.getLogger(__name__)

__all__ = [
    "BookmarkCreateRequest",
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


# ── Router factory ─────────────────────────────────────────────────────

def create_bookmarks_router() -> APIRouter:
    """Return an APIRouter providing the bookmark endpoints."""

    router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])

    # ------------------------------------------------------------------
    # GET /api/bookmarks
    # ------------------------------------------------------------------

    @router.get("")
    def list_bookmarks() -> JSONResponse:
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
    def create_bookmark(body: BookmarkCreateRequest) -> JSONResponse:
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
            raise HTTPException(status_code=422, detail=validation_error(exc)) from exc
        except Exception as exc:
            logger.error("[bookmarks] create_bookmark failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to create bookmark.") from exc
        return JSONResponse({"bookmark": record}, status_code=201)

    # ------------------------------------------------------------------
    # DELETE /api/bookmarks/{bookmark_id}
    # ------------------------------------------------------------------

    @router.delete("/{bookmark_id}", status_code=204)
    def delete_bookmark(bookmark_id: int) -> None:
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
