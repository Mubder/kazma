"""kazma_core.stores — Persistent data stores for Kazma core services.

Exports
-------
- :class:`~kazma_core.stores.bookmarks.BookmarkStore` — SQLite-backed
  bookmark persistence.
- :func:`~kazma_core.stores.bookmarks.get_bookmark_store` — process-wide
  singleton accessor.
- :func:`~kazma_core.stores.bookmarks.reset_bookmark_store` — singleton
  teardown helper (primarily for tests).
"""

from __future__ import annotations

from kazma_core.stores.bookmarks import (
    BookmarkStore,
    get_bookmark_store,
    reset_bookmark_store,
)
from kazma_core.stores.workspaces import (
    WorkspaceStore,
    get_workspace_store,
    reset_workspace_store,
)

__all__ = [
    "BookmarkStore",
    "get_bookmark_store",
    "reset_bookmark_store",
    "WorkspaceStore",
    "get_workspace_store",
    "reset_workspace_store",
]

