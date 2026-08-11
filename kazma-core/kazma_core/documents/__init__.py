"""Document intelligence foundation contracts, metadata, and storage."""

from __future__ import annotations

from .artifacts import ArtifactManifest, DocumentArtifact
from .audit import DocumentAuditEvent, DocumentAuditStore
from .capacity import CapacityError, DocumentCapacityGuard
from .config import (
    DocumentConfig,
    DocumentRollout,
    get_document_config,
    get_document_rollout,
)
from .errors import (
    DocumentEncryptedError,
    DocumentFormatError,
    DocumentLimitError,
    DocumentOcrError,
    DocumentOcrUnavailableError,
    DocumentParseError,
    DocumentSandboxError,
    DocumentSecurityError,
    DocumentUnavailableError,
)
from .indexer import DocumentChunk, chunk_document_ir
from .ingestion import (
    DocumentIngestionError,
    DocumentIngestionService,
    IngestionResult,
)
from .jobs import DocumentJobRecord, DocumentJobRepository
from .jobs_pg import (
    PostgresDocumentJobRepository,
    document_jobs_backend,
    document_storage_readiness,
    resolve_job_repository,
)
from .knowledge import (
    DocumentIndexResult,
    DocumentKnowledgeAdapter,
    DocumentSearchResult,
    format_document_hits,
)
from .models import (
    SCHEMA_VERSION,
    ArtifactId,
    BlobId,
    BlockType,
    BoundingBox,
    DocumentBlock,
    DocumentId,
    DocumentIR,
    DocumentJobState,
    DocumentPage,
    DocumentProvenance,
    DocumentResult,
    JobId,
    JobState,
    Provenance,
    VersionId,
    new_artifact_id,
    new_blob_id,
    new_document_id,
    new_job_id,
    new_version_id,
)
from .mutation import get_mutation_registry
from .ocr import OcrHealth, OcrReadiness, get_ocr_health
from .operations import OperationScope
from .quality import (
    PageQuality,
    assess_document_quality,
    assess_page_quality,
    presentation_form_ratio,
    score_document_extraction,
    score_extracted_text,
)
from .registry import (
    ParserCapability,
    ParserPlugin,
    ParserReadiness,
    ParserRegistry,
    get_parser_registry,
)
from .renderers import (
    RendererCapability,
    RendererPlugin,
    RendererReadiness,
    RendererRegistry,
    get_renderer_registry,
)
from .repository import DocumentAccessError, DocumentChunkRecord, DocumentRepository
from .retention import DocumentGarbageCollector, GcReport, start_document_maintenance_loop
from .service import DocumentReadResult, DocumentService
from .storage import (
    BlobChecksumError,
    BlobTooLargeError,
    ContentAddressedStorage,
    StorageQuotaExceeded,
    StoredBlob,
)
from .worker import DocumentWorker, DocumentWorkerManager

__all__ = [
    "ArtifactId",
    "ArtifactManifest",
    "BlobId",
    "BlockType",
    "BlobChecksumError",
    "BlobTooLargeError",
    "BoundingBox",
    "ContentAddressedStorage",
    "CapacityError",
    "DocumentAccessError",
    "DocumentArtifact",
    "DocumentAuditEvent",
    "DocumentAuditStore",
    "DocumentBlock",
    "DocumentCapacityGuard",
    "DocumentConfig",
    "DocumentRollout",
    "DocumentChunk",
    "DocumentChunkRecord",
    "DocumentEncryptedError",
    "DocumentFormatError",
    "DocumentGarbageCollector",
    "DocumentId",
    "DocumentIR",
    "DocumentJobState",
    "DocumentLimitError",
    "DocumentOcrError",
    "DocumentOcrUnavailableError",
    "DocumentJobRecord",
    "DocumentJobRepository",
    "DocumentPage",
    "DocumentIndexResult",
    "DocumentIngestionError",
    "DocumentIngestionService",
    "DocumentKnowledgeAdapter",
    "DocumentParseError",
    "DocumentProvenance",
    "DocumentResult",
    "DocumentSandboxError",
    "DocumentSecurityError",
    "DocumentRepository",
    "DocumentReadResult",
    "DocumentService",
    "DocumentSearchResult",
    "DocumentWorker",
    "DocumentWorkerManager",
    "DocumentUnavailableError",
    "GcReport",
    "IngestionResult",
    "JobId",
    "JobState",
    "ParserCapability",
    "ParserPlugin",
    "ParserReadiness",
    "ParserRegistry",
    "PostgresDocumentJobRepository",
    "OcrHealth",
    "OcrReadiness",
    "OperationScope",
    "PageQuality",
    "Provenance",
    "RendererCapability",
    "RendererPlugin",
    "RendererReadiness",
    "RendererRegistry",
    "SCHEMA_VERSION",
    "StorageQuotaExceeded",
    "StoredBlob",
    "VersionId",
    "get_document_config",
    "get_document_rollout",
    "get_parser_registry",
    "get_ocr_health",
    "get_mutation_registry",
    "get_renderer_registry",
    "assess_document_quality",
    "assess_page_quality",
    "presentation_form_ratio",
    "score_document_extraction",
    "score_extracted_text",
    "chunk_document_ir",
    "document_jobs_backend",
    "document_storage_readiness",
    "format_document_hits",
    "resolve_job_repository",
    "start_document_maintenance_loop",
    "new_artifact_id",
    "new_blob_id",
    "new_document_id",
    "new_job_id",
    "new_version_id",
]
