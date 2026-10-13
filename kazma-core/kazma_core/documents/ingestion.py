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
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from kazma_core.db.pg_helpers import store_errors

from . import telemetry
from .admission import IntakeAdmissionStore, current_owner, lease_lapsed, owner_state
from .audit import DocumentAuditStore
from .capacity import CapacityError, DocumentCapacityGuard
from .config import DocumentConfig, get_document_rollout
from .errors import DocumentParseError, infrastructure_errors, storage_full
from .jobs import (
    DocumentJobRecord,
    DocumentJobRepository,
    IntakeFencedError,
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
    new_version_id,
)
from .policy import DocumentPolicy, policy_operation
from .repository import DocumentAccessError, DocumentRepository
from .retention import DocumentGarbageCollector
from .service import DocumentService
from .storage import (
    BlobChecksumError,
    BlobLengthError,
    BlobTooLargeError,
    ContentAddressedStorage,
    StorageQuotaExceeded,
    bounded_stream_digest,
)
from .worker import (
    DocumentWorker,
    DocumentWorkerManager,
    PermanentDocumentError,
    StageContext,
    StageResult,
    TransientDocumentError,
)
from .worker_container import isolation_report

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentIngestionError",
    "DocumentIngestionService",
    "IngestionResult",
    "create_default_ingestion_service",
    "get_ingestion_service",
    "peek_ingestion_service",
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
        self._policy = DocumentPolicy(config)
        initial = self._policy.initial
        self.storage = storage or ContentAddressedStorage(initial.storage_root)
        sqlite_meta = repository or DocumentRepository(
            initial.storage_root / "documents.db",
            tenant_quota_bytes=initial.quota_tenant_bytes,
        )
        # Prefer Postgres multi-replica metadata when configured; fall back to SQLite.
        from .repository_pg import resolve_document_repository

        self.repository = resolve_document_repository(
            sqlite_meta,
            tenant_quota_bytes=initial.quota_tenant_bytes,
        )
        # Job queue needs a SQLite DocumentRepository only as a local fallback
        # when Postgres jobs are unavailable. If metadata is already SQLite,
        # share that connection; otherwise use a sidecar jobs-only DB.
        if getattr(self.repository, "backend_name", "sqlite") == "postgres":
            jobs_meta = DocumentRepository(
                initial.storage_root / "documents-jobs-fallback.db",
                tenant_quota_bytes=initial.quota_tenant_bytes,
            )
        else:
            jobs_meta = self.repository
        sqlite_jobs = jobs or DocumentJobRepository(
            jobs_meta,
            retry_base_seconds=float(initial.worker_retry_base_seconds),
            retry_max_seconds=float(initial.worker_retry_max_seconds),
        )
        # Use the Postgres multi-replica job queue when configured; otherwise
        # (or if the pool is unavailable) stay on the single-node SQLite queue.
        self.jobs = resolve_job_repository(
            sqlite_jobs,
            retry_base_seconds=float(initial.worker_retry_base_seconds),
            retry_max_seconds=float(initial.worker_retry_max_seconds),
        )
        self.knowledge_adapter = knowledge_adapter
        self.service = service or DocumentService(
            policy=self._policy,
            knowledge_adapter=knowledge_adapter,
            storage=self.storage,
            repository=self.repository,
        )
        # Phase 9 operations: append-only audit, backpressure guard, GC.
        self.audit = DocumentAuditStore(self.repository)
        self.admissions = IntakeAdmissionStore(self.jobs)
        self.admissions.reconcile_published()
        self._recover_stranded_intakes()
        self.capacity = DocumentCapacityGuard(
            config=initial,
            policy=self._policy,
            jobs=self.jobs,
            storage_root=initial.storage_root,
            admissions=self.admissions,
        )
        self.gc = DocumentGarbageCollector(
            repository=self.repository,
            storage=self.storage,
            audit=self.audit,
            config=initial,
            policy=self._policy,
        )
        self._manager: DocumentWorkerManager | None = None
        self._intake_sweeper: asyncio.Task[None] | None = None
        self._index_reconcile: asyncio.Task[Any] | None = None
        self._reconcile_stop = threading.Event()
        #: Tenant -> when ``grant_operator_access`` last looked (monotonic).
        self._operator_checked: dict[str, float] = {}
        self._operator_lock = threading.Lock()

    @property
    def config(self) -> DocumentConfig:
        return self._policy.current

    # ── Worker lifecycle ────────────────────────────────────────────────

    @property
    def worker_running(self) -> bool:
        return self._manager is not None and self._manager.worker.running

    @policy_operation
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
        with self._policy.detached():
            await self._manager.start()
        from kazma_core.background import spawn_background

        self._intake_sweeper = spawn_background(
            self._sweep_stranded_intakes(), name="documents:stranded-intakes", never_completes=True
        )
        if self.knowledge_adapter is not None:
            self._reconcile_stop.clear()
            self._index_reconcile = spawn_background(
                self._reconcile_index_once(), name="documents:index-reconcile"
            )
        logger.info(
            "[documents.ingestion] worker pool started concurrency=%d",
            self.config.worker_concurrency,
        )

    async def _sweep_stranded_intakes(self) -> None:
        """Roll back dead owners' intakes while the service runs.

        Start-up recovery alone left an intake killed in ANOTHER process (a
        CLI import, a second worker) holding its capacity until this server
        restarted; the soak harness found it (2026-10-13, Q01). Only owners
        proven dead are touched, so a live intake -- here or elsewhere -- is
        never raced.
        """
        while True:
            await asyncio.sleep(_INTAKE_SWEEP_SECONDS)
            try:
                await asyncio.to_thread(self._recover_stranded_intakes)
            except store_errors() as exc:
                logger.warning("[documents.ingestion] stranded intake sweep failed (%s)", type(exc).__name__)

    async def _reconcile_index_once(self) -> None:
        """Bring the knowledge index back to what this store records, once
        per start. A restore of the documents store alone (the Postgres
        backup, ``backup_pg``) leaves the libraries without the chunks of
        documents it records as indexed; this re-publishes them."""
        try:
            report = await asyncio.to_thread(self.reconcile_index, stop=self._reconcile_stop)
        except store_errors() as exc:
            logger.warning("[documents.ingestion] index reconcile failed (%s)", type(exc).__name__)
            return
        if report.get("reindexed") or report.get("failed") or report.get("library_missing"):
            logger.log(
                logging.WARNING if report.get("failed") else logging.INFO,
                "[documents.ingestion] index reconcile: reindexed=%d failed=%d "
                "library_missing=%d stale_version=%d unavailable=%d extra=%d orphaned=%d",
                len(report["reindexed"]), len(report["failed"]), len(report["library_missing"]),
                len(report["stale_version"]), len(report["unavailable"]),
                len(report["extra"]), len(report["orphaned"]),
            )

    @policy_operation
    def reconcile_index(
        self,
        *,
        apply: bool = True,
        prune: bool = False,
        tenant_id: str | None = None,
        stop: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Compare what this store records as searchable with what the
        knowledge libraries hold, and repair the difference.

        The documents store is the truth: ``document_chunks`` rows that are
        active and indexed say a version must be searchable in a library.
        A version the library does not hold is re-published from its own
        manifest, as the document's owner, through the normal publication
        path (``index_document_ir``). Each one is re-checked under the
        document's publication lock first, so an unindex or a delete that
        ran meanwhile is never undone. Nothing is invented: a library that
        does not exist here is reported (``library_missing``), and so is a
        version that is no longer the document's current one
        (``stale_version``) or whose manifest is gone (``unavailable``).

        The other direction is reported and removed only with ``prune``:
        ``extra`` -- a live document the store records as not indexed in
        that library -- and ``orphaned`` -- a document this store does not
        have, or has deleted. Both stay searchable until then.

        ``apply=False`` reports what would be done and changes nothing;
        ``tenant_id`` limits both sides to one tenant.
        """
        adapter = self.knowledge_adapter
        report: dict[str, Any] = {
            "ok": adapter is not None, "applied": bool(apply), "checked": 0,
            "reindexed": [], "needs_reindex": [], "failed": [], "library_missing": [],
            "stale_version": [], "unavailable": [], "extra": [], "orphaned": [], "pruned": [],
            "stopped": False,
        }
        if adapter is None:
            report["reason"] = "knowledge_unavailable"
            return report
        wanted = self.repository.active_index_entries()
        held = adapter.store.document_index_entries()
        if tenant_id is not None:
            only = _clean(tenant_id, "tenant_id")
            wanted = [item for item in wanted if item[0] == only]
            held = {item for item in held if item[0] == only}
        report["checked"] = len(wanted)
        wanted_places = {(tenant, library, document) for tenant, library, document, _v in wanted}

        def entry(tenant: str, library: str, document: str, version: str) -> dict[str, str]:
            return {"tenant_id": tenant, "library_id": library,
                    "document_id": document, "version_id": version}

        for tenant, library, document, version in wanted:
            if (tenant, library, document, version) in held:
                continue
            if stop is not None and stop.is_set():
                report["stopped"] = True
                break
            item = entry(tenant, library, document, version)
            doc_id = DocumentId(document)
            with adapter.publication_lock(tenant, doc_id):
                still = self.repository.list_document_chunks(
                    tenant_id=tenant, library_id=library, document_id=doc_id,
                    version_id=VersionId(version),
                )
                if not still:
                    continue  # unindexed or deleted while this ran
                if adapter.store.get_library_for_tenant(library, tenant) is None:
                    report["library_missing"].append(item)
                    continue
                record = self.repository.get_document(tenant_id=tenant, document_id=doc_id)
                if record is None:
                    report["unavailable"].append({**item, "reason": "document_missing"})
                    continue
                if str(record.current_version_id) != version:
                    report["stale_version"].append(
                        {**item, "current_version_id": str(record.current_version_id)}
                    )
                    continue
                manifest = self.storage.read_manifest(
                    document_id=record.id, version_id=record.current_version_id
                )
                if manifest is None or "ir" not in manifest:
                    report["unavailable"].append({**item, "reason": "manifest_missing"})
                    continue
                report["needs_reindex"].append(item)
                if not apply:
                    continue
                result = adapter.index_document_ir(
                    DocumentIR.from_dict(manifest["ir"]),
                    tenant_id=tenant, actor_id=record.owner_id, library_id=library,
                )
                if not result.ok:
                    report["failed"].append({**item, "code": result.code})
                    continue
                report["reindexed"].append(item)
                self.audit.record(
                    tenant_id=tenant, event_type="index", action="reconcile",
                    outcome="success", actor_id=record.owner_id, document_id=doc_id,
                    version_id=record.current_version_id, detail={"library_id": library},
                )

        wanted_keys = set(wanted)
        for tenant, library, document, version in sorted(held - wanted_keys):
            if (tenant, library, document) in wanted_places:
                continue  # an older version the re-publication above retires
            item = entry(tenant, library, document, version)
            try:
                found: DocumentId | None = DocumentId(document)
            except ValueError:
                found = None
            record = (
                self.repository.get_document(tenant_id=tenant, document_id=found, include_deleted=True)
                if found is not None else None
            )
            kind = "extra" if record is not None and record.deleted_at is None else "orphaned"
            report[kind].append(item)
            if not (prune and apply):
                continue
            with adapter.publication_lock(tenant, document):
                if found is not None and library in self.repository.list_indexed_libraries(
                    tenant_id=tenant, document_id=found
                ):
                    continue  # indexed meanwhile
                adapter.index.unindex_document(
                    tenant_id=tenant, library_id=library, document_id=document
                )
            report["pruned"].append(item)
        return report

    async def stop_workers(self) -> None:
        """Stop the worker pool, awaiting in-flight stages (idempotent)."""
        sweeper, self._intake_sweeper = self._intake_sweeper, None
        if sweeper is not None:
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)
        reconcile, self._index_reconcile = self._index_reconcile, None
        if reconcile is not None:
            # The thread finishes the document it is on and stops.
            self._reconcile_stop.set()
            await asyncio.gather(reconcile, return_exceptions=True)
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

    @policy_operation
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
            force_ocr = current.force_ocr if current.request_digest else bool(version and version.metadata.get("force_ocr"))
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

    @policy_operation
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

        Raises :class:`~kazma_core.documents.capacity.CapacityError` (409/429/503/
        507) if backpressure/rate/storage limits are exceeded, before any
        bytes are stored.
        """

        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        workspace = _clean(workspace_id, "workspace_id")
        actor = _clean(actor_id, "actor_id")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key.strip()
            or len(idempotency_key) > 256
        ):
            raise DocumentIngestionError("invalid_request", "idempotency_key must contain 1 to 256 characters")
        safe_name = _safe_filename(filename)
        doc_title = (title or safe_name).strip() or safe_name
        if content_length is not None and (
            isinstance(content_length, bool) or not isinstance(content_length, int) or content_length < 0
        ):
            raise DocumentIngestionError("invalid_request", "content_length must be a non-negative integer")
        if content_length is not None and content_length > self.config.intake_max_bytes:
            raise DocumentIngestionError("intake_too_large", "Document exceeds the configured intake size limit")
        attempts = max(1, int(max_attempts) if max_attempts else self.config.worker_max_retries)
        request_digest = hashlib.sha256(json.dumps({
            "actor_id": actor, "workspace_id": workspace, "filename": safe_name,
            "title": doc_title, "document_id": str(DocumentId(document_id)) if document_id is not None else None,
            "force_ocr": bool(force_ocr), "max_attempts": attempts,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        # Deterministic ids on idempotent replay.
        det_doc = det_ver = det_job = None
        if idempotency_key:
            key = idempotency_key.strip()
            if not key:
                raise DocumentIngestionError("invalid_request", "idempotency_key is empty")
            det_job = JobId(_ingest_identifier(tenant, key, "job"))
            # The stored tenant/key constraint is the lookup authority. Older
            # jobs retain their IDs when the derivation changes.
            existing = self.jobs.get_by_idempotency_key(tenant_id=tenant, idempotency_key=key)
            if existing is not None:
                # Authorize before returning even an opaque id or state. A
                # tenant-level key is not authority to inspect another actor:
                # its holder gets the same conflict as a different request,
                # never a sign of whose document the key named.
                try:
                    self._authorize_job_read(tenant, existing.document_id, actor)
                except DocumentAccessError as exc:
                    raise DocumentIngestionError(
                        "idempotency_conflict", "Idempotency key belongs to another request"
                    ) from exc
                version = self.repository.get_version(
                    tenant_id=tenant, version_id=existing.version_id, actor_id=actor
                )
                if version is None or existing.request_digest != request_digest:
                    raise DocumentIngestionError(
                        "idempotency_conflict", "Idempotency key belongs to another request"
                    )
                try:
                    replay_sha, replay_size = bounded_stream_digest(source, max_bytes=self.config.intake_max_bytes)
                except BlobTooLargeError as exc:
                    raise DocumentIngestionError("intake_too_large", str(exc)) from exc
                if replay_sha != version.source_sha256:
                    raise DocumentIngestionError(
                        "idempotency_conflict", "Idempotency key belongs to different content"
                    )
                if content_length is not None and replay_size != content_length:
                    raise DocumentIngestionError("intake_length_mismatch", "Document length does not match the declared length")
                return IngestionResult(
                    document_id=existing.document_id,
                    version_id=existing.version_id,
                    job_id=existing.id,
                    state=existing.state,
                    reused=True,
                )
            det_doc = DocumentId(_ingest_identifier(tenant, key, "doc"))
            det_ver = VersionId(_ingest_identifier(tenant, key, "ver"))

        # Recheck write authority again at publication, including revocation.
        if document_id is not None and not self.repository.has_access(
            tenant_id=tenant, document_id=DocumentId(document_id), actor_id=actor, permission="write"
        ):
            raise DocumentIngestionError("document_access_denied", "Target document is unavailable")
        det_job = det_job or JobId(uuid.uuid4())
        key = idempotency_key.strip() if idempotency_key else f"intake:{det_job}"
        try:
            self.capacity.check_intake(tenant_id=tenant, byte_size=content_length or 0)
            reservation = self.admissions.reserve(
                tenant_id=tenant, request_key=key, request_digest=request_digest,
                job_id=str(det_job), byte_size=content_length if content_length is not None else self.config.intake_max_bytes,
                config=self.config,
            )
        except CapacityError as exc:
            self.audit.record(
                tenant_id=tenant, event_type="intake", action="refused", outcome="denied",
                actor_id=actor, workspace_id=workspace,
                detail={"reason": exc.reason, "http_status": exc.status, "code": exc.code},
            )
            telemetry.record_intake(accepted=False)
            telemetry.record_intake_rejection(exc.reason)
            raise
        published = False
        lease = _IntakeLease(self.admissions, reservation, float(self.config.intake_lease_seconds))
        try:
            result = self._ingest_reserved(
                source, safe_name=safe_name, tenant=tenant, workspace=workspace, actor=actor,
                doc_title=doc_title, document_id=document_id, det_doc=det_doc, det_ver=det_ver,
                det_job=det_job, key=key, request_digest=request_digest, attempts=attempts,
                force_ocr=force_ocr, content_length=content_length, reservation=reservation,
                lease=lease,
            )
            published = True
            return result
        except IntakeFencedError as exc:
            raise DocumentIngestionError(
                "intake_taken_over",
                "This upload took too long to finish and was cancelled; nothing was saved. Retry it.",
            ) from exc
        finally:
            lease.stop()
            if not published:
                # All or nothing: undo what this intake published before it
                # failed, so the reservation can close and a retry starts clean.
                self._abandon_own_intake(reservation)
            self.admissions.finish(reservation)

    @policy_operation
    def precheck_upload(
        self, *, tenant_id: str, actor_id: str, workspace_id: str, byte_size: int | None,
    ) -> None:
        """Answer an upload BEFORE its body is received (2026-10-13, F08).

        Every refusal ``ingest_stream`` would give for budget reasons --
        writes disabled, too large, storage floor, queue, tenant caps, rate
        window -- is given here, with nothing consumed. The body used to be
        received in full (up to the intake limit) and spooled to the system
        temp directory before any of these was asked. ``ingest_stream``
        remains the authority and re-checks everything.
        """
        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        _clean(actor_id, "actor_id")
        _clean(workspace_id, "workspace_id")
        # A required scanner that cannot run refuses every upload; say so
        # before the body (DOC-A9). Intake still scans and stays the authority.
        from .malware import malware_blocks_intake

        blocked = malware_blocks_intake(self.config)
        if blocked:
            raise DocumentIngestionError(
                blocked, "Uploads are paused: a malware scan is required and no scanner can run",
            )
        if byte_size is not None and byte_size > self.config.intake_max_bytes:
            raise DocumentIngestionError("intake_too_large", "Document exceeds the configured intake size limit")
        expected = byte_size if byte_size is not None else self.config.intake_max_bytes
        self.capacity.check_intake(tenant_id=tenant, byte_size=expected)
        self.admissions.check(tenant_id=tenant, byte_size=expected, config=self.config)

    def upload_spool_dir(self) -> Path:
        """Where the Web route spools a body: under the storage root, so the
        free-space floor measures it and garbage collection removes what a
        crash left (it used to be the system temp directory)."""
        path = self.config.storage_root / ".upload-staging"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _ingest_reserved(
        self, source: BinaryIO | Iterable[bytes], *, safe_name: str, tenant: str,
        workspace: str, actor: str, doc_title: str, document_id: DocumentId | str | None,
        det_doc: DocumentId | None, det_ver: VersionId | None, det_job: JobId,
        key: str, request_digest: str, attempts: int, force_ocr: bool,
        content_length: int | None, reservation: str, lease: _IntakeLease | None = None,
    ) -> IngestionResult:
        # 1) Stream to quarantine CAS (bounded + tenant-quota enforced).
        try:
            stored = self.storage.put_stream(
                source,
                kind="quarantine",
                max_bytes=self.config.intake_max_bytes,
                tenant_id=tenant,
                repository=self.repository,
                tenant_quota_bytes=self.config.quota_tenant_bytes,
                expected_byte_size=content_length,
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
        except BlobLengthError as exc:
            self._audit_intake_rejection(tenant, workspace, actor, "intake_length_mismatch")
            raise DocumentIngestionError("intake_length_mismatch", str(exc)) from exc

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

        # 2) Register blob + document + immutable version. The ids are fixed
        # and recorded first, and every row this intake creates is tagged
        # with its reservation, so a rollback removes exactly those rows.
        planned_doc = DocumentId(document_id) if document_id is not None else (det_doc or new_document_id())
        planned_ver = det_ver or new_version_id()
        if lease is not None:
            lease.check()
        self.admissions.mark_publishing(
            reservation,
            byte_size=stored.byte_size,
            source_sha256=stored.sha256,
            document_id=str(planned_doc),
            version_id=str(planned_ver),
            created_document=document_id is None,
        )
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
            doc_id = planned_doc
            self.repository.create_document(
                tenant_id=tenant,
                owner_id=actor,
                title=doc_title,
                metadata={
                    "original_filename": safe_name,
                    "workspace_id": workspace,
                    "intake_token": reservation,
                },
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
            metadata={
                "force_ocr": bool(force_ocr),
                "intake_policy_revision": self._policy.revision,
                "intake_token": reservation,
            },
            version_id=planned_ver,
        )

        # 3) Enqueue durable job: the publication, and the fence. It commits
        # only while the reservation is still this process's (admission).
        if lease is not None:
            lease.check()
        job = self.jobs.enqueue(
            tenant_id=tenant,
            workspace_id=workspace,
            document_id=doc_id,
            version_id=version.id,
            idempotency_key=key,
            max_attempts=attempts,
            job_id=det_job,
            actor_id=actor, request_digest=request_digest, force_ocr=bool(force_ocr),
            intake_fence=(reservation, current_owner()),
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

    # ── unpublished intakes: own rollback and dead-owner recovery ──────────

    def _rollback_intake(self, row: Mapping[str, Any]) -> bool:
        """Undo one unpublished intake this process owns; True when released.

        A job for it means publication completed: the ordinary close applies.
        Otherwise the rows it created (tagged with its token) are removed and
        the reservation is released; its quarantine bytes are left to GC.
        """

        token, tenant = str(row["token"]), str(row["tenant_id"])
        if self.jobs.get(tenant_id=tenant, job_id=JobId(str(row["job_id"]))) is not None:
            self.admissions.finish(token)
            return True
        if int(row.get("publishing") or 0):
            self.repository.discard_unpublished_intake(
                tenant_id=tenant,
                intake_token=token,
                document_id=str(row.get("document_id") or ""),
                version_id=str(row.get("version_id") or ""),
                created_document=bool(int(row.get("created_document") or 0)),
            )
        return self.admissions.release_owned(token)

    def _discard_tagged(self, row: Mapping[str, Any]) -> bool:
        """Remove the rows one intake tagged with its token, unless its job
        exists (it published). Idempotent: only rows carrying the token."""
        tenant = str(row["tenant_id"])
        if self.jobs.get(tenant_id=tenant, job_id=JobId(str(row["job_id"]))) is not None:
            return False
        removed = self.repository.discard_unpublished_intake(
            tenant_id=tenant,
            intake_token=str(row["token"]),
            document_id=str(row.get("document_id") or ""),
            version_id=str(row.get("version_id") or ""),
            created_document=bool(int(row.get("created_document") or 0)),
        )
        return any(removed.values())

    def _abandon_own_intake(self, token: str) -> None:
        """Best effort, never raising: the intake's own error is the answer."""

        try:
            row = self.admissions.get(token)
            if row is None or not int(row.get("publishing") or 0):
                return  # nothing published: ``finish`` closes it
            if str(row.get("owner") or "") != current_owner():
                # Taken over (fenced): remove what this process wrote under
                # its token -- possibly after the new owner's rollback. The
                # reservation is no longer ours to release.
                self._discard_tagged(row)
                return
            if not int(row.get("active") or 0):
                return
            self._rollback_intake(row)
        except store_errors() + (RuntimeError, ValueError, OSError) as exc:
            logger.warning(
                "[documents.ingestion] a failed intake could not be rolled back (%s); "
                "its reservation stays held until this process restarts",
                type(exc).__name__,
            )

    def _recover_stranded_intakes(self) -> int:
        """Roll back intakes whose owner is provably dead or whose lease lapsed.

        Runs when a service starts and every minute while it runs. A dead
        owner (this host) cannot publish again. An owner that cannot be
        proven dead (another host, an unreadable process) is taken over only
        once its lease has lapsed, and is then fenced (``admission``). A
        legacy row (no owner or no heartbeat) is never taken over: it stays
        held and is reported (``capacity.admission.stranded``). Rows a fenced
        owner wrote after its takeover are removed for a day after it.
        """

        try:
            rows = self.admissions.unpublished()
            now = self.admissions.now_epoch()
        except store_errors() as exc:
            logger.warning("[documents.ingestion] stranded intake scan failed (%s)", type(exc).__name__)
            return 0
        recovered = 0
        for row in rows:
            owner = str(row.get("owner") or "")
            state = owner_state(owner)
            if state == "dead":
                expected_heartbeat = None
            elif state != "self" and lease_lapsed(row, now):
                expected_heartbeat = float(row["heartbeat_epoch"])
            else:
                continue
            token = str(row["token"])
            if not self.admissions.claim(
                token, expected_owner=owner, expected_heartbeat=expected_heartbeat
            ):
                continue  # another recoverer took it, its lease was renewed, or it published
            try:
                if self._rollback_intake(row):
                    recovered += 1
            except store_errors() + (RuntimeError, ValueError, OSError) as exc:
                logger.warning(
                    "[documents.ingestion] stranded intake %s could not be rolled back (%s); "
                    "a later start retries it",
                    token, type(exc).__name__,
                )
        if recovered:
            logger.info("[documents.ingestion] rolled back %d intake(s) left by a stopped process", recovered)
        try:
            for row in self.admissions.fenced_recent():
                if self._discard_tagged(row):
                    logger.info(
                        "[documents.ingestion] removed rows a fenced intake wrote after its takeover (%s)",
                        row["token"],
                    )
        except store_errors() + (RuntimeError, ValueError, OSError) as exc:
            logger.warning("[documents.ingestion] fenced intake sweep failed (%s)", type(exc).__name__)
        return recovered

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
            if exc.retryable:
                return self.jobs.record_failure(
                    tenant_id=job.tenant_id, job_id=job.id,
                    expected_state=job.state, expected_version=job.version,
                    owner="intake", error_code=exc.code,
                    error_message=exc.safe_message, transient=True,
                )
            return self._transition(
                job,
                DocumentJobState.REJECTED,
                error_code=exc.code,
                error_message=exc.safe_message,
                event_type="rejected",
            )
        except infrastructure_errors() as exc:
            # Transient infra fault (disk full while staging, OS error re-
            # reading the quarantine blob, momentary DB error on SQLite OR
            # Postgres -- a psycopg error used to reach the generic handler
            # below and REJECT the document permanently). The worker
            # tier already routes TransientDocumentError to RETRY_WAIT; do the
            # same here instead of permanently REJECTing (terminal) — a flaky
            # disk shouldn't force the user to re-upload (audit finding).
            logger.warning(
                "[documents.ingestion] transient validation failure job=%s type=%s",
                job.id, type(exc).__name__,
            )
            # Route through record_failure (the exact worker-tier path) so the
            # row keeps its ACTIVE stage ("validating") and gets retry_at
            # stamped with the shared backoff helper — a bare transition to
            # RETRY_WAIT leaves stage="retry_wait" with retry_at NULL, which
            # claim_next/_claim_target can never reclaim (orphan).
            return self.jobs.record_failure(
                tenant_id=job.tenant_id,
                job_id=job.id,
                expected_state=job.state,
                expected_version=job.version,
                owner="intake",
                error_code="storage_full" if storage_full(exc) else "validation_transient",
                error_message=(
                    f"Document validation hit a transient error ({type(exc).__name__})"
                ),
                transient=True,
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
            # A validated immutable source can have lost its redundant
            # quarantine copy to GC (or restore). Revalidate the original
            # against today's policy instead of making recovery depend on it.
            quarantine_path = self.storage.blob_path(kind="originals", sha256=version.source_sha256)
            if not quarantine_path.is_file():
                raise DocumentParseError(
                    "Document source bytes are missing", code="document_missing"
                )
        with self._materialize(quarantine_path, version.original_filename) as staged:
            sniffed = self.service.inspect_path(staged)
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
            DocumentJobState.VALIDATING: self._stage_validate,
            DocumentJobState.PARSING: self._stage_parse,
            DocumentJobState.OCR_RUNNING: self._stage_ocr,
            DocumentJobState.NORMALIZING: self._stage_normalize,
            DocumentJobState.INDEXING: self._stage_index,
            DocumentJobState.VERIFYING: self._stage_verify,
        }

    @policy_operation
    def _stage_validate(
        self, job: DocumentJobRecord, context: StageContext
    ) -> StageResult:
        context.raise_if_cancelled()
        self._validate_and_promote(tenant=job.tenant_id, actor=None, job=job)
        version = self.repository.get_version(
            tenant_id=job.tenant_id, version_id=job.version_id
        )
        target = (
            DocumentJobState.OCR_REQUIRED
            if (job.force_ocr if job.request_digest else bool(version and version.metadata.get("force_ocr")))
            else DocumentJobState.READY_TO_PARSE
        )
        return StageResult(target, stage=target.value)

    @policy_operation
    def _stage_parse(
        self, job: DocumentJobRecord, context: StageContext
    ) -> StageResult:
        context.raise_if_cancelled()
        self._parse_to_manifest(job, force_ocr=False)
        return StageResult(DocumentJobState.NORMALIZING, stage="normalizing")

    @policy_operation
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
        except Exception as exc:
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
        from .extract_salvage import salvage_audit_scope

        record = self.repository.get_document(
            tenant_id=job.tenant_id, document_id=job.document_id
        )
        if record is None:
            raise PermanentDocumentError("document_missing", "Document is unavailable")
        actor = job.actor_id

        def audit_egress(provider: str, path: Path, size: int, phase: str) -> None:
            if not isinstance(actor, str) or not actor.strip():
                from .errors import DocumentSecurityError

                raise DocumentSecurityError(
                    "Remote parsing requires the original operation's actor",
                    code="remote_parse_audit_unavailable",
                )
            self.audit.record(
                tenant_id=job.tenant_id, actor_id=actor,
                workspace_id=job.workspace_id, document_id=job.document_id,
                version_id=job.version_id, job_id=job.id,
                event_type="egress", action=f"remote_parse_{phase}",
                outcome="failure" if phase == "failure" else "success",
                detail={"provider": provider, "bytes": int(size)}, required=True,
            )

        with salvage_audit_scope(audit_egress):
            self._parse_to_manifest_impl(job, force_ocr=force_ocr)

    def _parse_to_manifest_impl(self, job: DocumentJobRecord, *, force_ocr: bool) -> None:
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
        force = force_ocr or (job.force_ocr if job.request_digest else bool(version.metadata.get("force_ocr")))
        if self.capacity.below_floor(extra_bytes=2 * int(getattr(version, "byte_size", 0) or 0)):
            # Parsing stages a copy and writes a run directory and a manifest:
            # held and retried, never started into a full disk (F08).
            raise TransientDocumentError(
                "storage_full", "Document storage is below its free-space floor"
            )
        # A queued job must obey today's malware policy, not intake's old one.
        from .malware import scan_if_configured

        scan_if_configured(originals_path, self.config)
        with self._materialize(originals_path, version.original_filename) as staged:
            parsed = self.service.parse_ingested_blob(staged, force_ocr=force)
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

    @policy_operation
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

    #: How often a tenant is looked over again for documents the operator
    #: cannot open (a document ingested under an older name, or before a
    #: switch from multi-user mode).
    OPERATOR_GRANT_INTERVAL_SECONDS = 60.0

    def grant_operator_access(self, *, tenant_id: str, force: bool = False) -> int:
        """Give the install's operator an ``owner`` ACL row on every document.

        Single-operator installs only (``documents.principal``; the caller
        decides). Each grant is made by the document's own owner through the
        normal ACL path and audited; ownership never changes, and leaving
        single-operator mode is enough to stop using the grants. Returns how
        many were added. Blocking.
        """
        from .principal import OPERATOR

        tenant = _clean(tenant_id, "tenant_id")
        now = time.monotonic()
        with self._operator_lock:
            last = self._operator_checked.get(tenant)
            if not force and last is not None and now - last < self.OPERATOR_GRANT_INTERVAL_SECONDS:
                return 0
        granted = 0
        for record in self.repository.list_documents(tenant_id=tenant):
            if record.owner_id == OPERATOR or self.repository.has_access(
                tenant_id=tenant, document_id=record.id, actor_id=OPERATOR, permission="owner"
            ):
                continue
            self.repository.grant_access(
                tenant_id=tenant, document_id=record.id, actor_id=record.owner_id,
                principal_id=OPERATOR, permission="owner",
            )
            self.audit.record(
                tenant_id=tenant, event_type="access", action="grant_operator",
                outcome="success", actor_id=record.owner_id, document_id=record.id,
            )
            granted += 1
        with self._operator_lock:
            self._operator_checked[tenant] = now
        if granted:
            logger.info("[documents] gave the operator access to %d document(s) of tenant %s", granted, tenant)
        return granted

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
                tenant_id=tenant, document_id=record.id, actor_id=SYSTEM_ACTOR
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
        jobs = self.jobs_for_document(tenant_id=tenant, document_id=doc_id, actor_id=SYSTEM_ACTOR)
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
            "libraries": self._libraries_holding(tenant, record.id),
        }

    def _libraries_holding(self, tenant: str, doc_id: DocumentId) -> list[dict[str, str]] | None:
        """The Knowledge libraries a document is searchable in, named, for the
        Documents page's remove control (the page could add a document to a
        library and never take it out). None when that is unknown -- no
        knowledge wiring, or the lookup failed -- never an empty list that
        would claim the document is in none."""
        adapter = getattr(self.service, "knowledge_adapter", None)
        if adapter is None:
            return None
        try:
            held = adapter.libraries_holding(tenant_id=tenant, document_id=doc_id)
            out: list[dict[str, str]] = []
            for library_id in held:
                lib = adapter.store.get_library_for_tenant(library_id, tenant) or {}
                out.append({"id": library_id, "name": str(lib.get("name") or library_id)})
            return out
        except Exception:
            logger.warning("[documents] libraries of %s could not be read", doc_id, exc_info=True)
            return None

    def jobs_for_document(
        self, *, tenant_id: str, document_id: DocumentId | str,
        actor_id: str | _SystemActor,
    ) -> list[dict[str, Any]]:
        tenant = _clean(tenant_id, "tenant_id")
        doc_id = DocumentId(document_id)
        self._authorize_job_read(tenant, doc_id, actor_id)
        # Backend-agnostic: works whether the queue is SQLite or Postgres.
        job_ids = self.jobs.document_job_ids(tenant_id=tenant, document_id=doc_id)
        result: list[dict[str, Any]] = []
        for job_id in job_ids:
            job = self.jobs.get(tenant_id=tenant, job_id=JobId(job_id))
            if job is not None:
                result.append(self._job_dict(job))
        return result

    def job_status(
        self, *, tenant_id: str, job_id: JobId | str,
        actor_id: str | _SystemActor,
    ) -> dict[str, Any] | None:
        """The job, or None when it does not exist OR the actor cannot read
        its document -- the two are indistinguishable to the caller."""
        job = self._readable_job(tenant_id, job_id, actor_id)
        return self._job_dict(job) if job is not None else None

    def job_events(
        self, *, tenant_id: str, job_id: JobId | str,
        actor_id: str | _SystemActor,
    ) -> list[dict[str, Any]]:
        tenant = _clean(tenant_id, "tenant_id")
        if self._readable_job(tenant, job_id, actor_id) is None:
            raise JobNotFoundError("job is unavailable in this tenant")
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

    def _authorize_job_read(
        self, tenant: str, document_id: DocumentId, actor_id: str | _SystemActor,
        *, permission: str = "read",
    ) -> None:
        """Every transport passes its resolved actor; only ``SYSTEM_ACTOR``
        (code that already authorized the read) skips the check. A missing
        actor is refused -- it used to mean "no check" (2026-10-13, F05)."""
        if actor_id is SYSTEM_ACTOR:
            return
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise DocumentAccessError("document access requires an actor")
        if not self.repository.has_access(
            tenant_id=tenant, document_id=document_id,
            actor_id=_clean(actor_id, "actor_id"), permission=permission,
        ):
            raise DocumentAccessError("document is unavailable to this actor")

    def _readable_job(
        self, tenant_id: str, job_id: JobId | str, actor_id: str | _SystemActor,
        *, permission: str = "read",
    ) -> DocumentJobRecord | None:
        tenant = _clean(tenant_id, "tenant_id")
        job = self.jobs.get(tenant_id=tenant, job_id=JobId(job_id))
        if job is None:
            return None
        try:
            self._authorize_job_read(tenant, job.document_id, actor_id, permission=permission)
        except DocumentAccessError:
            if actor_id is not SYSTEM_ACTOR and (not isinstance(actor_id, str) or not actor_id.strip()):
                raise  # a caller bug, not an access answer
            return None
        return job

    def cancel_job(
        self, *, tenant_id: str, job_id: JobId | str, actor_id: str | _SystemActor
    ) -> dict[str, Any]:
        tenant = _clean(tenant_id, "tenant_id")
        # The same per-principal read gate as get_content: cancelling is
        # destructive control over another principal's ingestion. The actor
        # is required -- the agent tool used to omit it and cancel any job in
        # the tenant -- and an unreadable job is "not found".
        job = self._readable_job(tenant, job_id, actor_id)
        if job is None:
            raise JobNotFoundError("job is unavailable in this tenant")
        job = self.jobs.request_cancel(tenant_id=tenant, job_id=job.id)
        self.audit.record(
            tenant_id=tenant,
            event_type="cancel",
            action="request_cancel",
            outcome="success",
            actor_id=actor_id if isinstance(actor_id, str) else None,
            document_id=job.document_id,
            job_id=job.id,
            detail={"state": job.state.value},
        )
        return self._job_dict(job)

    @policy_operation
    def retry_job(
        self, *, tenant_id: str, job_id: JobId | str, actor_id: str | _SystemActor
    ) -> dict[str, Any]:
        """Re-enqueue a dead-lettered/rejected job as a fresh version job."""
        self._require_writes_enabled()
        tenant = _clean(tenant_id, "tenant_id")
        if self._readable_job(tenant, job_id, actor_id) is None:
            raise JobNotFoundError("job is unavailable in this tenant")
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
            tenant_id=tenant, document_id=job.document_id
        )
        if owner_record is None:
            raise DocumentAccessError("document is unavailable to this actor")
        self._authorize_job_read(tenant, job.document_id, actor_id, permission="write")
        actor_text = actor_id if isinstance(actor_id, str) else ""
        owner = owner_record.owner_id
        force = job.force_ocr if job.request_digest else bool(version.metadata.get("force_ocr"))
        identifier = JobId(uuid.uuid4())
        key = f"retry:{job.id}:{identifier}"
        digest = hashlib.sha256(json.dumps({
            "retry_of": str(job.id), "actor_id": actor_text, "force_ocr": force,
            "workspace_id": job.workspace_id, "source_sha256": version.source_sha256,
            "max_attempts": job.max_attempts,
        }, sort_keys=True).encode("utf-8")).hexdigest()
        self.capacity.check_intake(tenant_id=tenant)
        reservation = self.admissions.reserve(
            tenant_id=tenant, request_key=key, request_digest=digest,
            job_id=str(identifier), byte_size=0, config=self.config,
        )
        try:
            fresh = self.jobs.enqueue(
                tenant_id=tenant, workspace_id=job.workspace_id,
                document_id=job.document_id, version_id=job.version_id,
                idempotency_key=key, job_id=identifier, max_attempts=job.max_attempts,
                actor_id=actor_text, request_digest=digest, force_ocr=force,
                intake_fence=(reservation, current_owner()),
            )
        finally:
            self.admissions.finish(reservation)
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

    @policy_operation
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
        self, *, tenant_id: str, library_id: str, query: str, top_k: int = 5,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        result = await self.service.search_library(
            _clean(query, "query"),
            tenant_id=_clean(tenant_id, "tenant_id"),
            library_id=_clean(library_id, "library_id"),
            top_k=top_k,
            actor_id=(_clean(actor_id, "actor_id") if actor_id else None),
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

    @policy_operation
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

    @policy_operation
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

    @policy_operation
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

    @policy_operation
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

    @policy_operation
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

    @policy_operation
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

    @policy_operation
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
        except Exception as exc:  # ACL/lookup failures are denials
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

    @policy_operation
    def health(self) -> dict[str, Any]:
        data = self.service.health()
        data["policy"] = self._policy.status()
        data["rollout"] = get_document_rollout().to_dict()
        data["worker"] = {
            "running": self.worker_running,
            "concurrency": self.config.worker_concurrency,
        }
        data["storage_root"] = str(self.config.storage_root)
        return data

    # ── Phase 9 operations (capacity / metrics / audit / GC / readiness) ──

    @policy_operation
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

    @policy_operation
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

    @policy_operation
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

    @policy_operation
    def readiness(self) -> dict[str, Any]:
        """Report multi-replica readiness of document storage (truthful)."""
        data = document_storage_readiness(
            jobs_repo=self.jobs, metadata_repo=self.repository
        )
        data["policy"] = self._policy.status()
        try:
            from .malware import probe_malware_scanner

            data["malware"] = {
                **probe_malware_scanner(),
                "mode": self.config.security_malware_scan,
                "fail_closed": self.config.security_malware_fail_closed,
            }
        except Exception:  # noqa: BLE001
            data["malware"] = {"available": False, "mode": self.config.security_malware_scan}
        # Hostile-document execution profile: ``process`` is reported as
        # having no filesystem/network isolation; ``container`` is ready only
        # while the broker's attested policy holds.
        isolation = isolation_report()
        data["worker_isolation"] = isolation
        problems = []
        if not isolation.get("ready"):
            problems.append("document_worker_isolation_unavailable")
        try:
            stranded = int(self.admissions.snapshot().get("stranded", 0))
        except store_errors():
            stranded = 0  # the admission store's own outage is reported elsewhere
        data["stranded_intake_reservations"] = stranded
        if stranded:
            # Held by an owner no process can prove dead (another host, a
            # legacy row): capacity only the operator can free.
            problems.append("document_intake_reservations_stranded")
        if isolation.get("image_code_matches") is False:
            # The worker image runs other document code than this Kazma.
            problems.append("document_worker_image_outdated")
        if problems:
            reasons = list(data.get("degraded_reasons") or [])
            reasons += [reason for reason in problems if reason not in reasons]
            data["degraded_reasons"] = reasons
            if data.get("status") in {"ok", "ready"}:
                data["status"] = "degraded"
        rollout = get_document_rollout()
        data["rollout"] = rollout.to_dict()
        data["accepting_durable_writes"] = rollout.enabled
        try:
            from .malware import malware_blocks_intake

            blocked = malware_blocks_intake(self.config, data.get("malware"))
        except Exception:  # noqa: BLE001
            blocked = None
        if blocked:
            data["accepting_durable_writes"] = False
            reasons = list(data.get("degraded_reasons") or [])
            if blocked not in reasons:
                reasons.append(blocked)
            data["degraded_reasons"] = reasons
            if data.get("status") in {"ok", "ready"}:
                data["status"] = "degraded"
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
        _sql = """
            SELECT
              COALESCE(SUM(byte_size), 0) AS logical,
              COALESCE(SUM(CASE WHEN rn = 1 THEN byte_size ELSE 0 END), 0) AS physical
            FROM (
              SELECT byte_size,
                     ROW_NUMBER() OVER (PARTITION BY sha256, storage_kind ORDER BY id) AS rn
              FROM document_blobs
            )
        """
        try:
            if getattr(self.repository, "_conn", None) is not None:
                with self.repository._lock:  # noqa: SLF001
                    row = self.repository._conn.execute(_sql).fetchone()  # noqa: SLF001
                logical = int(row["logical"])
                physical = int(row["physical"])
            else:
                # Postgres metadata backend: _conn is None (pool-based).
                # Previously the bare _conn.execute raised AttributeError,
                # swallowed at debug → storage gauges reported 0 forever in
                # the multi-replica deployment (audit finding).
                _pool = getattr(self.repository, "_pool", None)
                if _pool is None:
                    return 0, 0
                with _pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(_sql)
                        row = cur.fetchone()
                    conn.commit()
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
                return self.service.inspect_path(staged).mime_type
        except DocumentParseError:
            # Defer the real rejection to the VALIDATING stage; store a
            # generic type so the immutable version can still be created.
            return "application/octet-stream"

    class _Materialized:
        __slots__ = ("_dir", "path")

        def __init__(self, blob_path: Path, filename: str, scratch: Path | None = None) -> None:
            suffix = Path(filename).suffix or ""
            if scratch is not None:
                scratch.mkdir(parents=True, exist_ok=True)
            self._dir = Path(tempfile.mkdtemp(prefix="doc-stage-", dir=scratch))
            self.path = self._dir / f"source{suffix}"
            shutil.copyfile(blob_path, self.path)

        def __enter__(self) -> Path:
            return self.path

        def __exit__(self, *exc: object) -> None:
            shutil.rmtree(self._dir, ignore_errors=True)

    def _materialize(self, blob_path: Path, filename: str) -> "DocumentIngestionService._Materialized":
        # Under the storage root: measured by the free-space floor and swept
        # by garbage collection if a crash leaves it behind.
        return self._Materialized(blob_path, filename, self.config.storage_root / ".stage")

    def _write_manifest(self, job: DocumentJobRecord, ir: DocumentIR) -> None:
        payload = {
            "policy_revision": self._policy.revision,
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

    service = DocumentIngestionService(config=config)
    cfg = service._policy.initial
    try:
        from kazma_core.stores.knowledge import get_knowledge_store
        from kazma_core.stores.knowledge_index import KnowledgeIndex

        store = get_knowledge_store()
        adapter = DocumentKnowledgeAdapter(
            repository=service.repository,
            knowledge_store=store,
            knowledge_index=KnowledgeIndex(store=store),
            config=cfg,
            policy=service._policy,
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


def peek_ingestion_service() -> DocumentIngestionService | None:
    """Return the installed coordinator without creating stores or workers."""
    return _singleton


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


class _IntakeLease:
    """Renews one intake's reservation while it runs (``admission``).

    A daemon thread renews every quarter of the lease. When a renewal finds
    the reservation no longer this process's, the intake has been taken over:
    ``check`` raises before the next write and the job's own fence refuses
    publication whatever this thread saw. A renewal that fails on a store
    error is retried at the next tick; a lease that lapses because the store
    stayed unreachable ends in the same fence.
    """

    def __init__(self, admissions: IntakeAdmissionStore, token: str, lease_seconds: float) -> None:
        self._admissions = admissions
        self._token = token
        self._interval = max(0.5, lease_seconds / 4.0)
        self._stop = threading.Event()
        self.fenced = False
        self._thread = threading.Thread(target=self._run, name="documents-intake-lease", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not self._admissions.heartbeat(self._token):
                    self.fenced = True
                    return
            except store_errors() as exc:
                logger.debug("[documents.ingestion] intake lease renewal failed (%s)", type(exc).__name__)

    def check(self) -> None:
        if self.fenced:
            raise IntakeFencedError("the intake's reservation was taken over")

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)


def _ingest_identifier(tenant: str, key: str, kind: str) -> uuid.UUID:
    # Delimiter joins alias ("a", "b:c") and ("a:b", "c") across tenants.
    return uuid.uuid5(_INGEST_NS, json.dumps([tenant, key, kind], separators=(",", ":")))


def _clean(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DocumentIngestionError("invalid_request", f"{name} is required")
    return value.strip()


#: How often a running service looks for intakes whose owner died.
_INTAKE_SWEEP_SECONDS = 60.0


class _SystemActor:
    """The service itself (listing it already authorized, recovery, tests).

    Not a string, so no request, header or chat message can produce it.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "SYSTEM_ACTOR"


#: Pass as ``actor_id`` only from code that has authorized the read itself.
SYSTEM_ACTOR = _SystemActor()


def _safe_filename(filename: str) -> str:
    name = Path(str(filename or "").strip()).name
    if not name or name in {".", ".."}:
        raise DocumentIngestionError("invalid_request", "A valid filename is required")
    return name
