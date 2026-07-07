"""kazma_gateway.routers — FastAPI router modules for the Kazma gateway.

Provides factory functions that return mounted APIRouters for:
- Workspace directory selection and tree scanning.
- Live git status for the active workspace.
- Bookmark CRUD operations.
"""

from __future__ import annotations

from kazma_gateway.routers.bookmarks import create_bookmarks_router
from kazma_gateway.routers.git import create_git_router
from kazma_gateway.routers.workspace import create_workspace_select_router

__all__ = [
    "create_bookmarks_router",
    "create_git_router",
    "create_workspace_select_router",
]
