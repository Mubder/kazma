"""Documents API — Web transport for the shared ``DocumentIngestionService``.

Thin FastAPI layer over the process-wide ingestion coordinator stored on
``app.state.documents`` (wired in ``app.py`` startup). It adds **no** parsing
path of its own — every operation delegates to the coordinator, which is the
sole orchestration boundary.

Scope resolution (never trusts a client-supplied path or tenant):
  * ``tenant_id``   — request-scoped :func:`get_current_tenant_id` (tenant
    middleware), single-user fallback ``"default"``.
  * ``actor_id``    — authenticated principal username, fallback ``"local"``.
  * ``workspace_id``— the active WorkspaceStore row id, fallback ``"default"``.

Uploads stream to a bounded temp file (the request is never materialized in
memory unbounded); the byte cap is enforced while reading, returning HTTP 413
on overflow. Only uploaded opaque IDs / workspace-safe local selections are
accepted — no arbitrary server paths from remote clients.

Endpoints (all under ``/api/documents``):
  POST   /                         streamed upload intake
  POST   /import                   workspace-safe local file intake
  POST   /generate                 generate + durably ingest a new document
  GET    /                         list documents (tenant/actor scoped)
  GET    /health                   capability + worker health
  POST   /merge                    merge several documents' PDFs (opaque IDs)
  GET    /artifacts/{artifact_id}/download  stream a derived artifact by ID
  GET    /{document_id}            document detail (versions + jobs + artifacts)
  GET    /{document_id}/versions   version list
  GET    /{document_id}/content    paged normalized content
  GET    /{document_id}/artifacts  list derived artifacts
  POST   /{document_id}/convert    convert current version to a target format
  GET    /{document_id}/pdf-info   structural report for a PDF version
  POST   /{document_id}/split      split a page range from a PDF version
  POST   /{document_id}/fill-form  fill AcroForm fields on a PDF version
  POST   /{document_id}/redact     physically redact terms from a PDF version
  POST   /{document_id}/index      publish current version to a library
  POST   /{document_id}/unindex    remove from a library
  POST   /{document_id}/delete     soft-delete (tombstone/archive); GC reclaims later
  DELETE /{document_id}            same as POST …/delete
  POST   /search                   library search (fenced)
  GET    /jobs/{job_id}            job status
  GET    /jobs/{job_id}/events     append-only job event history
  POST   /jobs/{job_id}/cancel     cooperative cancellation
  POST   /jobs/{job_id}/retry      re-enqueue a dead-lettered/rejected job

Failures return truthful HTTP status codes. No document content, filenames of
sensitive nature, or redaction terms are ever logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from kazma_core.diagnostic_scope import read_only_diagnostic
from kazma_core.documents.errors import DocumentPolicyError
from kazma_core.errors import safe_error

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["create_documents_router"]

_COPY_CHUNK = 1024 * 1024


def _active_workspace_id() -> str:
    """The active workspace's id, or ``"default"``.

    A WorkspaceStore read (a database query): run it in a thread.
    """
    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("id"):
            return str(active["id"]).strip() or "default"
    except Exception:  # pragma: no cover - defensive
        logger.debug("[documents_api] active workspace lookup failed", exc_info=True)
    return "default"


async def _resolve_scope(request: Request) -> tuple[str, str, str]:
    """Return (tenant_id, actor_id, workspace_id) from trusted server context.

    Async since 2026-10-01: the workspace is a store read and the principal
    may be a session-store read, which every document route ran on the event
    loop through this helper. Both are read in one thread.
    """
    tenant = "default"
    try:
        from kazma_core.tenant_context import get_current_tenant_id

        tenant = (get_current_tenant_id() or "default").strip() or "default"
    except Exception:  # pragma: no cover - defensive
        pass

    def _actor_and_workspace() -> tuple[str, str]:
        from kazma_core.documents.principal import operator_for

        # One operator per install: the page, chat and the agent are the same
        # principal (DOC-A3); with platform users the page acts as each user.
        operator = operator_for(getattr(request.app.state, "documents", None), tenant)
        if operator is not None:
            return operator, _active_workspace_id()
        actor = "local"
        try:
            from kazma_ui.auth import get_request_principal

            principal = get_request_principal(request)
            if principal and principal.get("username"):
                actor = str(principal["username"]).strip() or "local"
        except Exception:  # pragma: no cover - defensive
            logger.debug("[documents_api] principal lookup failed", exc_info=True)
        return actor, _active_workspace_id()

    actor, workspace = await asyncio.to_thread(_actor_and_workspace)
    return tenant, actor, workspace


def create_documents_router() -> APIRouter:
    """Create and return the Documents API router."""

    router = APIRouter(prefix="/api/documents", tags=["documents"])

    def _svc(request: Request):
        svc = getattr(request.app.state, "documents", None)
        return svc

    def _unavailable() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "error": "Document platform is not available"},
        )

    def _ingestion_error(exc: Any) -> JSONResponse:
        code = getattr(exc, "code", "document_error")
        message = getattr(exc, "safe_message", None) or str(exc)
        return _coded_error(code, message, default=400)

    def _capacity_error(exc: Any) -> JSONResponse:
        """Map a backpressure/capacity refusal to a truthful 429/503/507."""
        return JSONResponse(
            status_code=int(getattr(exc, "status", 503)),
            content={
                "ok": False,
                "error": getattr(exc, "safe_message", "Capacity limit reached"),
                "code": getattr(exc, "code", "capacity"),
                "reason": getattr(exc, "reason", "backpressure"),
                "retry_after": int(getattr(exc, "retry_after", 30)),
            },
            headers={"Retry-After": str(int(getattr(exc, "retry_after", 30)))},
        )

    def _require_admin(request: Request) -> JSONResponse | None:
        """Admin/operator gate for destructive operations (GC/maintenance)."""
        from kazma_ui.auth import admin_decision

        decision = admin_decision(request)  # the one fail-closed decision
        if decision == "ok":
            return None
        if decision == "unauthorized":
            return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
        return JSONResponse({"ok": False, "error": "Admin role required"}, status_code=403)

    # ── Upload intake (streamed, bounded) ───────────────────────────────

    async def _admit_upload(
        svc: Any, *, tenant: str, actor: str, workspace: str, declared: int | None
    ) -> JSONResponse | None:
        """The service's budget answer for this upload, before its body is read."""
        import asyncio

        from kazma_core.documents.capacity import CapacityError
        from kazma_core.documents.ingestion import DocumentIngestionError

        try:
            await asyncio.to_thread(
                svc.precheck_upload, tenant_id=tenant, actor_id=actor, workspace_id=workspace, byte_size=declared,
            )
        except DocumentPolicyError as exc:
            return _policy_error(exc)
        except CapacityError as exc:
            return _capacity_error(exc)
        except DocumentIngestionError as exc:
            if exc.code == "intake_too_large":
                return _too_large()
            return _ingestion_error(exc)
        return None

    @router.post("", dependencies=[Depends(rate_limit("documents", 10))])
    async def upload(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        raw_name = (
            request.headers.get("X-Document-Filename")
            or request.query_params.get("filename")
            or ""
        ).strip()
        # Clients send encodeURIComponent(...) so non-ASCII names are safe in
        # HTTP headers (Latin-1 only). Accept both encoded and plain names.
        filename = raw_name
        if raw_name:
            from urllib.parse import unquote

            try:
                filename = unquote(raw_name)
            except Exception:  # noqa: BLE001
                filename = raw_name
        filename = (filename or "").strip()
        if not filename:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Missing X-Document-Filename header"},
            )
        idempotency_key = request.headers.get("X-Idempotency-Key") or None
        force_ocr = request.query_params.get("force_ocr", "").lower() in {"1", "true", "yes"}
        try:
            max_bytes, max_uploads = await asyncio.to_thread(
                lambda: (int(svc.config.intake_max_bytes), int(svc.config.capacity_max_concurrent_uploads))
            )
        except DocumentPolicyError as exc:
            return _policy_error(exc)

        # Admission BEFORE the body (2026-10-13, F08): a declared length past
        # the limit, a disabled platform, a full queue, a tenant cap, a spent
        # rate window or a disk below its floor answer now, not after up to
        # the intake limit has been received and spooled.
        declared_header = request.headers.get("content-length")
        declared: int | None = None
        if declared_header is not None:
            try:
                declared = int(declared_header)
                if declared < 0:
                    raise ValueError(declared_header)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"ok": False, "error": "Invalid Content-Length", "code": "invalid_request"}
                )
            if declared > max_bytes:
                return _too_large()
        refusal = await _admit_upload(svc, tenant=tenant, actor=actor, workspace=workspace, declared=declared)
        if refusal is not None:
            return refusal
        if not _UPLOAD_SLOTS.acquire(max_uploads):
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "5"},
                content={
                    "ok": False,
                    "error": "Too many uploads are being received; retry shortly.",
                    "code": "uploads_busy",
                    "reason": "upload_backpressure",
                },
            )

        # Stream the request body to a bounded spool under the storage root
        # (measured by the free-space floor, swept by garbage collection) --
        # never buffer the whole request in memory, abort early past the cap,
        # and write off the event loop.
        tmp_path: Path | None = None
        total = 0
        try:
            spool_dir = await asyncio.to_thread(svc.upload_spool_dir)
            fd, tmp_name = await asyncio.to_thread(tempfile.mkstemp, prefix="doc-upload-", dir=spool_dir)
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "wb") as handle:
                pending = bytearray()
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes or (declared is not None and total > declared):
                        return _too_large()
                    pending += chunk
                    if len(pending) >= _SPOOL_FLUSH_BYTES:
                        await asyncio.to_thread(handle.write, bytes(pending))
                        pending.clear()
                if pending:
                    await asyncio.to_thread(handle.write, bytes(pending))
            if total == 0:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "Empty upload"},
                )

            spooled = tmp_path

            def _run() -> dict[str, Any]:
                with spooled.open("rb") as source:
                    result = svc.ingest_stream(
                        source,
                        filename=filename,
                        tenant_id=tenant,
                        workspace_id=workspace,
                        actor_id=actor,
                        idempotency_key=idempotency_key,
                        force_ocr=force_ocr,
                        content_length=total,
                    )
                return result.to_dict()

            payload = await asyncio.to_thread(_run)
            return {"ok": True, **payload}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            from kazma_core.documents.capacity import CapacityError
            from kazma_core.documents.ingestion import DocumentIngestionError

            if isinstance(exc, CapacityError):
                return _capacity_error(exc)
            if isinstance(exc, DocumentIngestionError):
                return _ingestion_error(exc)
            # AUD-102: an exception no typed handler above claimed is a server
            # fault, not the caller's mistake: a 500 with a code and a
            # reference. The body used to carry the exception's type and its
            # first 160 characters (paths, the store's own messages) as a 400.
            info = safe_error(exc)
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": f"Upload failed ({info})",
                    "code": "upload_failed",
                    "ref": info.ref,
                },
            )
        finally:
            _UPLOAD_SLOTS.release()
            if tmp_path is not None:
                await asyncio.to_thread(tmp_path.unlink, missing_ok=True)

    # ── Workspace-safe local import ─────────────────────────────────────

    @router.post("/import", dependencies=[Depends(rate_limit("documents", 10))])
    async def import_local(request: Request, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        rel = str(payload.get("path", "")).strip()
        if not rel:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'path'"}
            )
        resolved = await asyncio.to_thread(_resolve_workspace_file, rel)
        if resolved is None:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": "Path is outside the active workspace",
                    "code": "path_outside_workspace",
                },
            )

        def _run() -> dict[str, Any]:
            with resolved.open("rb") as source:
                result = svc.ingest_stream(
                    source,
                    filename=resolved.name,
                    tenant_id=tenant,
                    workspace_id=workspace,
                    actor_id=actor,
                )
            return result.to_dict()

        try:
            data = await asyncio.to_thread(_run)
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            from kazma_core.documents.capacity import CapacityError
            from kazma_core.documents.ingestion import DocumentIngestionError

            if isinstance(exc, CapacityError):
                return _capacity_error(exc)
            if isinstance(exc, DocumentIngestionError):
                return _ingestion_error(exc)
            logger.warning("[documents_api] import failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"Import failed ({type(exc).__name__})"},
            )

    # ── Document actions (convert / pdf / redact / generate / merge) ─────

    @router.post("/generate", dependencies=[Depends(rate_limit("documents", 10))])
    async def generate(request: Request, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        target_format = str(payload.get("target_format", "")).strip()
        body = payload.get("payload")
        if not target_format:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'target_format'"}
            )
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "'payload' must be an object"},
            )
        output_name = _opt_str(payload.get("output_name"))
        title = _opt_str(payload.get("title"))
        try:
            data = await svc.generate_document(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                target_format=target_format,
                payload=body,
                output_name=output_name,
                title=title,
            )
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "generate")

    @router.post("/merge", dependencies=[Depends(rate_limit("documents", 10))])
    async def merge(request: Request, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        document_ids = payload.get("document_ids")
        if not isinstance(document_ids, list) or len(document_ids) < 2:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "'document_ids' must list at least two IDs"},
            )
        if any(not isinstance(item, str) or not item.strip() for item in document_ids):
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "'document_ids' must be non-empty strings"},
            )
        output_name = _opt_str(payload.get("output_name"))
        try:
            data = await svc.merge_documents(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                document_ids=[str(item).strip() for item in document_ids],
                output_name=output_name,
            )
            return {"ok": True, "artifact": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "merge")

    @router.get("/artifacts/{artifact_id}/download")
    async def download_artifact(request: Request, artifact_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            info = await asyncio.to_thread(
                svc.resolve_artifact_blob,
                tenant_id=tenant,
                actor_id=actor,
                artifact_id=artifact_id,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "download")
        return FileResponse(
            path=info["path"],
            media_type=info["mime_type"],
            filename=info["filename"],
        )

    @router.post("/{document_id}/convert", dependencies=[Depends(rate_limit("documents", 10))])
    async def convert(request: Request, document_id: str, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        target_format = str(payload.get("target_format", "")).strip()
        if not target_format:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'target_format'"}
            )
        try:
            data = await svc.convert_document(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                document_id=document_id,
                target_format=target_format,
                output_name=_opt_str(payload.get("output_name")),
            )
            return {"ok": True, "artifact": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "convert")

    @router.get("/{document_id}/pdf-info")
    async def pdf_info(request: Request, document_id: str) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        try:
            data = await svc.pdf_info_document(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                document_id=document_id,
            )
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "pdf_info")

    @router.post("/{document_id}/split", dependencies=[Depends(rate_limit("documents", 10))])
    async def split(request: Request, document_id: str, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        start_page = _opt_int(payload.get("start_page"), 1)
        end_page = _opt_int(payload.get("end_page"), 0)
        if start_page is None or end_page is None:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "start_page/end_page must be integers"},
            )
        try:
            data = await svc.pdf_split_document(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                document_id=document_id,
                start_page=start_page,
                end_page=end_page,
                output_name=_opt_str(payload.get("output_name")),
            )
            return {"ok": True, "artifact": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "split")

    @router.post("/{document_id}/fill-form", dependencies=[Depends(rate_limit("documents", 10))])
    async def fill_form(request: Request, document_id: str, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        fields = payload.get("fields")
        if not isinstance(fields, dict) or not fields:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'fields' object"}
            )
        try:
            data = await svc.pdf_fill_form_document(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                document_id=document_id,
                fields=fields,
                output_name=_opt_str(payload.get("output_name")),
            )
            return {"ok": True, "artifact": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "fill_form")

    @router.post("/{document_id}/redact", dependencies=[Depends(rate_limit("documents", 10))])
    async def redact(request: Request, document_id: str, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = await _resolve_scope(request)
        terms = payload.get("terms")
        if not isinstance(terms, list) or not terms:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'terms' list"}
            )
        try:
            data = await svc.redact_document(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                document_id=document_id,
                terms=terms,
                output_name=_opt_str(payload.get("output_name")),
            )
            return {"ok": True, "artifact": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "redact")

    @router.get("/{document_id}/artifacts")
    async def list_artifacts(request: Request, document_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.list_document_artifacts,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
            )
            return {"ok": True, "artifacts": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _action_error(exc, "artifacts")

    # ── Health ──────────────────────────────────────────────────────────

    @router.get("/health")
    def health(request: Request) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        try:
            with read_only_diagnostic("/api/documents/health"):
                return {"ok": True, "health": svc.health()}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] health failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "health unavailable"}
            )

    # ── Library list / detail ───────────────────────────────────────────

    @router.get("")
    async def list_documents(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            docs = await asyncio.to_thread(
                svc.list_documents, tenant_id=tenant, actor_id=actor
            )
            return {"ok": True, "documents": docs}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] list failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500,
                content={"ok": False, "error": "list failed", "documents": []},
            )

    @router.get("/{document_id}")
    async def detail(request: Request, document_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.get_document_detail,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
            )
            return {"ok": True, "document": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "detail")

    @router.get("/{document_id}/versions")
    async def versions(request: Request, document_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.get_document_detail,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
            )
            return {"ok": True, "versions": data["versions"]}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "versions")

    @router.get("/{document_id}/content")
    async def content(
        request: Request,
        document_id: str,
        page: int | None = Query(None),
        page_start: int | None = Query(None),
        page_end: int | None = Query(None),
        offset: int = Query(0, ge=0),
        max_chars: int = Query(20_000, ge=1, le=200_000),
    ) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.get_content,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
                page=page,
                page_start=page_start,
                page_end=page_end,
                offset=offset,
                max_chars=max_chars,
            )
            return {"ok": True, "content": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "content")

    # ── Knowledge index / search ────────────────────────────────────────

    @router.post("/{document_id}/index", dependencies=[Depends(rate_limit("documents", 10))])
    async def index(request: Request, document_id: str, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        library_id = str(payload.get("library_id", "")).strip()
        if not library_id:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'library_id'"}
            )
        try:
            data = await asyncio.to_thread(
                svc.index_document,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
                library_id=library_id,
            )
            return {"ok": True, "index": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "index")

    @router.post("/{document_id}/unindex")
    async def unindex(request: Request, document_id: str, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        library_id = str(payload.get("library_id", "")).strip()
        if not library_id:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'library_id'"}
            )
        try:
            data = await asyncio.to_thread(
                svc.unindex_document,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
                library_id=library_id,
            )
            return {"ok": True, "unindex": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "unindex")

    @router.post("/search", dependencies=[Depends(rate_limit("documents", 30))])
    async def search(request: Request, payload: dict[str, Any] = Depends(_json_body)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = await _resolve_scope(request)
        library_id = str(payload.get("library_id", "")).strip()
        query = str(payload.get("query", "")).strip()
        try:
            top_k = int(payload.get("top_k", 5) or 5)
        except (TypeError, ValueError):
            top_k = 5
        top_k = max(1, min(top_k, 50))
        if not library_id or not query:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Missing 'library_id' or 'query'"},
            )
        try:
            data = await svc.search_library(
                tenant_id=tenant, library_id=library_id, query=query, top_k=top_k,
                actor_id=_actor,
            )
            return {"ok": True, "search": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "search")

    # ── Jobs ────────────────────────────────────────────────────────────

    @router.get("/jobs/{job_id}")
    async def job_status(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.job_status, tenant_id=tenant, job_id=job_id, actor_id=actor
            )
            if data is None:
                return JSONResponse(
                    status_code=404, content={"ok": False, "error": "Job not found"}
                )
            return {"ok": True, "job": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "job_status")

    @router.get("/jobs/{job_id}/events")
    async def job_events(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.job_events, tenant_id=tenant, job_id=job_id, actor_id=actor
            )
            return {"ok": True, "events": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "job_events", not_found_default=True)

    @router.post("/jobs/{job_id}/cancel")
    async def cancel(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.cancel_job, tenant_id=tenant, job_id=job_id, actor_id=actor
            )
            return {"ok": True, "job": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            return _error_for(exc, "cancel")

    @router.post("/jobs/{job_id}/retry")
    async def retry(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.retry_job, tenant_id=tenant, job_id=job_id, actor_id=actor
            )
            return {"ok": True, "job": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            from kazma_core.documents.capacity import CapacityError

            if isinstance(exc, CapacityError):
                return _capacity_error(exc)
            return _error_for(exc, "retry")

    # ── Phase 9: operations (metrics / capacity / audit / maintenance) ───

    @router.get("/ops/metrics")
    async def documents_metrics(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(svc.metrics_snapshot, tenant_id=tenant)
            return {"ok": True, "metrics": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] metrics failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "metrics unavailable"}
            )

    @router.get("/ops/capacity")
    async def documents_capacity(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = await _resolve_scope(request)
        try:
            data = await asyncio.to_thread(svc.capacity_snapshot, tenant_id=tenant)
            return {"ok": True, "capacity": data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] capacity failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "capacity unavailable"}
            )

    @router.get("/ops/readiness")
    def documents_readiness(request: Request) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        try:
            with read_only_diagnostic("/api/documents/ops/readiness"):
                return {"ok": True, "readiness": svc.readiness()}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] readiness failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "readiness unavailable"}
            )

    @router.get("/ops/retention")
    def documents_retention(request: Request) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        try:
            cfg = svc.config
            return {
                "ok": True,
                "retention": {
                    "rejected_days": cfg.retention_rejected_days,
                    "dead_letter_days": cfg.retention_dead_letter_days,
                    "tombstone_days": cfg.retention_tombstone_days,
                    "quarantine_days": cfg.retention_quarantine_days,
                    "original_days": cfg.retention_original_days,
                    "artifact_days": cfg.retention_artifact_days,
                    "audit_days": cfg.retention_audit_days,
                    "gc_grace_seconds": cfg.gc_grace_seconds,
                    "gc_max_deletions_per_run": cfg.gc_max_deletions_per_run,
                    "gc_enabled": cfg.gc_enabled,
                    "gc_auto_maintain": cfg.gc_auto_maintain,
                    "gc_interval_hours": cfg.gc_interval_hours,
                },
            }
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] retention failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "retention unavailable"}
            )

    @router.get("/ops/audit")
    async def documents_audit(request: Request) -> Any:
        import asyncio

        # The trail names every document, job and actor of the tenant,
        # including documents the caller cannot read: an operator view.
        denied = await asyncio.to_thread(_require_admin, request)
        if denied is not None:
            return denied
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = await _resolve_scope(request)
        document_id = _opt_str(request.query_params.get("document_id"))
        event_type = _opt_str(request.query_params.get("event_type"))
        limit = _opt_int(request.query_params.get("limit"), 50) or 50
        before_id = request.query_params.get("before_id")
        before = int(before_id) if before_id and before_id.isdigit() else None
        try:
            data = await asyncio.to_thread(
                svc.audit_events,
                tenant_id=tenant,
                document_id=document_id,
                event_type=event_type,
                limit=limit,
                before_id=before,
            )
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] audit failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "audit unavailable", "events": []}
            )

    @router.post("/ops/maintenance/dry-run")
    async def maintenance_dry_run(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        denied = await asyncio.to_thread(_require_admin, request)
        if denied:
            return denied
        _tenant, actor, _ = await _resolve_scope(request)
        try:
            report = await asyncio.to_thread(
                svc.run_maintenance, dry_run=True, actor_id=actor
            )
            return {"ok": True, "report": report}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] maintenance dry-run failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "maintenance failed"}
            )

    @router.post("/ops/maintenance/run")
    async def maintenance_run(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        denied = await asyncio.to_thread(_require_admin, request)
        if denied:
            return denied
        _tenant, actor, _ = await _resolve_scope(request)
        try:
            report = await asyncio.to_thread(
                svc.run_maintenance, dry_run=False, actor_id=actor
            )
            return {"ok": True, "report": report}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning("[documents_api] maintenance run failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "maintenance failed"}
            )

    async def _delete_document_impl(
        request: Request, document_id: str, *, reason: str = "user_requested"
    ) -> Any:
        """Soft-delete (tombstone) a document — unindex + archive metadata.

        Physical blobs are reclaimed later by retention/GC, not immediately.
        """
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = await _resolve_scope(request)
        clean_reason = (reason or "user_requested").strip()[:200] or "user_requested"
        try:
            data = await asyncio.to_thread(
                svc.delete_document,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
                reason=clean_reason,
            )
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, DocumentPolicyError):
                return _policy_error(exc)
            logger.warning(
                "[documents_api] delete failed doc=%s actor=%s type=%s err=%s",
                document_id[:12],
                actor,
                type(exc).__name__,
                exc,
            )
            return _error_for(exc, "delete", not_found_default=True)

    @router.post("/{document_id}/delete")
    async def delete_document(
        request: Request, document_id: str, payload: dict[str, Any] = Depends(_optional_json_body),
    ) -> Any:
        # The same bounded reader as every other route (DOC-A4): this one
        # read any size with request.json() and ignored what it could not parse.
        reason = str(payload["reason"])[:200] if payload.get("reason") else "user_requested"
        return await _delete_document_impl(request, document_id, reason=reason)

    @router.delete("/{document_id}")
    async def delete_document_rest(request: Request, document_id: str) -> Any:
        """REST alias for soft-delete / archive (same as POST …/delete)."""
        reason = "user_requested"
        if request.query_params.get("reason"):
            reason = str(request.query_params.get("reason"))[:200]
        return await _delete_document_impl(request, document_id, reason=reason)

    return router


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any, default: int) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _policy_error(exc: DocumentPolicyError) -> JSONResponse:
    return JSONResponse(status_code=503,
                        content={"ok": False, "code": exc.code, "error": exc.safe_message},
                        headers={"Retry-After": "30"})


#: One status per document error code, for every route (DOC-A4, 2026-10-13).
#: Three route helpers kept three tables that had drifted: writes switched off
#: answered 503 on upload, import, index, unindex, delete and retry but 422 on
#: the action routes, and a required malware scanner that could not run
#: answered 400, as if the file were at fault. A refusal the server's state
#: causes (writes off, no scanner, no permitted worker profile) is a 503 with
#: Retry-After; the caller's request is not wrong. A code missing here keeps
#: its route's default (400 for intake and lifecycle, 422 for actions).
_STATUS_BY_CODE: dict[str, int] = {
    "invalid_request": 400,
    "invalid_document_request": 400,
    "invalid_document_scope": 400,
    "invalid_document_encoding": 400,
    "intake_corrupt": 400,
    "document_access_denied": 404,
    "artifact_access_denied": 404,
    "not_ready": 409,
    "idempotency_conflict": 409,
    "intake_taken_over": 409,
    "intake_too_large": 413,
    "quota_exceeded": 413,
    "unsupported_document_operation": 422,
    "document_delete_failed": 500,
    "document_unindex_failed": 500,
    "document_platform_disabled": 503,
    "document_engine_unavailable": 503,
    "repository_unavailable": 503,
    "malware_scanner_unavailable": 503,
    "worker_isolation_required": 503,
    "worker_isolation_invalid": 503,
    "worker_isolation_unavailable": 503,
    "worker_isolation_busy": 503,
}


def _coded_error(code: str, message: str, *, default: int) -> JSONResponse:
    status = _STATUS_BY_CODE.get(code, default)
    headers = {"Retry-After": "30"} if status == 503 else None
    return JSONResponse(
        status_code=status,
        content={"ok": False, "error": message, "code": code},
        headers=headers,
    )


def _unknown_id(exc: Exception) -> JSONResponse | None:
    """A malformed id reads exactly like an id nobody holds (DOC-B4: no
    existence oracle): a job's as an unknown job, any other as an
    unavailable document. None when *exc* is not one."""
    from kazma_core.documents.models import InvalidIdError

    if not isinstance(exc, InvalidIdError):
        return None
    logger.debug("[documents_api] malformed %s in a request", exc.kind)
    if exc.kind == "JobId":
        return JSONResponse(status_code=404, content={"ok": False, "error": "Job not found"})
    return _coded_error("document_access_denied", "Document is unavailable", default=404)


def _action_error(exc: Exception, op: str) -> JSONResponse:
    """Map a document-action failure to a truthful HTTP status."""
    from kazma_core.documents.ingestion import DocumentIngestionError

    if isinstance(exc, DocumentPolicyError):
        return _policy_error(exc)
    unknown = _unknown_id(exc)
    if unknown is not None:
        return unknown
    if isinstance(exc, DocumentIngestionError):
        return _coded_error(exc.code, exc.safe_message, default=422)
    logger.warning("[documents_api] %s failed type=%s", op, type(exc).__name__)
    return JSONResponse(
        status_code=400,
        content={"ok": False, "error": f"{op} failed ({type(exc).__name__})"},
    )


#: The largest JSON body a document route reads (2026-10-13, F08). FastAPI's
#: ``Body(...)`` parsed any size into memory before a route could check it;
#: the largest legitimate body is a 1 MiB generation payload plus its frame.
_JSON_BODY_MAX_BYTES = 2 * 1024 * 1024


async def _optional_json_body(request: Request) -> dict[str, Any]:
    """:func:`_json_body` for a route whose body may be absent: none is ``{}``."""
    return await _read_json_body(request, allow_empty=True)


async def _json_body(request: Request) -> dict[str, Any]:
    """The request's JSON object (a FastAPI dependency); see the reader."""
    return await _read_json_body(request, allow_empty=False)


async def _read_json_body(request: Request, *, allow_empty: bool) -> dict[str, Any]:
    """The request's JSON object, read under ``_JSON_BODY_MAX_BYTES``.

    A declared length past the cap is refused before a byte is read; an
    undeclared or false one is cut off as soon as the running total passes
    it. Malformed JSON, a non-object and nesting too deep for the parser are
    422s, never a 500.
    """
    import json

    def _refuse(status: int, error: str, code: str) -> HTTPException:
        return HTTPException(status_code=status, detail={"ok": False, "error": error, "code": code})

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _JSON_BODY_MAX_BYTES:
                raise _refuse(413, "Request body is too large", "request_too_large")
        except ValueError:
            raise _refuse(400, "Invalid Content-Length", "invalid_request") from None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > _JSON_BODY_MAX_BYTES:
            raise _refuse(413, "Request body is too large", "request_too_large")
    if allow_empty and not bytes(body).strip():
        return {}
    try:
        value = json.loads(bytes(body) or b"null")
    except (ValueError, RecursionError):
        raise _refuse(422, "Request body is not valid JSON", "invalid_json") from None
    if not isinstance(value, dict):
        raise _refuse(422, "Request body must be a JSON object", "invalid_json")
    return value


#: Spooled bytes are written to disk in pieces of this size, off the loop.
_SPOOL_FLUSH_BYTES = 1024 * 1024


class _UploadSlots:
    """Uploads being received by this process at once (each spooled)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held = 0

    def acquire(self, limit: int) -> bool:
        with self._lock:
            if self._held >= max(1, limit):
                return False
            self._held += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._held = max(0, self._held - 1)

    @property
    def held(self) -> int:
        return self._held


_UPLOAD_SLOTS = _UploadSlots()


def _too_large() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "ok": False,
            "error": "Upload exceeds the configured intake limit",
            "code": "intake_too_large",
        },
    )


def _error_for(exc: Exception, op: str, *, not_found_default: bool = False) -> JSONResponse:
    from kazma_core.documents.ingestion import DocumentIngestionError
    from kazma_core.documents.jobs import (
        InvalidJobTransitionError,
        JobNotFoundError,
    )

    if isinstance(exc, DocumentPolicyError):
        return _policy_error(exc)
    unknown = _unknown_id(exc)
    if unknown is not None:
        return unknown
    if isinstance(exc, DocumentIngestionError):
        return _coded_error(exc.code, exc.safe_message, default=400)
    if isinstance(exc, JobNotFoundError):
        return JSONResponse(
            status_code=404, content={"ok": False, "error": "Job not found"}
        )
    if isinstance(exc, InvalidJobTransitionError):
        return JSONResponse(
            status_code=409, content={"ok": False, "error": safe_error(exc)}
        )
    # Surface DocumentAccessError from the repository with a clear message
    # instead of a generic "Not found" when not_found_default is set.
    err_name = type(exc).__name__
    if err_name == "DocumentAccessError":
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": safe_error(exc) or "Not allowed to delete this document",
                "code": "document_access_denied",
            },
        )
    if not_found_default:
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "error": f"{op} failed: {err_name}",
                "code": "not_found",
            },
        )
    logger.warning("[documents_api] %s failed type=%s", op, err_name)
    return JSONResponse(
        status_code=400,
        content={"ok": False, "error": f"{op} failed ({err_name})"},
    )


def _resolve_workspace_file(rel_path: str) -> Path | None:
    """Resolve a workspace-relative path with strict containment, or None."""
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root().resolve()
    except Exception:  # pragma: no cover - defensive
        return None
    candidate = (root / rel_path).resolve() if not Path(rel_path).is_absolute() else Path(rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate
