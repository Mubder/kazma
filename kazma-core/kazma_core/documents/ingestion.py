"""Durable ingestion coordinator — the shared, transport-neutral entry point.

This module wires the Phase 2-7 building blocks (``DocumentRepository``,
``ContentAddressedStorage``, ``DocumentJobRepository``, ``DocumentService``,
``DocumentKnowledgeAdapter``, ``DocumentWorker``) into one restart-safe
ingestion pipeline used by every transport (Web API, native tools, gateway,
IDE/TUI, swarm).

Pipeline (canonical states from :class:`DocumentJobState`)::

    upload bytes
      -> quarantine CAS blob            (streamed, bounded, quota-checked)
      -> repository document + version  (tenant-owned, immutable version)
      -> durable job (RECEIVED)
      -> [intake]  QUARANTINED -> VALIDATING -> READY_TO_PARSE / OCR_REQUIRED
      -> [worker]  PARSING/OCR_RUNNING -> NORMALIZING -> INDEXING
                   -> VERIFYING -> READY

Design invariants:

* **One parsing path.** All parsing goes through ``DocumentService`` (which
  runs the isolated subprocess worker). This module never re-implements
  parsing; the stage handlers call ``DocumentService`` only.
* **Quarantine + validation are synchronous at intake** because the durable
  claim protocol only makes ``READY_TO_PARSE`` / ``OCR_REQUIRED`` /
  ``RETRY_WAIT`` claimable. The *heavy* work (parse/OCR/normalize/index/
  verify) is durable and restart-safe in the worker.
* **Content is stored as an atomic per-version manifest** (canonical
  ``DocumentIR`` JSON) so the paged read API never re-parses hostile bytes.
* **Auto-index is off by default.** Parsing lands a document at ``READY`` and
  indexing is an explicit action (matches the plan: "default parse to READY
  and expose explicit index action").
* **No content in logs.** Only ids, states, and safe codes are logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable

from .audit import DocumentAuditStore
from .capacity import CapacityError, DocumentCapacityGuard
from .config import DocumentConfig, get_document_config, get_document_rollout
from .errors import DocumentParseError
from .jobs import (
    DocumentJobRecord,
    DocumentJobRepository,
    InvalidJobTransitionError,
    JobNotFoundError,
)
from .jobs_pg import (
    document_storage_readiness,
    resolve_job_repository,
)
from .knowledge import DocumentKnowledgeAdapter
from .models import (
    ArtifactId,
    DocumentId,
    DocumentIR,
    DocumentJobState,
    JobId,
    VersionId,
    new_document_id,
)
from .repository import DocumentAccessError, DocumentRepository
from .retention import DocumentGarbageCollector
from .service import DocumentService
from .sniff import sniff_document
from . import telemetry
from .storage import (
    BlobChecksumError,
    BlobTooLargeError,
    ContentAddressedStorage,
    StorageQuotaExceeded,
)
from .worker import (
    DocumentWorker,
    DocumentWorkerManager,
    PermanentDocumentError,
    StageContext,
    StageResult,
    TransientDocumentError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentIngestionError",
    "DocumentIngestionService",
    "IngestionResult",
    "create_default_ingestion_service",
    "get_ingestion_service",
    "set_ingestion_service",
]

# Deterministic namespace so an idempotency key maps to stable opaque IDs.
_INGEST_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://kazma.local/documents/ingest")

# Conservative bound on generation payloads (serialized JSON bytes). A huge
# nested/serialized payload is rejected *before* any renderer is invoked.
_GENERATE_MAX_PAYLOAD_BYTES = 1024 * 1024
_GENERATE_MAX_PAYLOAD_DEPTH = 32

# Keys that expose a server filesystem path — never returned to a caller.
_ARTIFACT_PATH_KEYS = frozenset({"storage_path", "export_path"})


class DocumentIngestionError(RuntimeError):
    """Safe, caller-facing ingestion failure carrying a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Outcome of a successful intake."""

    document_id: DocumentId
    version_id: VersionId
    job_id: JobId
    state: DocumentJobState
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "job_id": str(self.job_id),
            "state": self.state.value,
            "reused": self.reused,
        }


class DocumentIngestionService:
    """Shared durable-ingestion coordinator (one per process)."""

    def __init__(
        self,
        *,
        config: DocumentConfig | None = None,
        storage: ContentAddressedStorage | None = None,
        repository: DocumentRepository | None = None,
        jobs: DocumentJobRepository | None = None,
        service: DocumentService | None = None,
        knowledge_adapter: DocumentKnowledgeAdapter | None = None,
    ) -> None:
        self.config = config or get_document_config()
        self.storage = storage or ContentAddressedStorage(self.config.storage_root)
        sqlite_meta = repository or DocumentRepository(
            self.config.storage_root / "documents.db",
            tenant_quota_bytes=self.config.quota_tenant_bytes,
        )
        # Prefer Postgres multi-replica metadata when configured; fall back to SQLite.
        from .repository_pg import resolve_document_repository

        self.repository = resolve_document_repository(
            sqlite_meta,
            tenant_quota_bytes=self.config.quota_tenant_bytes,
        )
        # Job queue needs a SQLite DocumentRepository only as a local fallback
        # when Postgres jobs are unavailable. If metadata is already SQLite,
        # share that connection; otherwise use a sidecar jobs-only DB.
        if getattr(self.repository, "backend_name", "sqlite") == "postgres":
            jobs_meta = DocumentRepository(
                self.config.storage_root / "documents-jobs-fallback.db",
                tenant_quota_bytes=self.config.quota_tenant_bytes,
            )
        else:
            jobs_meta = self.repository
        sqlite_jobs = jobs or DocumentJobRepository(
            jobs_meta,
            retry_base_seconds=float(self.config.worker_retry_base_seconds),
            retry_max_seconds=float(self.config.worker_retry_max_seconds),
        )
        # Use the Postgres multi-replica job queue when configured; otherwise
        # (or if the pool is unavailable) stay on the single-node SQLite queue.
        self.jobs = resolve_job_repository(
            sqlite_jobs,
            retry_base_seconds=float(self.config.worker_retry_base_seconds),
            retry_max_seconds=float(self.config.worker_retry_max_seconds),
        )
        self.knowledge_adapter = knowledge_adapter
        self.service = service or DocumentService(
            config=self.config,
            knowledge_adapter=knowledge_adapter,
            storage=self.storage,
            repository=self.repository,
        )
        # Phase 9 operations: append-only audit, backpressure guard, GC.
        self.audit = DocumentAuditStore(self.repository)
        self.capacity = DocumentCapacityGuard(
            config=self.config,
            jobs=self.jobs,
            storage_root=self.config.storage_root,
        )
        self.gc = DocumentGarbageCollector(
            repository=self.repository,
            storage=self.storage,
            audit=self.audit,
            config=self.config,
        )
        self._manager: DocumentWorkerManager | None = None

    # ── Worker lifecycle ────────────────────────────────────────────────

    @property
    def worker_running(self) -> bool:
        return self._manager is not None and self._manager.worker.running

    async def start_workers(self) -> None:
        """Start the bounded worker pool (idempotent)."""
        if self.worker_running:
            return
        recovered_intake = await asyncio.to_thread(self.recover_interrupted_intake)
        if recovered_intake:
            logger.info(
                "[documents.ingestion] recovered %d interrupted intake job(s)",
                recovered_intake,
            )
        worker = DocumentWorker(
            self.jobs,
            self.stage_handlers(),
            concurrency=self.config.worker_concurrency,
            poll_interval=0.25,
            lease_seconds=float(self.config.worker_lease_seconds),
            heartbeat_interval=float(self.config.worker_heartbeat_seconds),
        )
        self._manager = DocumentWorkerManager(worker)
        await self._manager.start()
        logger.info(
            "[documents.ingestion] worker pool started concurrency=%d",
            self.config.worker_concurrency,
        )

    async def stop_workers(self) -> None:
        """Stop the worker pool, awaiting in-flight stages (idempotent)."""
        manager = self._manager
        self._manager = None
        if manager is not None:
            await manager.stop()
            logger.info("[documents.ingestion] worker pool stopped")

    def close(self) -> None:
        """Close the shared repository connection."""
        try:
            self.repository.close()
        except Exception:  # pragma: no cover - defensive
            logger.debug("[documents.ingestion] repository close failed", exc_info=True)

    def recover_interrupted_intake(self) -> int:
        """Resume pre-worker intake states left behind by a hard process crash."""

        recoverable = getattr(self.jobs, "interrupted_intake_jobs", None)
        if not callable(recoverable):
            return 0
        recovered = 0
        for snapshot in recoverable():
            current = self.jobs.get(tenant_id=snapshot.tenant_id, job_id=snapshot.id)
            if current is None or current.state not in {
                DocumentJobState.RECEIVED,
                DocumentJobState.QUARANTINED,
                DocumentJobState.VALIDATING,
            }:
                continue
            version = self.repository.get_version(
                tenant_id=current.tenant_id,
                version_id=current.version_id,
            )
            force_ocr = bool(version and version.metadata.get("force_ocr"))
            try:
                result = self._advance_intake(
                    current,
                    tenant=current.tenant_id,
                    actor=None,
                    force_ocr=force_ocr,
                )
            except InvalidJobTransitionError:
                # Another recovery actor won the compare-and-swap.
                continue
            if result.state not in {
                DocumentJobState.RECEIVED,
                DocumentJobState.QUARANTINED,
                DocumentJobState.VALIDATING,
            }:
                recovered += 1
        return recovered

    # ── Intake ──────────────────────────────────────────────────────────

    def ingest_stream(
        self,
        source: BinaryIO | Iterable[bytes],
        *,
        filename: str,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        title: str | None = None,
        document_id: DocumentId | str | None = None,
        idempotency_key: str | None = None,
        force_ocr: bool = False,
        max_attempts: int | None = None,
        content_length: int | None = None,
    ) -> IngestionResult:
        """Stream bytes into quarantine, register, and enqueue a durable job.

        Raises :class:`~kazma_core.documents.capacity.CapacityError` (429/503/
        507) if backpressure/rate/storage limits are exceeded, before any
        bytes are stored.
        """

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        workspace = _clean(workspace_id, "workspace_id")
        actor = _clean(actor_id, "actor_id")
        safe_name = _safe_filename(filename)
        doc_title = (title or safe_name).strip() or safe_name

        # Backpressure / rate / storage capacity gate (before streaming bytes).
        try:
            self.capacity.check_intake(
                tenant_id=tenant, byte_size=int(content_length or 0)
            )
        except CapacityError as exc:
            self.audit.record(
                tenant_id=tenant,
                event_type="intake",
                action="refused",
                outcome="denied",
                actor_id=actor,
                workspace_id=workspace,
                detail={"reason": exc.reason, "http_status": exc.status, "code": exc.code},
            )
            telemetry.record_intake(accepted=False)
            telemetry.record_intake_rejection(exc.reason)
            raise

        # Deterministic ids on idempotent replay.
        det_doc = det_ver = det_job = None
        if idempotency_key:
            key = idempotency_key.strip()
            if not key:
                raise DocumentIngestionError("invalid_request", "idempotency_key is empty")
            det_job = JobId(uuid.uuid5(_INGEST_NS, f"{tenant}:{key}:job"))
            existing = self.jobs.get(tenant_id=tenant, job_id=det_job)
            if existing is not None:
                return IngestionResult(
                    document_id=existing.document_id,
                    version_id=existing.version_id,
                    job_id=existing.id,
                    state=existing.state,
                    reused=True,
                )
            det_doc = DocumentId(uuid.uuid5(_INGEST_NS, f"{tenant}:{key}:doc"))
            det_ver = VersionId(uuid.uuid5(_INGEST_NS, f"{tenant}:{key}:ver"))

        # 1) Stream to quarantine CAS (bounded + tenant-quota enforced).
        try:
            stored = self.storage.put_stream(
                source,
                kind="quarantine",
                max_bytes=self.config.intake_max_bytes,
                tenant_id=tenant,
                repository=self.repository,
                tenant_quota_bytes=self.config.quota_tenant_bytes,
            )
        except BlobTooLargeError as exc:
            self._audit_intake_rejection(tenant, workspace, actor, "intake_too_large")
            raise DocumentIngestionError("intake_too_large", str(exc)) from exc
        except StorageQuotaExceeded as exc:
            self._audit_intake_rejection(tenant, workspace, actor, "quota_exceeded")
            raise DocumentIngestionError("quota_exceeded", str(exc)) from exc
        except BlobChecksumError as exc:
            self._audit_intake_rejection(tenant, workspace, actor, "intake_corrupt")
            raise DocumentIngestionError("intake_corrupt", str(exc)) from exc

        # 1b) Optional malware scan on the quarantined bytes (ClamAV when present).
        try:
            from .malware import scan_if_configured

            quarantine_path = self.storage.blob_path(
                kind="quarantine", sha256=stored.sha256
            )
            scan_if_configured(quarantine_path, self.config)
        except DocumentParseError as exc:
            self._audit_intake_rejection(
                tenant, workspace, actor, getattr(exc, "code", "unsafe_document")
            )
            telemetry.record_intake(accepted=False)
            telemetry.record_intake_rejection(getattr(exc, "code", "unsafe_document"))
            raise DocumentIngestionError(
                getattr(exc, "code", "unsafe_document"),
                getattr(exc, "safe_message", str(exc)),
            ) from exc

        # 2) Register blob + document + immutable version.
        blob = self.repository.register_blob(
            tenant_id=tenant,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            storage_kind="quarantine",
        )
        if document_id is not None:
            doc_id = DocumentId(document_id)
            record = self.repository.get_document(
                tenant_id=tenant, document_id=doc_id, actor_id=actor
            )
            if record is None:
                raise DocumentIngestionError(
                    "document_access_denied", "Target document is unavailable"
                )
        else:
            doc_id = det_doc or new_document_id()
            self.repository.create_document(
                tenant_id=tenant,
                owner_id=actor,
                title=doc_title,
                metadata={"original_filename": safe_name, "workspace_id": workspace},
                document_id=doc_id,
            )

        mime = self._probe_mime(stored, safe_name)
        version = self.repository.create_version(
            tenant_id=tenant,
            document_id=doc_id,
            actor_id=actor,
            source_blob_id=blob.id,
            source_sha256=stored.sha256,
            original_filename=safe_name,
            mime_type=mime,
            metadata={"force_ocr": bool(force_ocr)},
            version_id=det_ver,
        )

        # 3) Enqueue durable job.
        attempts = int(max_attempts) if max_attempts else self.config.worker_max_retries
        attempts = max(1, attempts)
        job = self.jobs.enqueue(
            tenant_id=tenant,
            workspace_id=workspace,
            document_id=doc_id,
            version_id=version.id,
            idempotency_key=idempotency_key.strip()
            if idempotency_key
            else f"ingest:{version.id}",
            max_attempts=attempts,
            job_id=det_job,
        )

        # 4) Synchronous quarantine + validation, then hand to the worker.
        job = self._advance_intake(job, tenant=tenant, actor=actor, force_ocr=force_ocr)
        telemetry.record_intake(accepted=True, byte_size=stored.byte_size)
        self.audit.record(
            tenant_id=tenant,
            event_type="intake",
            action="upload",
            outcome="success",
            actor_id=actor,
            workspace_id=workspace,
            document_id=doc_id,
            version_id=version.id,
            job_id=job.id,
            detail={"byte_size": stored.byte_size, "state": job.state.value},
        )
        logger.info(
            "[documents.ingestion] intake accepted",
            extra=telemetry.correlation_extra(
                tenant_id=tenant,
                workspace_id=workspace,
                document_id=doc_id,
                version_id=version.id,
                job_id=job.id,
            ),
        )
        return IngestionResult(
            document_id=doc_id,
            version_id=version.id,
            job_id=job.id,
            state=job.state,
            reused=False,
        )

    def _audit_intake_rejection(
        self, tenant: str, workspace: str, actor: str, reason: str
    ) -> None:
        telemetry.record_intake(accepted=False)
        telemetry.record_intake_rejection(reason)
        self.audit.record(
            tenant_id=tenant,
            event_type="intake",
            action="rejected",
            outcome="failure",
            actor_id=actor,
            workspace_id=workspace,
            detail={"reason": reason},
        )

    def _advance_intake(
        self,
        job: DocumentJobRecord,
        *,
        tenant: str,
        actor: str | None,
        force_ocr: bool,
    ) -> DocumentJobRecord:
        """RECEIVED -> QUARANTINED -> VALIDATING -> READY_TO_PARSE/OCR_REQUIRED."""

        if job.state is DocumentJobState.RECEIVED:
            job = self._transition(job, DocumentJobState.QUARANTINED)
        if job.state is DocumentJobState.QUARANTINED:
            job = self._transition(job, DocumentJobState.VALIDATING)
        if job.state is not DocumentJobState.VALIDATING:
            return job
        try:
            self._validate_and_promote(tenant=tenant, actor=actor, job=job)
        except DocumentParseError as exc:
            return self._transition(
                job,
                DocumentJobState.REJECTED,
                error_code=exc.code,
                error_message=exc.safe_message,
                event_type="rejected",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[documents.ingestion] validation failed job=%s type=%s",
                job.id,
                type(exc).__name__,
            )
            return self._transition(
                job,
                DocumentJobState.REJECTED,
                error_code="validation_failed",
                error_message=f"Document validation failed safely ({type(exc).__name__})",
                event_type="rejected",
            )
        target = (
            DocumentJobState.OCR_REQUIRED if force_ocr else DocumentJobState.READY_TO_PARSE
        )
        return self._transition(job, target, event_type="validated")

    def _validate_and_promote(
        self, *, tenant: str, actor: str | None, job: DocumentJobRecord
    ) -> None:
        """Sniff the quarantined blob and physically promote it to originals."""

        version = self.repository.get_version(
            tenant_id=tenant, version_id=job.version_id, actor_id=actor
        )
        if version is None:
            raise DocumentParseError("Document version is unavailable", code="document_missing")
        quarantine_path = self.storage.blob_path(
            kind="quarantine", sha256=version.source_sha256
        )
        if not quarantine_path.is_file():
            raise DocumentParseError(
                "Quarantined document bytes are missing", code="document_missing"
            )
        with self._materialize(quarantine_path, version.original_filename) as staged:
            sniffed = sniff_document(staged, self.config)
            self.service.registry.resolve(
                mime_type=sniffed.mime_type, extension=sniffed.extension
            )
        # Physically promote to the immutable originals store (same sha).
        with quarantine_path.open("rb") as handle:
            self.storage.put_stream(
                handle,
                kind="originals",
                max_bytes=self.config.intake_max_bytes,
                expected_sha256=version.source_sha256,
            )

    # ── Stage handlers (durable worker) ─────────────────────────────────

    def stage_handlers(self) -> dict[DocumentJobState, Any]:
        return {
            DocumentJobState.PARSING: self._stage_parse,
            DocumentJobState.OCR_RUNNING: self._stage_ocr,
            DocumentJobState.NORMALIZING: self._stage_normalize,
            DocumentJobState.INDEXING: self._stage_index,
            DocumentJobState.VERIFYING: self._stage_verify,
        }

    def _stage_parse(
        self, job: DocumentJobRecord, context: StageContext
    ) -> StageResult:
        context.raise_if_cancelled()
        self._parse_to_manifest(job, force_ocr=False)
        return StageResult(DocumentJobState.NORMALIZING, stage="normalizing")

    def _stage_ocr(self, job: DocumentJobRecord, context: StageContext) -> StageResult:
        context.raise_if_cancelled()
        self._parse_to_manifest(job, force_ocr=True)
        return StageResult(DocumentJobState.NORMALIZING, stage="normalizing")

    def _stage_normalize(
        self, job: DocumentJobRecord, context: StageContext
    ) -> StageResult:
        context.raise_if_cancelled()
        if self._read_manifest(job) is None:
            raise TransientDocumentError(
                "manifest_missing", "Normalized document IR is missing"
            )
        return StageResult(DocumentJobState.INDEXING, stage="indexing")

    def _stage_index(
        self, job: DocumentJobRecord, context: StageContext
    ) -> StageResult:
        context.raise_if_cancelled()
        # Auto-index policy is OFF: indexing is an explicit action. This stage
        # only confirms the canonical IR exists before verification.
        return StageResult(DocumentJobState.VERIFYING, stage="verifying")

    def _stage_verify(
        self, job: DocumentJobRecord, context: StageContext
    ) -> StageResult:
        context.raise_if_cancelled()
        manifest = self._read_manifest(job)
        if manifest is None:
            raise TransientDocumentError(
                "manifest_missing", "Verification could not load the document IR"
            )
        ir_value = manifest.get("ir")
        expected = manifest.get("ir_sha256")
        try:
            ir = DocumentIR.from_dict(ir_value)
        except Exception as exc:  # noqa: BLE001
            raise PermanentDocumentError(
                "ir_invalid", f"Document IR failed verification ({type(exc).__name__})"
            ) from exc
        actual = hashlib.sha256(ir.to_json().encode("utf-8")).hexdigest()
        if expected != actual:
            raise PermanentDocumentError(
                "ir_hash_mismatch", "Document IR hash failed verification"
            )
        return StageResult(DocumentJobState.READY, stage="ready")

    def _parse_to_manifest(self, job: DocumentJobRecord, *, force_ocr: bool) -> None:
        version = self.repository.get_version(
            tenant_id=job.tenant_id, version_id=job.version_id
        )
        if version is None:
            raise PermanentDocumentError(
                "document_missing", "Document version is unavailable"
            )
        originals_path = self.storage.blob_path(
            kind="originals", sha256=version.source_sha256
        )
        if not originals_path.is_file():
            raise TransientDocumentError(
                "originals_missing", "Original document bytes are unavailable"
            )
        force = force_ocr or bool(version.metadata.get("force_ocr"))
        with self._materialize(originals_path, version.original_filename) as staged:
            try:
                parsed = self.service.parse_ingested_blob(staged, force_ocr=force)
            except DocumentParseError as exc:
                raise PermanentDocumentError(exc.code, exc.safe_message) from exc
        # Bind the parsed IR to the durable document/version identity.
        ir = DocumentIR(
            document_id=version.document_id,
            version_id=version.id,
            pages=parsed.pages,
            provenance=parsed.provenance,
            schema_version=parsed.schema_version,
            metadata=parsed.metadata,
        )
        self._write_manifest(job, ir)

    # ── Content + inspection ────────────────────────────────────────────

    def get_content(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        document_id: DocumentId | str,
        page: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        block: str | int | None = None,
        offset: int = 0,
        max_chars: int = 20_000,
        fence: bool = True,
    ) -> dict[str, Any]:
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        doc_id = DocumentId(document_id)
        record = self.repository.get_document(
            tenant_id=tenant, document_id=doc_id, actor_id=actor
        )
        if record is None or record.current_version_id is None:
            raise DocumentIngestionError(
                "document_access_denied", "Document is unavailable"
            )
        version = self.repository.get_version(
            tenant_id=tenant, version_id=record.current_version_id, actor_id=actor
        )
        if version is None:
            raise DocumentIngestionError(
                "document_access_denied", "Document version is unavailable"
            )
        manifest = self.storage.read_manifest(
            document_id=doc_id, version_id=version.id
        )
        if manifest is None or "ir" not in manifest:
            raise DocumentIngestionError(
                "not_ready", "Document has not finished processing"
            )
        ir = DocumentIR.from_dict(manifest["ir"])
        read = self.service.read_ir(
            ir,
            page=page,
            page_start=page_start,
            page_end=page_end,
            block=block,
            offset=offset,
            max_chars=max_chars,
            fence=fence,
        )
        self.audit.record(
            tenant_id=tenant,
            event_type="access",
            action="read_content",
            outcome="success",
            actor_id=actor,
            document_id=doc_id,
            version_id=version.id,
            detail={"page_start": page_start, "page_end": page_end, "page": page},
        )
        return {
            "document_id": str(doc_id),
            "version_id": str(version.id),
            "page_count": len(ir.pages),
            "mime_type": version.mime_type,
            "text": read.text,
            "fenced": read.fenced,
            "continuation": read.continuation,
        }

    def list_documents(
        self, *, tenant_id: str, actor_id: str
    ) -> list[dict[str, Any]]:
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        rows: list[dict[str, Any]] = []
        for record in self.repository.list_documents(tenant_id=tenant):
            if not self.repository.has_access(
                tenant_id=tenant, document_id=record.id, actor_id=actor
            ):
                continue
            latest = self.jobs_for_document(
                tenant_id=tenant, document_id=record.id
            )
            state = latest[0]["state"] if latest else None
            rows.append(
                {
                    "document_id": str(record.id),
                    "title": record.title,
                    "current_version_id": str(record.current_version_id)
                    if record.current_version_id
                    else None,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "state": state,
                    "job_id": latest[0]["job_id"] if latest else None,
                }
            )
        return rows

    def get_document_detail(
        self, *, tenant_id: str, actor_id: str, document_id: DocumentId | str
    ) -> dict[str, Any]:
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        doc_id = DocumentId(document_id)
        record = self.repository.get_document(
            tenant_id=tenant, document_id=doc_id, actor_id=actor
        )
        if record is None:
            raise DocumentIngestionError(
                "document_access_denied", "Document is unavailable"
            )
        versions = [
            {
                "version_id": str(v.id),
                "version_number": v.version_number,
                "original_filename": v.original_filename,
                "mime_type": v.mime_type,
                "source_sha256": v.source_sha256,
                "created_at": v.created_at,
            }
            for v in self.repository.list_versions(
                tenant_id=tenant, document_id=doc_id, actor_id=actor
            )
        ]
        jobs = self.jobs_for_document(tenant_id=tenant, document_id=doc_id)
        try:
            artifacts = self.list_document_artifacts(
                tenant_id=tenant, actor_id=actor, document_id=doc_id
            )
        except DocumentIngestionError:
            artifacts = []
        return {
            "document_id": str(record.id),
            "title": record.title,
            "current_version_id": str(record.current_version_id)
            if record.current_version_id
            else None,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "versions": versions,
            "jobs": jobs,
            "artifacts": artifacts,
        }

    def jobs_for_document(
        self, *, tenant_id: str, document_id: DocumentId | str
    ) -> list[dict[str, Any]]:
        tenant = _clean(tenant_id, "tenant_id")
        doc_id = DocumentId(document_id)
        # Backend-agnostic: works whether the queue is SQLite or Postgres.
        job_ids = self.jobs.document_job_ids(tenant_id=tenant, document_id=doc_id)
        result: list[dict[str, Any]] = []
        for job_id in job_ids:
            job = self.jobs.get(tenant_id=tenant, job_id=JobId(job_id))
            if job is not None:
                result.append(self._job_dict(job))
        return result

    def job_status(
        self, *, tenant_id: str, job_id: JobId | str
    ) -> dict[str, Any] | None:
        tenant = _clean(tenant_id, "tenant_id")
        job = self.jobs.get(tenant_id=tenant, job_id=JobId(job_id))
        return self._job_dict(job) if job is not None else None

    def job_events(
        self, *, tenant_id: str, job_id: JobId | str
    ) -> list[dict[str, Any]]:
        tenant = _clean(tenant_id, "tenant_id")
        events = self.jobs.events(tenant_id=tenant, job_id=JobId(job_id))
        return [
            {
                "event_type": e.event_type,
                "from_state": e.from_state.value if e.from_state else None,
                "to_state": e.to_state.value,
                "stage": e.stage,
                "error_code": e.error_code,
                "error_message": e.error_message,
                "created_at": e.created_at,
            }
            for e in events
        ]

    def cancel_job(
        self, *, tenant_id: str, job_id: JobId | str, actor_id: str | None = None
    ) -> dict[str, Any]:
        tenant = _clean(tenant_id, "tenant_id")
        job = self.jobs.request_cancel(tenant_id=tenant, job_id=JobId(job_id))
        self.audit.record(
            tenant_id=tenant,
            event_type="cancel",
            action="request_cancel",
            outcome="success",
            actor_id=actor_id,
            document_id=job.document_id,
            job_id=job.id,
            detail={"state": job.state.value},
        )
        return self._job_dict(job)

    def retry_job(
        self, *, tenant_id: str, job_id: JobId | str, actor_id: str | None = None
    ) -> dict[str, Any]:
        """Re-enqueue a dead-lettered/rejected job as a fresh version job."""
        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        job = self.jobs.get(tenant_id=tenant, job_id=JobId(job_id))
        if job is None:
            raise JobNotFoundError("job is unavailable in this tenant")
        if job.state not in {DocumentJobState.DEAD_LETTER, DocumentJobState.REJECTED}:
            raise InvalidJobTransitionError(
                "only dead-lettered or rejected jobs can be retried"
            )
        version = self.repository.get_version(
            tenant_id=tenant, version_id=job.version_id
        )
        if version is None:
            raise JobNotFoundError("document version is unavailable")
        owner_record = self.repository.get_document(
            tenant_id=tenant, document_id=job.document_id, include_deleted=True
        )
        owner = owner_record.owner_id if owner_record is not None else "system"
        fresh = self.jobs.enqueue(
            tenant_id=tenant,
            workspace_id=job.workspace_id,
            document_id=job.document_id,
            version_id=job.version_id,
            idempotency_key=f"retry:{job.id}:{uuid.uuid4().hex}",
            max_attempts=job.max_attempts,
        )
        force = bool(version.metadata.get("force_ocr"))
        fresh = self._advance_intake(
            fresh, tenant=tenant, actor=owner, force_ocr=force
        )
        self.audit.record(
            tenant_id=tenant,
            event_type="retry",
            action="reenqueue",
            outcome="success",
            actor_id=actor_id or owner,
            document_id=job.document_id,
            job_id=fresh.id,
            detail={"state": fresh.state.value},
        )
        return self._job_dict(fresh)

    # ── Knowledge integration (explicit index/search) ───────────────────

    def index_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        document_id: DocumentId | str,
        library_id: str,
    ) -> dict[str, Any]:
        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        doc_id = DocumentId(document_id)
        record = self.repository.get_document(
            tenant_id=tenant, document_id=doc_id, actor_id=actor
        )
        if record is None or record.current_version_id is None:
            raise DocumentIngestionError(
                "document_access_denied", "Document is unavailable"
            )
        manifest = self.storage.read_manifest(
            document_id=doc_id, version_id=record.current_version_id
        )
        if manifest is None or "ir" not in manifest:
            raise DocumentIngestionError(
                "not_ready", "Document has not finished processing"
            )
        ir = DocumentIR.from_dict(manifest["ir"])
        result = self.service.index_document_ir(
            ir, tenant_id=tenant, actor_id=actor, library_id=_clean(library_id, "library_id")
        )
        payload = self._result_payload(result)
        chunk_count = int(payload.get("chunk_count") or payload.get("chunks") or 0)
        telemetry.record_indexing(chunks=chunk_count)
        self.audit.record(
            tenant_id=tenant,
            event_type="index",
            action="publish",
            outcome="success",
            actor_id=actor,
            document_id=doc_id,
            version_id=record.current_version_id,
            detail={"library_id": _clean(library_id, "library_id"), "chunk_count": chunk_count},
        )
        return payload

    def unindex_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        document_id: DocumentId | str,
        library_id: str,
    ) -> dict[str, Any]:
        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        lib = _clean(library_id, "library_id")
        doc_id = DocumentId(document_id)
        result = self.service.unindex_document(
            tenant_id=tenant,
            actor_id=actor,
            library_id=lib,
            document_id=doc_id,
        )
        payload = self._result_payload(result)
        self.audit.record(
            tenant_id=tenant,
            event_type="unindex",
            action="remove",
            outcome="success",
            actor_id=actor,
            document_id=doc_id,
            detail={"library_id": lib},
        )
        return payload

    async def search_library(
        self, *, tenant_id: str, library_id: str, query: str, top_k: int = 5
    ) -> dict[str, Any]:
        result = await self.service.search_library(
            _clean(query, "query"),
            tenant_id=_clean(tenant_id, "tenant_id"),
            library_id=_clean(library_id, "library_id"),
            top_k=top_k,
        )
        return self._result_payload(result)

    def delete_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        document_id: DocumentId | str,
        reason: str = "user_requested",
    ) -> dict[str, Any]:
        """Unindex + tombstone a document (physical bytes reclaimed later by GC)."""
        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        doc_id = DocumentId(document_id)
        result = self.service.delete_document(
            tenant_id=tenant,
            actor_id=actor,
            document_id=doc_id,
            reason=_clean(reason, "reason"),
        )
        if not result.ok:
            self.audit.record(
                tenant_id=tenant,
                event_type="delete",
                action="tombstone",
                outcome="failure",
                actor_id=actor,
                document_id=doc_id,
                detail={"code": result.code},
            )
            raise DocumentIngestionError(
                result.code or "document_delete_failed",
                result.message or "Document deletion failed",
            )
        self.audit.record(
            tenant_id=tenant,
            event_type="delete",
            action="tombstone",
            outcome="success",
            actor_id=actor,
            document_id=doc_id,
            detail={"reason": _clean(reason, "reason")},
        )
        serialized = result.to_dict()
        return {"document_id": str(doc_id), "deleted": True, "data": serialized.get("data")}

    # ── Document actions (convert / pdf / redact / generate) ─────────────

    async def convert_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workspace_id: str,
        document_id: DocumentId | str,
        target_format: str,
        output_name: str | None = None,
    ) -> dict[str, Any]:
        """Convert a document's current immutable version to ``target_format``.

        Resolves the version with tenant+actor ACL, materializes only its
        immutable ``originals`` blob, and delegates to
        :meth:`DocumentService.convert` under the exact durable scope. No raw
        path is ever accepted; the produced artifact is tenant-owned.
        """

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        workspace = _clean(workspace_id, "workspace_id")
        fmt = _clean(target_format, "target_format").lower().lstrip(".")
        doc_id = DocumentId(document_id)
        _record, version = self._resolve_current_version(
            tenant=tenant, actor=actor, doc_id=doc_id
        )
        with self._materialize_original(version) as staged:
            result = await self.service.convert(
                staged,
                fmt,
                approved_path=staged,
                tenant_id=tenant,
                workspace_id=workspace,
                actor_id=actor,
                output_name=output_name,
                export_dir=None,
                document_id=doc_id,
                version_id=version.id,
            )
        return self._audited_artifact(
            result, tenant=tenant, actor=actor, doc_id=doc_id,
            event_type="convert", action="convert",
            detail={"target_format": fmt}, failure_metric="generation",
        )

    async def pdf_info_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workspace_id: str,
        document_id: DocumentId | str,
    ) -> dict[str, Any]:
        """Return a safe structural report for a document's current PDF version."""

        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        workspace = _clean(workspace_id, "workspace_id")
        doc_id = DocumentId(document_id)
        _record, version = self._resolve_current_version(
            tenant=tenant, actor=actor, doc_id=doc_id
        )
        with self._materialize_original(version) as staged:
            result = await self.service.pdf_info(
                staged,
                approved_path=staged,
                tenant_id=tenant,
                workspace_id=workspace,
                actor_id=actor,
            )
        return self._report_payload(result)

    async def pdf_split_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workspace_id: str,
        document_id: DocumentId | str,
        start_page: int = 1,
        end_page: int = 0,
        output_name: str | None = None,
    ) -> dict[str, Any]:
        """Split a page range from a document's current PDF version."""

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        workspace = _clean(workspace_id, "workspace_id")
        doc_id = DocumentId(document_id)
        start = self._require_page(start_page, "start_page")
        end = self._require_page(end_page, "end_page", allow_zero=True)
        if end and end < start:
            raise DocumentIngestionError(
                "invalid_request", "end_page must be greater than or equal to start_page"
            )
        _record, version = self._resolve_current_version(
            tenant=tenant, actor=actor, doc_id=doc_id
        )
        with self._materialize_original(version) as staged:
            result = await self.service.pdf_split(
                staged,
                approved_path=staged,
                tenant_id=tenant,
                workspace_id=workspace,
                actor_id=actor,
                start_page=start,
                end_page=end,
                output_name=output_name,
                export_dir=None,
                document_id=doc_id,
                version_id=version.id,
            )
        return self._audited_artifact(
            result, tenant=tenant, actor=actor, doc_id=doc_id,
            event_type="mutate", action="split",
            detail={"page_start": start, "page_end": end}, failure_metric="generation",
        )

    async def pdf_fill_form_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workspace_id: str,
        document_id: DocumentId | str,
        fields: dict[str, str],
        output_name: str | None = None,
    ) -> dict[str, Any]:
        """Fill AcroForm fields on a document's current PDF version."""

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        workspace = _clean(workspace_id, "workspace_id")
        doc_id = DocumentId(document_id)
        clean_fields = self._require_form_fields(fields)
        _record, version = self._resolve_current_version(
            tenant=tenant, actor=actor, doc_id=doc_id
        )
        with self._materialize_original(version) as staged:
            result = await self.service.pdf_fill_form(
                staged,
                clean_fields,
                approved_path=staged,
                tenant_id=tenant,
                workspace_id=workspace,
                actor_id=actor,
                output_name=output_name,
                export_dir=None,
                document_id=doc_id,
                version_id=version.id,
            )
        return self._audited_artifact(
            result, tenant=tenant, actor=actor, doc_id=doc_id,
            event_type="mutate", action="fill_form",
            detail={"field_count": len(clean_fields)}, failure_metric="generation",
        )

    async def redact_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workspace_id: str,
        document_id: DocumentId | str,
        terms: list[str],
        output_name: str | None = None,
    ) -> dict[str, Any]:
        """Physically redact terms from a document's current PDF version.

        Redaction terms are validated but never logged or persisted here; the
        isolated worker protocol/manifest deliberately excludes them.
        """

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        workspace = _clean(workspace_id, "workspace_id")
        doc_id = DocumentId(document_id)
        clean_terms = self._require_terms(terms)
        _record, version = self._resolve_current_version(
            tenant=tenant, actor=actor, doc_id=doc_id
        )
        with self._materialize_original(version) as staged:
            result = await self.service.redact(
                staged,
                clean_terms,
                approved_path=staged,
                tenant_id=tenant,
                workspace_id=workspace,
                actor_id=actor,
                output_name=output_name,
                export_dir=None,
                document_id=doc_id,
                version_id=version.id,
            )
        return self._audited_artifact(
            result, tenant=tenant, actor=actor, doc_id=doc_id,
            event_type="redact", action="redact",
            detail={"term_count": len(clean_terms)}, failure_metric="redaction",
        )

    async def merge_documents(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workspace_id: str,
        document_ids: list[DocumentId | str],
        output_name: str | None = None,
    ) -> dict[str, Any]:
        """Merge several documents' current PDF versions into one artifact.

        All inputs must belong to the same tenant/actor/workspace. The merged
        artifact is owned by the first input document so it is tenant-scoped
        and downloadable by its opaque artifact ID.
        """

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        workspace = _clean(workspace_id, "workspace_id")
        if not isinstance(document_ids, (list, tuple)) or len(document_ids) < 2:
            raise DocumentIngestionError(
                "invalid_request", "merge requires at least two document IDs"
            )
        if len(document_ids) > self.config.intake_max_files:
            raise DocumentIngestionError("invalid_request", "too many documents to merge")
        doc_ids = [DocumentId(value) for value in document_ids]
        owner_doc: DocumentId | None = None
        owner_version_id: VersionId | None = None
        materialized: list[DocumentIngestionService._Materialized] = []
        paths: list[Path] = []
        try:
            for doc_id in doc_ids:
                _record, version = self._resolve_current_version(
                    tenant=tenant, actor=actor, doc_id=doc_id
                )
                if owner_doc is None:
                    owner_doc = doc_id
                    owner_version_id = version.id
                handle = self._materialize_original(version)
                staged = handle.__enter__()
                materialized.append(handle)
                paths.append(staged)
            result = await self.service.pdf_merge(
                tuple(paths),
                approved_paths=tuple(paths),
                tenant_id=tenant,
                workspace_id=workspace,
                actor_id=actor,
                output_name=output_name,
                export_dir=None,
                document_id=owner_doc,
                version_id=owner_version_id,
            )
        finally:
            for handle in reversed(materialized):
                handle.__exit__(None, None, None)
        return self._audited_artifact(
            result, tenant=tenant, actor=actor, doc_id=owner_doc,
            event_type="generate", action="merge",
            detail={"count": len(doc_ids)}, failure_metric="generation",
        )

    async def generate_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        workspace_id: str,
        target_format: str,
        payload: dict[str, Any],
        output_name: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Generate a verified artifact, then durably ingest it as a document.

        The renderer runs in isolation with **no** durable scope (a transient
        CAS artifact). Its verified bytes are then re-ingested through the
        normal durable pipeline so the caller receives a tenant-owned
        ``document_id``/``version_id``/``job_id`` and a downloadable result.
        """

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        workspace = _clean(workspace_id, "workspace_id")
        fmt = _clean(target_format, "target_format").lower().lstrip(".")
        bounded = self._bounded_payload(payload)
        result = await self.service.generate(
            fmt,
            bounded,
            tenant_id=tenant,
            workspace_id=workspace,
            actor_id=actor,
            output_name=output_name,
            export_dir=None,
        )
        if not result.ok:
            telemetry.record_generation_failure("generate")
            self.audit.record(
                tenant_id=tenant, event_type="generate", action="generate",
                outcome="failure", actor_id=actor,
                detail={"target_format": fmt, "code": result.code or "generation_failed"},
            )
            raise DocumentIngestionError(
                result.code or "document_operation_failed",
                result.message or "Document generation failed",
            )
        artifact = result.data
        if artifact is None:
            raise DocumentIngestionError(
                "document_service_contract_error",
                "Generation succeeded but returned no artifact",
            )
        extension = artifact.manifest.output_extension.lstrip(".") or fmt
        stem = _safe_filename(output_name or str(bounded.get("title", "document")))
        stem = Path(stem).stem or "document"
        filename = f"{stem}.{extension}"
        storage_path = artifact.storage_path
        try:
            with storage_path.open("rb") as source:
                ingest = self.ingest_stream(
                    source,
                    filename=filename,
                    tenant_id=tenant,
                    workspace_id=workspace,
                    actor_id=actor,
                    title=title or stem,
                )
        finally:
            self._cleanup_transient_artifact(tenant, artifact)
        payload_out = ingest.to_dict()
        payload_out["target_format"] = fmt
        payload_out["warnings"] = list(result.warnings)
        self.audit.record(
            tenant_id=tenant, event_type="generate", action="generate",
            outcome="success", actor_id=actor,
            document_id=ingest.document_id, version_id=ingest.version_id,
            job_id=ingest.job_id, detail={"target_format": fmt},
        )
        return payload_out

    # ── Secure artifact retrieval ────────────────────────────────────────

    def resolve_artifact_blob(
        self, *, tenant_id: str, actor_id: str, artifact_id: ArtifactId | str
    ) -> dict[str, Any]:
        """Resolve a downloadable artifact blob by opaque ID (ACL enforced).

        Returns a safe descriptor (physical path + download filename + mime)
        without exposing any server path to the caller. Returns cross-tenant
        and cross-actor requests as an access denial (mapped to 404 upstream).
        """

        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        try:
            identifier = ArtifactId(artifact_id)
        except (TypeError, ValueError) as exc:
            raise DocumentIngestionError(
                "artifact_access_denied", "Artifact is unavailable"
            ) from exc
        try:
            record = self.repository.get_artifact(
                tenant_id=tenant, artifact_id=identifier, actor_id=actor
            )
        except Exception as exc:  # noqa: BLE001 - ACL/lookup failures are denials
            raise DocumentIngestionError(
                "artifact_access_denied", "Artifact is unavailable"
            ) from exc
        if record is None:
            raise DocumentIngestionError(
                "artifact_access_denied", "Artifact is unavailable"
            )
        blob = self.repository.get_blob(tenant_id=tenant, blob_id=record.blob_id)
        if blob is None:
            raise DocumentIngestionError(
                "artifact_access_denied", "Artifact content is unavailable"
            )
        path = self.storage.blob_path(kind=blob.storage_kind, sha256=blob.sha256)
        if not path.is_file():
            raise DocumentIngestionError(
                "not_ready", "Artifact content is unavailable"
            )
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        extension = str(metadata.get("output", {}).get("extension") or "").lstrip(".")
        mime = str(metadata.get("output", {}).get("mime_type") or "application/octet-stream")
        operation = str(record.artifact_type or "artifact").replace(":", "-")
        short = str(identifier)[:8]
        suffix = f".{extension}" if extension else ""
        self.audit.record(
            tenant_id=tenant,
            event_type="download",
            action="artifact",
            outcome="success",
            actor_id=actor,
            document_id=record.document_id,
            version_id=record.version_id,
            detail={"artifact_type": operation, "byte_size": blob.byte_size},
        )
        return {
            "path": path,
            "filename": f"{operation}-{short}{suffix}",
            "mime_type": mime,
            "size": blob.byte_size,
            "artifact_id": str(identifier),
        }

    def list_document_artifacts(
        self, *, tenant_id: str, actor_id: str, document_id: DocumentId | str
    ) -> list[dict[str, Any]]:
        """List sanitized artifact descriptors for a document (ACL enforced)."""

        tenant = _clean(tenant_id, "tenant_id")
        actor = _clean(actor_id, "actor_id")
        doc_id = DocumentId(document_id)
        try:
            record = self.repository.get_document(
                tenant_id=tenant, document_id=doc_id, actor_id=actor
            )
        except DocumentAccessError as exc:
            raise DocumentIngestionError(
                "document_access_denied", "Document is unavailable"
            ) from exc
        if record is None:
            raise DocumentIngestionError(
                "document_access_denied", "Document is unavailable"
            )
        rows = self.repository.list_artifacts(
            tenant_id=tenant, document_id=doc_id
        )
        artifacts: list[dict[str, Any]] = []
        for row in rows:
            metadata = row.metadata if isinstance(row.metadata, dict) else {}
            output = metadata.get("output", {}) if isinstance(metadata, dict) else {}
            artifacts.append(
                {
                    "artifact_id": str(row.id),
                    "operation": row.artifact_type,
                    "created_at": row.created_at,
                    "output": {
                        "extension": output.get("extension"),
                        "mime_type": output.get("mime_type"),
                        "size": output.get("size"),
                    },
                    "warnings": metadata.get("warnings", [])
                    if isinstance(metadata, dict)
                    else [],
                }
            )
        return artifacts

    # ── Health ──────────────────────────────────────────────────────────

    def health(self) -> dict[str, Any]:
        data = self.service.health()
        data["rollout"] = get_document_rollout().to_dict()
        data["worker"] = {
            "running": self.worker_running,
            "concurrency": self.config.worker_concurrency,
        }
        data["storage_root"] = str(self.config.storage_root)
        return data

    # ── Phase 9 operations (capacity / metrics / audit / GC / readiness) ──

    def capacity_snapshot(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        """Return the live capacity/backpressure snapshot and refresh gauges."""
        snap = self.capacity.snapshot(tenant_id=tenant_id)
        rollout = get_document_rollout()
        snap["rollout"] = rollout.to_dict()
        if not rollout.enabled:
            reasons = list(snap.get("degraded_reasons", []))
            if "durable_writes_disabled" not in reasons:
                reasons.append("durable_writes_disabled")
            snap["degraded_reasons"] = reasons
            snap["status"] = "disabled"
        try:
            queue = snap.get("queue", {})
            if "error" not in queue:
                telemetry.set_queue_gauges(
                    depth=queue.get("depth", 0),
                    oldest_age_seconds=queue.get("oldest_age_seconds", 0),
                    active_leases=queue.get("active_leases", 0),
                    retry_waiting=queue.get("retry_waiting", 0),
                    dead_letter=queue.get("dead_letter", 0),
                )
        except Exception:  # noqa: BLE001 - telemetry must not break the snapshot
            logger.debug("[documents.ingestion] queue gauge refresh failed", exc_info=True)
        return snap

    def metrics_snapshot(self, *, tenant_id: str | None = None) -> dict[str, Any]:
        """Refresh queue/storage gauges and return a document metrics view.

        Numeric-only, no content: queue health, storage bytes/dedup, and (when
        a tenant is given) that tenant's quota consumption via the query API.
        """
        cap = self.capacity_snapshot(tenant_id=tenant_id)
        logical, physical = self._storage_bytes()
        telemetry.set_storage_gauges(logical_bytes=logical, physical_bytes=physical)
        out: dict[str, Any] = {
            "queue": cap.get("queue", {}),
            "storage": {
                "logical_bytes": logical,
                "physical_bytes": physical,
                "dedup_ratio": round(logical / physical, 4) if physical > 0 else 1.0,
            },
            "status": cap.get("status"),
            "degraded_reasons": cap.get("degraded_reasons", []),
        }
        if tenant_id is not None:
            out["tenant_quota"] = telemetry.tenant_quota_snapshot(
                self.repository,
                tenant_id=tenant_id,
                quota_bytes=self.config.quota_tenant_bytes,
            )
        return out

    def audit_events(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId | str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        before_id: int | None = None,
    ) -> dict[str, Any]:
        """Return a tenant-scoped page of operational audit events."""
        return self.audit.list_events(
            tenant_id=_clean(tenant_id, "tenant_id"),
            document_id=str(document_id) if document_id is not None else None,
            event_type=event_type,
            limit=limit,
            before_id=before_id,
        )

    def run_maintenance(
        self, *, dry_run: bool = False, actor_id: str | None = None
    ) -> dict[str, Any]:
        """Run one garbage-collection pass (dry-run or real) and audit it."""
        if not dry_run:
            self._require_writes_enabled()
        report = self.gc.collect(dry_run=dry_run)
        data = report.to_dict()
        # Audit under a system tenant so operator GC actions are recorded.
        self.audit.record(
            tenant_id="system",
            event_type="gc",
            action="dry_run" if dry_run else "collect",
            outcome="failure" if report.errors else "success",
            actor_id=actor_id,
            detail={
                "dry_run": dry_run,
                "deleted_blobs": report.deleted_blobs,
                "deleted_manifests": report.deleted_manifests,
                "deleted_rows": report.deleted_blob_rows,
                "reclaimed_bytes": report.reclaimed_bytes,
                "batch": report.budget,
            },
        )
        return data

    def readiness(self) -> dict[str, Any]:
        """Report multi-replica readiness of document storage (truthful)."""
        data = document_storage_readiness(
            jobs_repo=self.jobs, metadata_repo=self.repository
        )
        try:
            from .malware import probe_malware_scanner

            data["malware"] = {
                **probe_malware_scanner(),
                "mode": self.config.security_malware_scan,
                "fail_closed": self.config.security_malware_fail_closed,
            }
        except Exception:  # noqa: BLE001
            data["malware"] = {"available": False, "mode": self.config.security_malware_scan}
        rollout = get_document_rollout()
        data["rollout"] = rollout.to_dict()
        data["accepting_durable_writes"] = rollout.enabled
        if not rollout.enabled:
            data["underlying_status"] = data["status"]
            data["status"] = "disabled"
            reasons = list(data.get("degraded_reasons", []))
            if "durable_writes_disabled" not in reasons:
                reasons.append("durable_writes_disabled")
            data["degraded_reasons"] = reasons
        return data

    @staticmethod
    def _require_writes_enabled() -> None:
        rollout = get_document_rollout()
        if not rollout.enabled:
            raise DocumentIngestionError(
                "document_platform_disabled",
                (
                    "Durable document writes are disabled. Existing documents, "
                    "jobs, and readiness data remain available."
                ),
            )

    def _storage_bytes(self) -> tuple[int, int]:
        """Return (logical_referenced_bytes, physical_on_disk_bytes)."""
        logical = 0
        physical = 0
        try:
            with self.repository._lock:  # noqa: SLF001
                row = self.repository._conn.execute(  # noqa: SLF001
                    """
                    SELECT
                      COALESCE(SUM(byte_size), 0) AS logical,
                      COALESCE(SUM(CASE WHEN rn = 1 THEN byte_size ELSE 0 END), 0) AS physical
                    FROM (
                      SELECT byte_size,
                             ROW_NUMBER() OVER (PARTITION BY sha256, storage_kind ORDER BY id) AS rn
                      FROM document_blobs
                    )
                    """
                ).fetchone()
            logical = int(row["logical"])
            physical = int(row["physical"])
        except Exception:  # noqa: BLE001
            logger.debug("[documents.ingestion] storage byte accounting failed", exc_info=True)
        return logical, physical

    def _audited_artifact(
        self,
        result: Any,
        *,
        tenant: str,
        actor: str,
        doc_id: Any,
        event_type: str,
        action: str,
        detail: dict[str, Any] | None = None,
        failure_metric: str | None = None,
    ) -> dict[str, Any]:
        """Unwrap an artifact result, recording an audit event + failure metric."""
        try:
            payload = self._artifact_payload(result)
        except DocumentIngestionError as exc:
            if failure_metric == "generation":
                telemetry.record_generation_failure(action)
            elif failure_metric == "redaction":
                telemetry.record_redaction_failure()
            self.audit.record(
                tenant_id=tenant,
                event_type=event_type,
                action=action,
                outcome="failure",
                actor_id=actor,
                document_id=doc_id,
                detail={"code": exc.code},
            )
            raise
        self.audit.record(
            tenant_id=tenant,
            event_type=event_type,
            action=action,
            outcome="success",
            actor_id=actor,
            document_id=doc_id,
            detail=detail or {},
        )
        return payload

    @staticmethod
    def _result_payload(result: Any) -> dict[str, Any]:
        """Unwrap a typed service result without turning failures into success."""

        serialized = result.to_dict()
        if not result.ok:
            raise DocumentIngestionError(
                result.code or "document_operation_failed",
                result.message or "Document operation failed",
            )
        payload = serialized.get("data")
        if not isinstance(payload, dict):
            raise DocumentIngestionError(
                "document_service_contract_error",
                "Document service returned an invalid result",
            )
        return payload

    @staticmethod
    def _artifact_payload(result: Any) -> dict[str, Any]:
        """Sanitize an artifact-producing result (never expose server paths)."""

        serialized = result.to_dict()
        if not result.ok:
            raise DocumentIngestionError(
                result.code or "document_operation_failed",
                result.message or "Document operation failed",
            )
        data = serialized.get("data")
        manifest: dict[str, Any] = {}
        if isinstance(data, dict):
            manifest = {
                key: value
                for key, value in data.items()
                if key not in _ARTIFACT_PATH_KEYS
            }
        return {
            "artifact_id": serialized.get("artifact_id"),
            "document_id": serialized.get("document_id"),
            "version_id": serialized.get("version_id"),
            "job_id": serialized.get("job_id"),
            "warnings": serialized.get("warnings", []),
            "manifest": manifest,
        }

    @staticmethod
    def _report_payload(result: Any) -> dict[str, Any]:
        """Sanitize a report-producing result (e.g. pdf-info)."""

        serialized = result.to_dict()
        if not result.ok:
            raise DocumentIngestionError(
                result.code or "document_operation_failed",
                result.message or "Document operation failed",
            )
        report = serialized.get("data")
        if not isinstance(report, dict):
            raise DocumentIngestionError(
                "document_service_contract_error",
                "Document service returned an invalid report",
            )
        return {
            "document_id": serialized.get("document_id"),
            "version_id": serialized.get("version_id"),
            "report": report,
        }

    def _resolve_current_version(
        self, *, tenant: str, actor: str, doc_id: DocumentId
    ) -> tuple[Any, Any]:
        """Return (document record, current version) with tenant+actor ACL."""

        try:
            record = self.repository.get_document(
                tenant_id=tenant, document_id=doc_id, actor_id=actor
            )
        except DocumentAccessError as exc:
            raise DocumentIngestionError(
                "document_access_denied", "Document is unavailable"
            ) from exc
        if record is None or record.current_version_id is None:
            raise DocumentIngestionError(
                "document_access_denied", "Document is unavailable"
            )
        try:
            version = self.repository.get_version(
                tenant_id=tenant, version_id=record.current_version_id, actor_id=actor
            )
        except DocumentAccessError as exc:
            raise DocumentIngestionError(
                "document_access_denied", "Document version is unavailable"
            ) from exc
        if version is None:
            raise DocumentIngestionError(
                "document_access_denied", "Document version is unavailable"
            )
        return record, version

    def _materialize_original(
        self, version: Any
    ) -> "DocumentIngestionService._Materialized":
        """Materialize only the immutable ``originals`` blob for a version."""

        blob_path = self.storage.blob_path(
            kind="originals", sha256=version.source_sha256
        )
        if not blob_path.is_file():
            raise DocumentIngestionError(
                "not_ready", "Document original bytes are unavailable"
            )
        return self._materialize(blob_path, version.original_filename)

    @staticmethod
    def _require_page(value: Any, name: str, *, allow_zero: bool = False) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise DocumentIngestionError("invalid_request", f"{name} must be an integer")
        if value < 0 or (value == 0 and not allow_zero) or (value < 1 and not allow_zero):
            raise DocumentIngestionError("invalid_request", f"{name} is out of range")
        return int(value)

    @staticmethod
    def _require_terms(terms: Any) -> list[str]:
        if not isinstance(terms, (list, tuple)) or not terms:
            raise DocumentIngestionError(
                "invalid_request", "At least one redaction term is required"
            )
        if len(terms) > 100:
            raise DocumentIngestionError("invalid_request", "Too many redaction terms")
        cleaned: list[str] = []
        for term in terms:
            if not isinstance(term, str):
                raise DocumentIngestionError(
                    "invalid_request", "Redaction terms must be strings"
                )
            text = term.strip()
            if not text:
                raise DocumentIngestionError(
                    "invalid_request", "Redaction terms must not be empty"
                )
            if len(text) > 256:
                raise DocumentIngestionError(
                    "invalid_request", "A redaction term is too long"
                )
            cleaned.append(text)
        return cleaned

    @staticmethod
    def _require_form_fields(fields: Any) -> dict[str, str]:
        if not isinstance(fields, dict) or not fields:
            raise DocumentIngestionError(
                "invalid_request", "At least one form field is required"
            )
        if len(fields) > 500:
            raise DocumentIngestionError("invalid_request", "Too many form fields")
        cleaned: dict[str, str] = {}
        for key, value in fields.items():
            if not isinstance(key, str) or not key.strip():
                raise DocumentIngestionError(
                    "invalid_request", "Form field names must be non-empty strings"
                )
            if not isinstance(value, str):
                raise DocumentIngestionError(
                    "invalid_request", "Form field values must be strings"
                )
            if len(key) > 256 or len(value) > 4096:
                raise DocumentIngestionError(
                    "invalid_request", "A form field name/value is too long"
                )
            cleaned[key] = value
        return cleaned

    def _bounded_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise DocumentIngestionError(
                "invalid_request", "Generation payload must be an object"
            )
        try:
            serialized = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise DocumentIngestionError(
                "invalid_request", "Generation payload is not JSON-serializable"
            ) from exc
        if len(serialized.encode("utf-8")) > _GENERATE_MAX_PAYLOAD_BYTES:
            raise DocumentIngestionError(
                "intake_too_large", "Generation payload exceeds the size limit"
            )
        if self._payload_depth(payload) > _GENERATE_MAX_PAYLOAD_DEPTH:
            raise DocumentIngestionError(
                "invalid_request", "Generation payload is too deeply nested"
            )
        return payload

    @staticmethod
    def _payload_depth(value: Any, _depth: int = 0) -> int:
        if _depth > _GENERATE_MAX_PAYLOAD_DEPTH:
            return _depth
        if isinstance(value, dict):
            return max(
                (
                    DocumentIngestionService._payload_depth(item, _depth + 1)
                    for item in value.values()
                ),
                default=_depth,
            )
        if isinstance(value, (list, tuple)):
            return max(
                (
                    DocumentIngestionService._payload_depth(item, _depth + 1)
                    for item in value
                ),
                default=_depth,
            )
        return _depth

    def _cleanup_transient_artifact(self, tenant: str, artifact: Any) -> None:
        """Best-effort removal of an unreferenced transient CAS artifact blob."""

        try:
            sha = artifact.manifest.output_sha256
            if self.repository.tenant_references_sha256(
                tenant_id=tenant, sha256=sha, storage_kind="artifacts"
            ):
                return
            path = self.storage.blob_path(kind="artifacts", sha256=sha)
            if path.is_file():
                path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 - cleanup must never raise
            logger.debug(
                "[documents.ingestion] transient artifact cleanup skipped",
                exc_info=True,
            )

    # ── Internal helpers ────────────────────────────────────────────────

    def _probe_mime(self, stored: Any, filename: str) -> str:
        path = self.storage.blob_path(kind="quarantine", sha256=stored.sha256)
        try:
            with self._materialize(path, filename) as staged:
                return sniff_document(staged, self.config).mime_type
        except DocumentParseError:
            # Defer the real rejection to the VALIDATING stage; store a
            # generic type so the immutable version can still be created.
            return "application/octet-stream"

    class _Materialized:
        __slots__ = ("_dir", "path")

        def __init__(self, blob_path: Path, filename: str) -> None:
            suffix = Path(filename).suffix or ""
            self._dir = Path(tempfile.mkdtemp(prefix="doc-stage-"))
            self.path = self._dir / f"source{suffix}"
            shutil.copyfile(blob_path, self.path)

        def __enter__(self) -> Path:
            return self.path

        def __exit__(self, *exc: object) -> None:
            shutil.rmtree(self._dir, ignore_errors=True)

    def _materialize(self, blob_path: Path, filename: str) -> "DocumentIngestionService._Materialized":
        return self._Materialized(blob_path, filename)

    def _write_manifest(self, job: DocumentJobRecord, ir: DocumentIR) -> None:
        payload = {
            "ir": ir.to_dict(),
            "ir_sha256": hashlib.sha256(ir.to_json().encode("utf-8")).hexdigest(),
        }
        self.storage.write_manifest(
            document_id=job.document_id,
            version_id=job.version_id,
            manifest=payload,
        )

    def _read_manifest(self, job: DocumentJobRecord) -> dict[str, Any] | None:
        return self.storage.read_manifest(
            document_id=job.document_id, version_id=job.version_id
        )

    def _transition(
        self,
        job: DocumentJobRecord,
        new_state: DocumentJobState,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        event_type: str = "transitioned",
    ) -> DocumentJobRecord:
        return self.jobs.transition(
            tenant_id=job.tenant_id,
            job_id=job.id,
            expected_state=job.state,
            expected_version=job.version,
            new_state=new_state,
            stage=new_state.value,
            error_code=error_code,
            error_message=error_message,
            event_type=event_type,
        )

    @staticmethod
    def _job_dict(job: DocumentJobRecord) -> dict[str, Any]:
        return {
            "job_id": str(job.id),
            "document_id": str(job.document_id),
            "version_id": str(job.version_id),
            "workspace_id": job.workspace_id,
            "state": job.state.value,
            "stage": job.stage,
            "attempt": job.attempt,
            "max_attempts": job.max_attempts,
            "cancel_requested": job.cancel_requested,
            "error_code": job.error_code,
            "error_message": job.error_message,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }


def create_default_ingestion_service(
    config: DocumentConfig | None = None,
) -> DocumentIngestionService:
    """Build a fully-wired coordinator, including the knowledge adapter.

    Best-effort: if the Knowledge stack is unavailable, ingestion still works
    (index/search operations then report the adapter is missing).
    """

    cfg = config or get_document_config()
    service = DocumentIngestionService(config=cfg)
    try:
        from kazma_core.stores.knowledge import get_knowledge_store
        from kazma_core.stores.knowledge_index import KnowledgeIndex

        store = get_knowledge_store()
        adapter = DocumentKnowledgeAdapter(
            repository=service.repository,
            knowledge_store=store,
            knowledge_index=KnowledgeIndex(store=store),
            config=cfg,
        )
        service.knowledge_adapter = adapter
        service.service.knowledge_adapter = adapter
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[documents.ingestion] knowledge adapter unavailable: %s", type(exc).__name__
        )
    return service


# ── Process-wide singleton (shared by tools / gateway / TUI) ────────────

_singleton: DocumentIngestionService | None = None


def set_ingestion_service(service: DocumentIngestionService | None) -> None:
    """Install the process-wide ingestion coordinator (called by app startup)."""
    global _singleton
    _singleton = service


def get_ingestion_service() -> DocumentIngestionService:
    """Return the shared coordinator, lazily creating one if unset.

    In the Web UI process the app installs the coordinator with a running
    worker pool via :func:`set_ingestion_service`. In headless/CLI/gateway
    processes a lazy default is created; callers that need processing must
    ensure workers are started (``await service.start_workers()``).
    """
    global _singleton
    if _singleton is None:
        _singleton = create_default_ingestion_service()
    return _singleton


def _clean(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocumentIngestionError("invalid_request", f"{name} is required")
    return value.strip()


def _safe_filename(filename: str) -> str:
    name = Path(str(filename or "").strip()).name
    if not name or name in {".", ".."}:
        raise DocumentIngestionError("invalid_request", "A valid filename is required")
    return name
