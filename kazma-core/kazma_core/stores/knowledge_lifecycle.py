"""What deleting a Knowledge Library takes with it, and who may use one.

One place for the library's lifecycle, called by every surface that deletes
a library (the Web API, the agent's tool):

- **A running crawl stops the delete** (Knowledge audit KB-E02): the crawl
  owns the library, and a delete under it left every later page write to
  fail -- reported as "unchanged" -- while the crawl kept fetching.
  :class:`KnowledgeLibraryBusy` answers; the crawl itself also stops at the
  next page when its library is gone.
- **The Documents platform is told** (KB-E08): every document the library
  holds is unindexed from it through the Documents service, as the
  document's owner, so the documents store no longer records it as
  searchable there. A library holding documents is not deleted while the
  Documents service cannot be told (:class:`KnowledgeDocumentsUnavailable`).
- **Every copy goes** (KB-E08): the meaning vectors (its collection), the
  chunks and their keyword-index rows (in bounded batches), and the fetched
  pages kept with it (``knowledge_provenance/<library>``). The memory
  copies an older build made are retired by Memory's sweep (M04); nothing
  writes them any more.

The Knowledge Library works on a single-operator install (decision D1,
KB-S04): libraries, chunks and crawl jobs are not separated per user, so an
install with platform users refuses every Knowledge surface with
:data:`MULTI_USER_REFUSAL` until per-user libraries are built.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "MULTI_USER_REFUSAL",
    "KnowledgeDocumentsUnavailable",
    "KnowledgeLibraryBusy",
    "delete_library",
    "kb_refusal",
]

logger = logging.getLogger(__name__)

MULTI_USER_REFUSAL = (
    "The Knowledge Library works on a single-operator install. This install has "
    "platform users, and libraries are not separated per user yet, so the Knowledge "
    "Library is off here."
)


class KnowledgeLibraryBusy(RuntimeError):
    """A crawl owns the library; the delete waits for it (KB-E02)."""

    def __init__(self, library_id: str, jobs: list[str]) -> None:
        super().__init__(f"A crawl of library {library_id!r} is running; delete it when the crawl finishes.")
        self.library_id = library_id
        self.jobs = jobs


class KnowledgeDocumentsUnavailable(RuntimeError):
    """The library holds documents and the Documents platform could not be
    told it is going (KB-E08)."""


def kb_refusal() -> str | None:
    """:data:`MULTI_USER_REFUSAL` when the Knowledge Library must refuse,
    else None -- the shared single-operator rule
    (``security.single_operator``; a check that cannot answer refuses).
    Blocking: async callers use ``asyncio.to_thread``."""
    from kazma_core.security.single_operator import single_operator_refusal

    return single_operator_refusal(MULTI_USER_REFUSAL)


def delete_library(library_id: str) -> dict[str, Any]:
    """Delete a library and everything derived from it. Blocking.

    Returns ``{"deleted": bool, "documents": n, "provenance_removed": bool}``;
    ``deleted`` False means no such library. Raises
    :class:`KnowledgeLibraryBusy` while a crawl owns it and
    :class:`KnowledgeDocumentsUnavailable` when its documents could not be
    unindexed through the Documents platform.
    """
    from kazma_core.stores.knowledge import get_knowledge_store
    from kazma_core.stores.knowledge_index import get_knowledge_index
    from kazma_core.stores.knowledge_ingest import delete_provenance
    from kazma_core.web_acquire.crawl_jobs import active_for_library

    store = get_knowledge_store()
    if not store.library_exists(library_id):
        return {"deleted": False, "documents": 0, "provenance_removed": True}
    running = active_for_library(library_id)
    if running:
        raise KnowledgeLibraryBusy(library_id, running)
    documents = store.library_document_ids(library_id)
    if documents:
        _unindex_documents(library_id, documents)
    deleted = get_knowledge_index().delete_library(library_id)
    removed = delete_provenance(library_id)
    logger.info("[knowledge] library %s deleted (%d document(s) unindexed, fetched pages %s)",
                library_id, len(documents), "removed" if removed else "NOT removed")
    return {"deleted": deleted, "documents": len(documents), "provenance_removed": removed}


def _unindex_documents(library_id: str, documents: list[tuple[str, str]]) -> None:
    try:
        from kazma_core.documents.ingestion import get_ingestion_service
        from kazma_core.documents.models import DocumentId

        service = get_ingestion_service()
        for tenant, document in documents:
            record = service.repository.get_document(tenant_id=tenant, document_id=DocumentId(document))
            if record is None:
                continue  # the documents store no longer knows it: nothing to tell
            service.unindex_document(tenant_id=tenant, actor_id=record.owner_id,
                                     document_id=document, library_id=library_id)
    except Exception as exc:
        raise KnowledgeDocumentsUnavailable(
            f"The library holds documents and the Documents platform could not be told: "
            f"{type(exc).__name__}"
        ) from exc
