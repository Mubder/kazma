"""kazma_core.stores — Persistent data stores for Kazma core services.

Exports
-------
- :class:`~kazma_core.stores.bookmarks.BookmarkStore` — SQLite-backed
  bookmark persistence.
- :func:`~kazma_core.stores.bookmarks.get_bookmark_store` — process-wide
  singleton accessor.
- :func:`~kazma_core.stores.bookmarks.reset_bookmark_store` — singleton
  teardown helper (primarily for tests).
- :class:`~kazma_core.stores.workspaces.WorkspaceStore` /
  :func:`~kazma_core.stores.workspaces.get_workspace_store` — workspace registry.
- :class:`~kazma_core.stores.knowledge.KnowledgeStore` /
  :func:`~kazma_core.stores.knowledge.get_knowledge_store` — Knowledge
  Library + chunk persistence (see ``docs/docs/guide/knowledge-library.md``).
- :class:`~kazma_core.stores.knowledge_index.KnowledgeIndex` /
  :func:`~kazma_core.stores.knowledge_index.get_knowledge_index` —
  per-library retrieval engine (ChromaDB + FTS5 + RRF).
- :func:`~kazma_core.stores.knowledge_chunker.chunk_markdown_doc` —
  hierarchy-aware markdown chunker for ingestion.
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
from kazma_core.stores.knowledge import (
    KnowledgeStore,
    get_knowledge_store,
    reset_knowledge_store,
)
from kazma_core.stores.knowledge_index import (
    KnowledgeIndex,
    get_knowledge_index,
    reset_knowledge_index,
)
from kazma_core.stores.knowledge_chunker import (
    KnowledgeChunk,
    chunk_markdown_doc,
)

__all__ = [
    "BookmarkStore",
    "get_bookmark_store",
    "reset_bookmark_store",
    "WorkspaceStore",
    "get_workspace_store",
    "reset_workspace_store",
    "KnowledgeStore",
    "get_knowledge_store",
    "reset_knowledge_store",
    "KnowledgeIndex",
    "get_knowledge_index",
    "reset_knowledge_index",
    "KnowledgeChunk",
    "chunk_markdown_doc",
]


