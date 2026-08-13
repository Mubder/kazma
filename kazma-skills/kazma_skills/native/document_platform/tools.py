"""Agent-facing document platform tools — thin delegates to the shared
``DocumentIngestionService`` (the sole orchestration boundary).

These tools never re-implement parsing, storage, or job logic. They resolve
tenant/workspace/actor scope from the request context (never hard-coded), and
only accept **workspace-safe local selections** or **opaque document IDs** —
no arbitrary server paths.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_OUTPUT_CHARS = 20_000
_INGEST_WAIT_SECONDS = 30.0


def _scope() -> tuple[str, str, str]:
    """Resolve (tenant_id, workspace_id, actor_id) from context, safely."""
    tenant = "default"
    try:
        from kazma_core.tenant_context import get_current_tenant_id

        tenant = (get_current_tenant_id() or "default").strip() or "default"
    except Exception:  # pragma: no cover - defensive
        pass

    workspace = "default"
    try:
        from kazma_core.ide.workspace_scope import current_workspace_id

        scoped = current_workspace_id()
        if scoped:
            workspace = str(scoped)
        else:
            from kazma_core.stores import get_workspace_store

            active = get_workspace_store().get_active_workspace()
            if active and active.get("id"):
                workspace = str(active["id"])
    except Exception:  # pragma: no cover - defensive
        pass

    return tenant, workspace, "agent"


def _resolve_workspace_file(path: str) -> tuple[Path | None, str | None]:
    """Resolve a workspace-relative/absolute path with strict containment."""
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root().resolve()
    except Exception:  # pragma: no cover - defensive
        return None, "Error: workspace is unavailable"
    candidate = Path(path).expanduser()
    candidate = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f"Error: path is outside the active workspace: {path}"
    if not candidate.is_file():
        return None, f"Error: file not found: {path}"
    return candidate, None


async def _ensure_service():
    from kazma_core.documents.ingestion import get_ingestion_service

    svc = get_ingestion_service()
    if not svc.worker_running:
        try:
            await svc.start_workers()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[document_platform] worker start failed: %s", type(exc).__name__)
    return svc


async def document_import(path: str, title: str = "") -> str:
    """Ingest a workspace-safe local file and wait until it is processed.

    Returns the opaque ``document_id`` / ``job_id`` and final state. Only
    files inside the active workspace are accepted.
    """

    import asyncio

    resolved, error = _resolve_workspace_file(path)
    if error:
        return error
    assert resolved is not None
    tenant, workspace, actor = _scope()
    try:
        svc = await _ensure_service()

        def _ingest() -> Any:
            with resolved.open("rb") as source:
                return svc.ingest_stream(
                    source,
                    filename=resolved.name,
                    tenant_id=tenant,
                    workspace_id=workspace,
                    actor_id=actor,
                    title=title or resolved.name,
                )

        result = await asyncio.to_thread(_ingest)
        state = await _wait_terminal(svc, tenant, str(result.job_id))
        return (
            f"Document ingested.\n"
            f"  document_id: {result.document_id}\n"
            f"  job_id: {result.job_id}\n"
            f"  state: {state}"
        )
    except Exception as exc:  # noqa: BLE001
        from kazma_core.documents.ingestion import DocumentIngestionError

        if isinstance(exc, DocumentIngestionError):
            return f"Error: {exc.safe_message}"
        logger.warning("[document_platform] import failed: %s", type(exc).__name__)
        return f"Error importing document: {type(exc).__name__}"


async def document_status(document_id: str = "", job_id: str = "") -> str:
    """Report the durable job state for a document or job opaque ID."""

    import asyncio

    tenant, _ws, _actor = _scope()
    try:
        svc = await _ensure_service()
        if job_id.strip():
            status = await asyncio.to_thread(
                svc.job_status, tenant_id=tenant, job_id=job_id.strip()
            )
            if status is None:
                return "Error: job not found"
            return _format_status(status)
        if document_id.strip():
            jobs = await asyncio.to_thread(
                svc.jobs_for_document, tenant_id=tenant, document_id=document_id.strip()
            )
            if not jobs:
                return "Error: document not found or has no jobs"
            return _format_status(jobs[0])
        return "Error: provide document_id or job_id"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[document_platform] status failed: %s", type(exc).__name__)
        return f"Error reading status: {type(exc).__name__}"


async def document_read(
    document_id: str,
    page: int | None = None,
    offset: int = 0,
    max_chars: int = _MAX_OUTPUT_CHARS,
) -> str:
    """Read paged, fenced content of a processed document by opaque ID."""

    import asyncio

    tenant, _ws, _actor = _scope()
    try:
        svc = await _ensure_service()
        data = await asyncio.to_thread(
            svc.get_content,
            tenant_id=tenant,
            actor_id="agent",
            document_id=document_id.strip(),
            page=page,
            offset=offset,
            max_chars=max_chars,
        )
        text = data["text"]
        cont = data["continuation"]
        if cont.get("has_more"):
            text += (
                f"\n[Document continuation: next_offset={cont['next_offset']}, "
                f"total_chars={cont['total_chars']}]"
            )
        return text
    except Exception as exc:  # noqa: BLE001
        from kazma_core.documents.ingestion import DocumentIngestionError

        if isinstance(exc, DocumentIngestionError):
            return f"Error: {exc.safe_message}"
        logger.warning("[document_platform] read failed: %s", type(exc).__name__)
        return f"Error reading document: {type(exc).__name__}"


async def document_index(document_id: str, library_id: str) -> str:
    """Publish a processed document's current version to a Knowledge library."""

    import asyncio

    if not library_id.strip():
        return "Error: library_id is required"
    tenant, _ws, _actor = _scope()
    try:
        svc = await _ensure_service()
        result = await asyncio.to_thread(
            svc.index_document,
            tenant_id=tenant,
            actor_id="agent",
            document_id=document_id.strip(),
            library_id=library_id.strip(),
        )
        return (
            f"Indexed {result.get('chunk_count', 0)} chunk(s) into "
            f"'{result.get('library_id', library_id)}'."
        )
    except Exception as exc:  # noqa: BLE001
        from kazma_core.documents.ingestion import DocumentIngestionError

        if isinstance(exc, DocumentIngestionError):
            return f"Error: {exc.safe_message}"
        logger.warning("[document_platform] index failed: %s", type(exc).__name__)
        return f"Error indexing document: {type(exc).__name__}"


async def document_search(library_id: str, query: str, top_k: int = 5) -> str:
    """Search a Knowledge library; results return inside one untrusted fence."""

    if not library_id.strip() or not query.strip():
        return "Error: library_id and query are required"
    tenant, _ws, _actor = _scope()
    try:
        svc = await _ensure_service()
        data = await svc.search_library(
            tenant_id=tenant,
            library_id=library_id.strip(),
            query=query.strip(),
            top_k=int(top_k) if top_k else 5,
            actor_id=_actor,
        )
        context = data.get("prompt_context") or ""
        if not context:
            return "No matching document chunks found."
        return context
    except Exception as exc:  # noqa: BLE001
        logger.warning("[document_platform] search failed: %s", type(exc).__name__)
        return f"Error searching documents: {type(exc).__name__}"


async def document_cancel(job_id: str) -> str:
    """Request cooperative cancellation of a running/pending document job."""

    import asyncio

    if not job_id.strip():
        return "Error: job_id is required"
    tenant, _ws, _actor = _scope()
    try:
        svc = await _ensure_service()
        status = await asyncio.to_thread(
            svc.cancel_job, tenant_id=tenant, job_id=job_id.strip()
        )
        return f"Cancellation requested. Current state: {status['state']}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("[document_platform] cancel failed: %s", type(exc).__name__)
        return f"Error cancelling job: {type(exc).__name__}"


async def document_convert(
    document_id: str, target_format: str, output_name: str = ""
) -> str:
    """Convert a processed document (by opaque ID) to another format.

    Delegates to the coordinator, which materializes only the immutable
    original bytes — no raw file path is accepted. Returns the new artifact's
    opaque ``artifact_id`` (downloadable) plus any warnings.
    """

    import asyncio

    if not document_id.strip() or not target_format.strip():
        return "Error: document_id and target_format are required"
    tenant, workspace, actor = _scope()
    try:
        svc = await _ensure_service()
        data = await svc.convert_document(
            tenant_id=tenant,
            actor_id=actor,
            workspace_id=workspace,
            document_id=document_id.strip(),
            target_format=target_format.strip(),
            output_name=output_name.strip() or None,
        )
        return _format_artifact("Converted", data)
    except Exception as exc:  # noqa: BLE001
        from kazma_core.documents.ingestion import DocumentIngestionError

        if isinstance(exc, DocumentIngestionError):
            return f"Error: {exc.safe_message}"
        logger.warning("[document_platform] convert failed: %s", type(exc).__name__)
        return f"Error converting document: {type(exc).__name__}"


async def document_redact(
    document_id: str, terms: list[str], output_name: str = ""
) -> str:
    """Physically redact terms from a processed PDF document by opaque ID.

    Redaction creates a new immutable artifact and independently verifies the
    result. Mixed image/vector PDFs fail closed. Terms are never logged.
    """

    if not document_id.strip():
        return "Error: document_id is required"
    if not isinstance(terms, (list, tuple)) or not terms:
        return "Error: at least one redaction term is required"
    tenant, workspace, actor = _scope()
    try:
        svc = await _ensure_service()
        data = await svc.redact_document(
            tenant_id=tenant,
            actor_id=actor,
            workspace_id=workspace,
            document_id=document_id.strip(),
            terms=[str(term) for term in terms],
            output_name=output_name.strip() or None,
        )
        return _format_artifact("Redacted", data)
    except Exception as exc:  # noqa: BLE001
        from kazma_core.documents.ingestion import DocumentIngestionError

        if isinstance(exc, DocumentIngestionError):
            return f"Error: {exc.safe_message}"
        logger.warning("[document_platform] redact failed: %s", type(exc).__name__)
        return f"Error redacting document: {type(exc).__name__}"


def _format_artifact(action: str, data: dict[str, Any]) -> str:
    manifest = data.get("manifest", {}) if isinstance(data, dict) else {}
    output = manifest.get("output", {}) if isinstance(manifest, dict) else {}
    lines = [
        f"{action}. artifact_id: {data.get('artifact_id')}",
        f"  document_id: {data.get('document_id')}",
        f"  output: {output.get('extension')} ({output.get('size')} bytes)",
    ]
    warnings = data.get("warnings") or []
    if warnings:
        lines.append(f"  warnings: {'; '.join(str(w) for w in warnings)}")
    return "\n".join(lines)


async def _wait_terminal(svc, tenant: str, job_id: str) -> str:
    import asyncio

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _INGEST_WAIT_SECONDS
    state = "unknown"
    terminal = {"ready", "cancelled", "dead_letter", "rejected"}
    while loop.time() < deadline:
        status = await asyncio.to_thread(
            svc.job_status, tenant_id=tenant, job_id=job_id
        )
        if status is not None:
            state = status["state"]
            if state in terminal:
                return state
        await asyncio.sleep(0.2)
    return state


def _format_status(status: dict[str, Any]) -> str:
    lines = [
        f"Document job {status['job_id']}",
        f"  document_id: {status['document_id']}",
        f"  state: {status['state']} (stage: {status['stage']})",
        f"  attempt: {status['attempt']}/{status['max_attempts']}",
    ]
    if status.get("error_code"):
        lines.append(f"  error: {status['error_code']} — {status.get('error_message')}")
    return "\n".join(lines)
