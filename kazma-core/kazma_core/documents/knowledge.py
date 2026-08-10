"""Tenant-safe adapter between DocumentIR and the existing Knowledge stack."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kazma_core.safety.prompt_fence import format_untrusted_block
from kazma_core.stores.knowledge import KnowledgeStore
from kazma_core.stores.knowledge_index import KnowledgeHit, KnowledgeIndex

from .config import DocumentConfig
from .indexer import chunk_document_ir
from .models import DocumentIR, DocumentResult
from .repository import DocumentAccessError, DocumentRepository

__all__ = [
    "DocumentIndexResult",
    "DocumentKnowledgeAdapter",
    "DocumentSearchResult",
    "format_document_hits",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentIndexResult:
    library_id: str
    chunk_count: int
    published: bool
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "library_id": self.library_id,
            "chunk_count": self.chunk_count,
            "published": self.published,
            "source_url": self.source_url,
        }


@dataclass(frozen=True, slots=True)
class DocumentSearchResult:
    hits: tuple[KnowledgeHit, ...]
    prompt_context: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [
                {
                    "chunk_id": hit.chunk_id,
                    "score": hit.score,
                    "library_id": hit.library_id,
                    "source_url": hit.source_url,
                    "document_id": hit.document_id,
                    "version_id": hit.version_id,
                    "citation_label": hit.citation_label,
                    "content": hit.content,
                    "metadata": hit.metadata,
                }
                for hit in self.hits
            ],
            "prompt_context": self.prompt_context,
        }


def format_document_hits(hits: list[KnowledgeHit] | tuple[KnowledgeHit, ...]) -> str:
    """Format citations and content inside exactly one untrusted-data fence."""
    if not hits:
        return ""
    body = "\n\n".join(
        f"[{hit.citation_label or hit.source_url}]\n{hit.content}" for hit in hits
    )
    return format_untrusted_block(body, source="document_knowledge")


class DocumentKnowledgeAdapter:
    """Focused publication/search API; no independent embedder or vector store."""

    def __init__(
        self,
        *,
        repository: DocumentRepository,
        knowledge_store: KnowledgeStore,
        knowledge_index: KnowledgeIndex | None = None,
        config: DocumentConfig,
    ) -> None:
        self.repository = repository
        self.store = knowledge_store
        self.index = knowledge_index or KnowledgeIndex(store=knowledge_store)
        self.config = config

    def index_document_ir(
        self,
        document: DocumentIR,
        *,
        tenant_id: str,
        actor_id: str,
        library_id: str,
    ) -> DocumentResult[DocumentIndexResult]:
        try:
            record = self.repository.get_document(
                tenant_id=tenant_id,
                document_id=document.document_id,
                actor_id=actor_id,
            )
            version = self.repository.get_version(
                tenant_id=tenant_id,
                version_id=document.version_id,
                actor_id=actor_id,
            )
            if record is None or version is None or version.document_id != document.document_id:
                raise DocumentAccessError("document version is unavailable")
            if record.current_version_id != document.version_id:
                raise ValueError("only the document's current immutable version may be activated")
            if self.store.get_library_for_tenant(library_id, tenant_id) is None:
                raise DocumentAccessError("knowledge library is unavailable")
            chunks = list(chunk_document_ir(document, self.config))
            if not chunks:
                raise ValueError("DocumentIR produced no indexable structural chunks")
            if any(chunk.source_sha256 != version.source_sha256 for chunk in chunks):
                raise ValueError("DocumentIR source hash does not match the immutable version")
            payload = [
                chunk.to_knowledge_dict(library_id=library_id, title=record.title)
                for chunk in chunks
            ]
            previous = self.store.get_document_chunks(
                tenant_id=tenant_id,
                library_id=library_id,
                document_id=str(document.document_id),
            )
            publication = self.index.publish_document_version(
                tenant_id=tenant_id,
                library_id=library_id,
                document_id=str(document.document_id),
                version_id=str(document.version_id),
                source_sha256=version.source_sha256,
                chunks=payload,
            )
            try:
                self.repository.record_indexed_version(
                    tenant_id=tenant_id,
                    library_id=library_id,
                    document_id=document.document_id,
                    version_id=document.version_id,
                    chunks=chunks,
                )
            except Exception:
                try:
                    if previous:
                        previous_version = str(previous[0]["version_id"])
                        previous_sha = str(previous[0]["source_sha256"])
                        self.index.publish_document_version(
                            tenant_id=tenant_id,
                            library_id=library_id,
                            document_id=str(document.document_id),
                            version_id=previous_version,
                            source_sha256=previous_sha,
                            chunks=previous,
                        )
                    else:
                        self.index.unindex_document(
                            tenant_id=tenant_id,
                            library_id=library_id,
                            document_id=str(document.document_id),
                        )
                except Exception:
                    logger.critical(
                        "[documents.knowledge] Publication compensation failed "
                        "tenant=%s library=%s document=%s",
                        tenant_id,
                        library_id,
                        document.document_id,
                        exc_info=True,
                    )
                raise
            data = DocumentIndexResult(
                library_id=library_id,
                chunk_count=len(chunks),
                published=bool(publication["published"]),
                source_url=chunks[0].source_url,
            )
            return DocumentResult(
                ok=True,
                code="document_indexed",
                message="Document version is available in the knowledge library",
                data=data,
                document_id=document.document_id,
                version_id=document.version_id,
            )
        except (DocumentAccessError, PermissionError) as exc:
            return DocumentResult(
                ok=False,
                code="document_access_denied",
                message=str(exc),
                document_id=document.document_id,
                version_id=document.version_id,
            )
        except Exception:
            logger.warning(
                "[documents.knowledge] Document index failed tenant=%s library=%s "
                "document=%s version=%s",
                tenant_id,
                library_id,
                document.document_id,
                document.version_id,
                exc_info=True,
            )
            return DocumentResult(
                ok=False,
                code="document_index_failed",
                message="Document indexing failed safely and can be retried",
                document_id=document.document_id,
                version_id=document.version_id,
                retryable=True,
            )

    def unindex_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        library_id: str,
        document_id: Any,
    ) -> DocumentResult[dict[str, Any]]:
        record = self.repository.get_document(
            tenant_id=tenant_id,
            document_id=document_id,
            actor_id=actor_id,
        )
        if record is None or self.store.get_library_for_tenant(library_id, tenant_id) is None:
            return DocumentResult(
                ok=False,
                code="document_access_denied",
                message="Document or knowledge library is unavailable",
                document_id=document_id,
            )
        count = self.index.unindex_document(
            tenant_id=tenant_id,
            library_id=library_id,
            document_id=str(record.id),
        )
        self.repository.tombstone_document_chunks(
            tenant_id=tenant_id,
            document_id=record.id,
            library_id=library_id,
        )
        return DocumentResult(
            ok=True,
            code="document_unindexed",
            message="Document chunks were removed from active retrieval",
            data={"library_id": library_id, "chunk_count": count},
            document_id=record.id,
        )

    async def search_document(
        self,
        query: str,
        *,
        tenant_id: str,
        actor_id: str,
        library_id: str,
        document_id: Any,
        top_k: int = 5,
    ) -> DocumentResult[DocumentSearchResult]:
        record = self.repository.get_document(
            tenant_id=tenant_id,
            document_id=document_id,
            actor_id=actor_id,
        )
        if record is None or self.store.get_library_for_tenant(library_id, tenant_id) is None:
            return DocumentResult(
                ok=False,
                code="document_access_denied",
                message="Document or knowledge library is unavailable",
                document_id=document_id,
            )
        hits = await self.index.search_document(
            query,
            tenant_id=tenant_id,
            library_id=library_id,
            document_id=str(record.id),
            top_k=top_k,
        )
        return DocumentResult(
            ok=True,
            code="document_search_complete",
            message=f"Found {len(hits)} document chunks",
            data=DocumentSearchResult(tuple(hits), format_document_hits(hits)),
            document_id=record.id,
            version_id=record.current_version_id,
        )

    async def search_library(
        self,
        query: str,
        *,
        tenant_id: str,
        library_id: str,
        top_k: int = 5,
    ) -> DocumentResult[DocumentSearchResult]:
        if self.store.get_library_for_tenant(library_id, tenant_id) is None:
            return DocumentResult(
                ok=False,
                code="library_access_denied",
                message="Knowledge library is unavailable",
            )
        hits = await self.index.search(
            query, library_id, top_k=top_k, tenant_id=tenant_id
        )
        return DocumentResult(
            ok=True,
            code="library_search_complete",
            message=f"Found {len(hits)} knowledge chunks",
            data=DocumentSearchResult(tuple(hits), format_document_hits(hits)),
        )
