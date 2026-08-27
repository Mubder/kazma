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

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from kazma_ui.rate_limit import rate_limit

logger = logging.getLogger(__name__)

__all__ = ["create_documents_router"]

_COPY_CHUNK = 1024 * 1024


def _resolve_scope(request: Request) -> tuple[str, str, str]:
    """Return (tenant_id, actor_id, workspace_id) from trusted server context."""
    tenant = "default"
    try:
        from kazma_core.tenant_context import get_current_tenant_id

        tenant = (get_current_tenant_id() or "default").strip() or "default"
    except Exception:  # pragma: no cover - defensive
        pass

    actor = "local"
    try:
        from kazma_ui.auth import get_request_principal

        principal = get_request_principal(request)
        if principal and principal.get("username"):
            actor = str(principal["username"]).strip() or "local"
    except Exception:  # pragma: no cover - defensive
        pass

    workspace = "default"
    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("id"):
            workspace = str(active["id"]).strip() or "default"
    except Exception:  # pragma: no cover - defensive
        pass

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
        status = {
            "invalid_request": 400,
            "intake_too_large": 413,
            "quota_exceeded": 413,
            "intake_corrupt": 400,
            "document_access_denied": 404,
            "not_ready": 409,
            "document_platform_disabled": 503,
        }.get(code, 400)
        return JSONResponse(
            status_code=status, content={"ok": False, "error": message, "code": code}
        )

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
        try:
            from kazma_ui.auth import get_kazma_secret, get_request_principal, is_authenticated

            secret = get_kazma_secret()
            if secret and not is_authenticated(request, secret):
                return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
            principal = get_request_principal(request) or {}
            if principal.get("source") == "secret":
                return None
            if principal.get("role") != "admin":
                return JSONResponse(
                    {"ok": False, "error": "Admin role required"}, status_code=403
                )
        except Exception:  # noqa: BLE001 - single-user/no-auth deployments allow it
            return None
        return None

    # ── Upload intake (streamed, bounded) ───────────────────────────────

    @router.post("", dependencies=[Depends(rate_limit("documents", 10))])
    async def upload(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
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
        max_bytes = int(svc.config.intake_max_bytes)

        # Stream the request body to a bounded temp file — never buffer the
        # whole request in memory, and abort early past the cap.
        fd, tmp_name = tempfile.mkstemp(prefix="doc-upload-")
        tmp_path = Path(tmp_name)
        total = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        return JSONResponse(
                            status_code=413,
                            content={
                                "ok": False,
                                "error": "Upload exceeds the configured intake limit",
                                "code": "intake_too_large",
                            },
                        )
                    handle.write(chunk)
            if total == 0:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "error": "Empty upload"},
                )

            def _run() -> dict[str, Any]:
                with tmp_path.open("rb") as source:
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
            from kazma_core.documents.capacity import CapacityError
            from kazma_core.documents.ingestion import DocumentIngestionError

            if isinstance(exc, CapacityError):
                return _capacity_error(exc)
            if isinstance(exc, DocumentIngestionError):
                return _ingestion_error(exc)
            # Log full detail for operators; return a short safe message to clients.
            logger.warning(
                "[documents_api] upload failed type=%s detail=%s",
                type(exc).__name__,
                str(exc)[:300],
                exc_info=True,
            )
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "error": f"Upload failed ({type(exc).__name__}: {str(exc)[:160]})",
                    "code": "upload_failed",
                },
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    # ── Workspace-safe local import ─────────────────────────────────────

    @router.post("/import", dependencies=[Depends(rate_limit("documents", 10))])
    async def import_local(request: Request, payload: dict[str, Any] = Body(...)) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
        rel = str(payload.get("path", "")).strip()
        if not rel:
            return JSONResponse(
                status_code=400, content={"ok": False, "error": "Missing 'path'"}
            )
        resolved = _resolve_workspace_file(rel)
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
            from kazma_core.documents.ingestion import DocumentIngestionError

            if isinstance(exc, DocumentIngestionError):
                return _ingestion_error(exc)
            logger.warning("[documents_api] import failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": f"Import failed ({type(exc).__name__})"},
            )

    # ── Document actions (convert / pdf / redact / generate / merge) ─────

    @router.post("/generate", dependencies=[Depends(rate_limit("documents", 10))])
    async def generate(request: Request, payload: dict[str, Any] = Body(...)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
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
            return _action_error(exc, "generate")

    @router.post("/merge", dependencies=[Depends(rate_limit("documents", 10))])
    async def merge(request: Request, payload: dict[str, Any] = Body(...)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
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
            return _action_error(exc, "merge")

    @router.get("/artifacts/{artifact_id}/download")
    async def download_artifact(request: Request, artifact_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = _resolve_scope(request)
        try:
            info = await asyncio.to_thread(
                svc.resolve_artifact_blob,
                tenant_id=tenant,
                actor_id=actor,
                artifact_id=artifact_id,
            )
        except Exception as exc:  # noqa: BLE001
            return _action_error(exc, "download")
        return FileResponse(
            path=info["path"],
            media_type=info["mime_type"],
            filename=info["filename"],
        )

    @router.post("/{document_id}/convert", dependencies=[Depends(rate_limit("documents", 10))])
    async def convert(request: Request, document_id: str, payload: dict[str, Any] = Body(...)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
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
            return _action_error(exc, "convert")

    @router.get("/{document_id}/pdf-info")
    async def pdf_info(request: Request, document_id: str) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
        try:
            data = await svc.pdf_info_document(
                tenant_id=tenant,
                actor_id=actor,
                workspace_id=workspace,
                document_id=document_id,
            )
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            return _action_error(exc, "pdf_info")

    @router.post("/{document_id}/split")
    async def split(request: Request, document_id: str, payload: dict[str, Any] = Body(...)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
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
            return _action_error(exc, "split")

    @router.post("/{document_id}/fill-form")
    async def fill_form(request: Request, document_id: str, payload: dict[str, Any] = Body(...)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
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
            return _action_error(exc, "fill_form")

    @router.post("/{document_id}/redact", dependencies=[Depends(rate_limit("documents", 10))])
    async def redact(request: Request, document_id: str, payload: dict[str, Any] = Body(...)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, workspace = _resolve_scope(request)
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
            return _action_error(exc, "redact")

    @router.get("/{document_id}/artifacts")
    async def list_artifacts(request: Request, document_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.list_document_artifacts,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
            )
            return {"ok": True, "artifacts": data}
        except Exception as exc:  # noqa: BLE001
            return _action_error(exc, "artifacts")

    # ── Health ──────────────────────────────────────────────────────────

    @router.get("/health")
    async def health(request: Request) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        try:
            return {"ok": True, "health": svc.health()}
        except Exception as exc:  # noqa: BLE001
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
        tenant, actor, _ = _resolve_scope(request)
        try:
            docs = await asyncio.to_thread(
                svc.list_documents, tenant_id=tenant, actor_id=actor
            )
            return {"ok": True, "documents": docs}
        except Exception as exc:  # noqa: BLE001
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
        tenant, actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.get_document_detail,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
            )
            return {"ok": True, "document": data}
        except Exception as exc:  # noqa: BLE001
            return _error_for(exc, "detail")

    @router.get("/{document_id}/versions")
    async def versions(request: Request, document_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.get_document_detail,
                tenant_id=tenant,
                actor_id=actor,
                document_id=document_id,
            )
            return {"ok": True, "versions": data["versions"]}
        except Exception as exc:  # noqa: BLE001
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
        tenant, actor, _ = _resolve_scope(request)
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
            return _error_for(exc, "content")

    # ── Knowledge index / search ────────────────────────────────────────

    @router.post("/{document_id}/index")
    async def index(request: Request, document_id: str, payload: dict[str, Any] = Body(...)) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = _resolve_scope(request)
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
            return _error_for(exc, "index")

    @router.post("/{document_id}/unindex")
    async def unindex(request: Request, document_id: str, payload: dict[str, Any] = Body(...)) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = _resolve_scope(request)
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
            return _error_for(exc, "unindex")

    @router.post("/search")
    async def search(request: Request, payload: dict[str, Any] = Body(...)) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = _resolve_scope(request)
        library_id = str(payload.get("library_id", "")).strip()
        query = str(payload.get("query", "")).strip()
        top_k = int(payload.get("top_k", 5) or 5)
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
            return _error_for(exc, "search")

    # ── Jobs ────────────────────────────────────────────────────────────

    @router.get("/jobs/{job_id}")
    async def job_status(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.job_status, tenant_id=tenant, job_id=job_id
            )
            if data is None:
                return JSONResponse(
                    status_code=404, content={"ok": False, "error": "Job not found"}
                )
            return {"ok": True, "job": data}
        except Exception as exc:  # noqa: BLE001
            return _error_for(exc, "job_status")

    @router.get("/jobs/{job_id}/events")
    async def job_events(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.job_events, tenant_id=tenant, job_id=job_id
            )
            return {"ok": True, "events": data}
        except Exception as exc:  # noqa: BLE001
            return _error_for(exc, "job_events", not_found_default=True)

    @router.post("/jobs/{job_id}/cancel")
    async def cancel(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.cancel_job, tenant_id=tenant, job_id=job_id, actor_id=actor
            )
            return {"ok": True, "job": data}
        except Exception as exc:  # noqa: BLE001
            return _error_for(exc, "cancel")

    @router.post("/jobs/{job_id}/retry")
    async def retry(request: Request, job_id: str) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(
                svc.retry_job, tenant_id=tenant, job_id=job_id, actor_id=actor
            )
            return {"ok": True, "job": data}
        except Exception as exc:  # noqa: BLE001
            return _error_for(exc, "retry")

    # ── Phase 9: operations (metrics / capacity / audit / maintenance) ───

    @router.get("/ops/metrics")
    async def documents_metrics(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(svc.metrics_snapshot, tenant_id=tenant)
            return {"ok": True, "metrics": data}
        except Exception as exc:  # noqa: BLE001
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
        tenant, _actor, _ = _resolve_scope(request)
        try:
            data = await asyncio.to_thread(svc.capacity_snapshot, tenant_id=tenant)
            return {"ok": True, "capacity": data}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[documents_api] capacity failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "capacity unavailable"}
            )

    @router.get("/ops/readiness")
    async def documents_readiness(request: Request) -> Any:
        svc = _svc(request)
        if svc is None:
            return _unavailable()
        try:
            return {"ok": True, "readiness": svc.readiness()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[documents_api] readiness failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "readiness unavailable"}
            )

    @router.get("/ops/retention")
    async def documents_retention(request: Request) -> Any:
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
            logger.warning("[documents_api] retention failed type=%s", type(exc).__name__)
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "retention unavailable"}
            )

    @router.get("/ops/audit")
    async def documents_audit(request: Request) -> Any:
        import asyncio

        svc = _svc(request)
        if svc is None:
            return _unavailable()
        tenant, _actor, _ = _resolve_scope(request)
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
        denied = _require_admin(request)
        if denied:
            return denied
        _tenant, actor, _ = _resolve_scope(request)
        try:
            report = await asyncio.to_thread(
                svc.run_maintenance, dry_run=True, actor_id=actor
            )
            return {"ok": True, "report": report}
        except Exception as exc:  # noqa: BLE001
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
        denied = _require_admin(request)
        if denied:
            return denied
        _tenant, actor, _ = _resolve_scope(request)
        try:
            report = await asyncio.to_thread(
                svc.run_maintenance, dry_run=False, actor_id=actor
            )
            return {"ok": True, "report": report}
        except Exception as exc:  # noqa: BLE001
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
        tenant, actor, _ = _resolve_scope(request)
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
            logger.warning(
                "[documents_api] delete failed doc=%s actor=%s type=%s err=%s",
                document_id[:12],
                actor,
                type(exc).__name__,
                exc,
            )
            return _error_for(exc, "delete", not_found_default=True)

    @router.post("/{document_id}/delete")
    async def delete_document(request: Request, document_id: str) -> Any:
        reason = "user_requested"
        try:
            body = await request.json()
            if isinstance(body, dict) and body.get("reason"):
                reason = str(body["reason"])[:200]
        except Exception:  # noqa: BLE001
            pass
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


def _action_error(exc: Exception, op: str) -> JSONResponse:
    """Map a document-action failure to a truthful HTTP status."""
    from kazma_core.documents.ingestion import DocumentIngestionError

    if isinstance(exc, DocumentIngestionError):
        code = exc.code
        status = {
            "invalid_request": 400,
            "invalid_document_request": 400,
            "invalid_document_scope": 400,
            "invalid_document_encoding": 400,
            "intake_too_large": 413,
            "quota_exceeded": 413,
            "not_ready": 409,
            "document_access_denied": 404,
            "artifact_access_denied": 404,
            "document_engine_unavailable": 503,
            "unsupported_document_operation": 422,
            "repository_unavailable": 503,
        }.get(code, 422)
        return JSONResponse(
            status_code=status,
            content={"ok": False, "error": exc.safe_message, "code": code},
        )
    logger.warning("[documents_api] %s failed type=%s", op, type(exc).__name__)
    return JSONResponse(
        status_code=400,
        content={"ok": False, "error": f"{op} failed ({type(exc).__name__})"},
    )


def _error_for(exc: Exception, op: str, *, not_found_default: bool = False) -> JSONResponse:
    from kazma_core.documents.ingestion import DocumentIngestionError
    from kazma_core.documents.jobs import (
        InvalidJobTransitionError,
        JobNotFoundError,
    )

    if isinstance(exc, DocumentIngestionError):
        code = exc.code
        status = {
            "invalid_request": 400,
            "not_ready": 409,
            "document_access_denied": 404,
            "document_delete_failed": 500,
            "document_platform_disabled": 503,
        }.get(code, 400)
        return JSONResponse(
            status_code=status,
            content={"ok": False, "error": exc.safe_message, "code": code},
        )
    if isinstance(exc, JobNotFoundError):
        return JSONResponse(
            status_code=404, content={"ok": False, "error": "Job not found"}
        )
    if isinstance(exc, InvalidJobTransitionError):
        return JSONResponse(
            status_code=409, content={"ok": False, "error": str(exc)}
        )
    # Surface DocumentAccessError from the repository with a clear message
    # instead of a generic "Not found" when not_found_default is set.
    err_name = type(exc).__name__
    if err_name == "DocumentAccessError":
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": str(exc) or "Not allowed to delete this document",
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
