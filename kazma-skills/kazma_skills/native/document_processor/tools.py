"""Thin compatibility tools for the isolated document platform."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from kazma_core.agent.tool_registry import _workspace_scope_error
from kazma_core.documents.errors import DocumentParseError
from kazma_core.documents.service import DocumentService

logger = logging.getLogger(__name__)

DOC_DIR = Path("kazma-data/documents")
_MAX_OUTPUT_CHARS = 20_000


def _scope() -> dict[str, str]:
    """Resolve document operation scope from request context, not hard-coded.

    Uses ``get_current_tenant_id()`` with a safe single-user fallback and the
    active/scoped workspace identity. ``OperationScope`` only requires
    non-empty values (used for artifact provenance), so the fallbacks are safe.
    """
    tenant = "local"
    try:
        from kazma_core.tenant_context import get_current_tenant_id

        tenant = (get_current_tenant_id() or "local").strip() or "local"
    except Exception:  # pragma: no cover - defensive
        pass
    workspace = "active"
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
    return {"tenant_id": tenant, "workspace_id": workspace, "actor_id": "agent"}


def _resolve_input(path: str, op: str = "document reads") -> tuple[Path | None, str | None]:
    resolved = Path(path).expanduser().resolve()
    scope_error = _workspace_scope_error(resolved, path, op)
    if scope_error:
        return None, scope_error
    if not resolved.is_file():
        return None, f"Error: File not found: {path}"
    return resolved, None


def _artifact_output(result: Any, label: str) -> str:
    if not result.ok:
        return f"Error: {result.message}"
    artifact = result.data
    path = artifact.export_path if artifact is not None else None
    warnings = (
        "\n" + "\n".join(f"  Warning: {item}" for item in result.warnings)
        if result.warnings
        else ""
    )
    return (
        f"{label} completed successfully.\n"
        f"  Artifact: {result.artifact_id}\n"
        f"  Saved to: {path}{warnings}"
    )


async def read_document(
    path: str,
    page: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    block: str | int | None = None,
    offset: int = 0,
    max_chars: int = _MAX_OUTPUT_CHARS,
) -> str:
    """Read a supported document through the isolated parser worker."""

    source, error = _resolve_input(path)
    if error:
        return error
    assert source is not None
    try:
        result = await DocumentService().read_transient(
            source,
            approved_path=source,
            page=page,
            page_start=page_start,
            page_end=page_end,
            block=block,
            offset=offset,
            max_chars=max_chars,
            fence=True,
        )
        return result.as_tool_output()
    except DocumentParseError as exc:
        return f"Error: {exc.safe_message}"
    except Exception as exc:
        logger.error("[document_processor] read failed (%s)", type(exc).__name__)
        return f"Error reading document: {type(exc).__name__}"


async def ocr_document(
    path: str,
    lang: str = "auto",
    pages: list[int] | None = None,
    language: str | None = None,
) -> str:
    """OCR a document through the isolated parser worker."""

    source, error = _resolve_input(path, "document OCR reads")
    if error:
        return error
    assert source is not None
    try:
        service = DocumentService()
        selected = tuple(pages) if pages is not None else None
        document = await service.ocr_transient(
            source,
            approved_path=source,
            language=language or lang,
            pages=selected,
        )
        if selected is not None:
            document = replace(
                document,
                pages=tuple(page for page in document.pages if page.page_number in selected),
            )
        return service.read_ir(
            document, max_chars=_MAX_OUTPUT_CHARS, fence=True
        ).as_tool_output()
    except DocumentParseError as exc:
        return f"Error: {exc.safe_message}"
    except Exception as exc:
        logger.error("[document_processor] OCR failed (%s)", type(exc).__name__)
        return f"Error during OCR: {type(exc).__name__}"


async def pdf_merge(file_paths: list[str], output_name: str = "merged") -> str:
    """Merge approved PDFs through the isolated mutation worker."""

    sources: list[Path] = []
    for path in file_paths:
        source, error = _resolve_input(path, "PDF merge reads")
        if error:
            return error
        assert source is not None
        sources.append(source)
    result = await DocumentService().pdf_merge(
        tuple(sources),
        approved_paths=tuple(sources),
        output_name=output_name,
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _artifact_output(result, "PDF merge")


async def pdf_split(
    file_path: str,
    start_page: int = 1,
    end_page: int = 0,
    output_name: str = "",
) -> str:
    """Extract a bounded PDF page range through the isolated mutation worker."""

    source, error = _resolve_input(file_path, "PDF split reads")
    if error:
        return error
    assert source is not None
    result = await DocumentService().pdf_split(
        source,
        approved_path=source,
        start_page=start_page,
        end_page=end_page,
        output_name=output_name or f"{source.stem}-split",
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _artifact_output(result, "PDF split")


async def pdf_info(file_path: str) -> str:
    """Inspect PDF metadata in the isolated mutation worker."""

    source, error = _resolve_input(file_path)
    if error:
        return error
    assert source is not None
    result = await DocumentService().pdf_info(
        source, approved_path=source, **_scope()
    )
    if not result.ok:
        return f"Error: {result.message}"
    value = result.data or {}
    return "\n".join(
        (
            f"PDF Metadata: {source.name}",
            f"  Pages: {value.get('page_count', 0)}",
            f"  Title: {value.get('title') or '(none)'}",
            f"  Author: {value.get('author') or '(none)'}",
            f"  Subject: {value.get('subject') or '(none)'}",
            f"  Creator: {value.get('creator') or '(none)'}",
            f"  Form fields: {len(value.get('field_names', []))}",
        )
    )


async def convert_document(
    file_path: str, target_format: str, output_name: str = ""
) -> str:
    """Convert a document through an isolated, runtime-probed renderer."""

    source, error = _resolve_input(file_path, "document conversion reads")
    if error:
        return error
    assert source is not None
    result = await DocumentService().convert(
        source,
        target_format,
        approved_path=source,
        output_name=output_name or source.stem,
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _artifact_output(result, "Document conversion")


async def pdf_fill_form(
    file_path: str, fields: dict[str, str], output_name: str = ""
) -> str:
    """Fill validated AcroForm fields through the isolated mutation worker."""

    source, error = _resolve_input(file_path, "PDF form reads")
    if error:
        return error
    assert source is not None
    result = await DocumentService().pdf_fill_form(
        source,
        fields,
        approved_path=source,
        output_name=output_name or f"{source.stem}-filled",
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _artifact_output(result, "PDF form fill")


async def pdf_redact(
    file_path: str, terms: list[str], output_name: str = ""
) -> str:
    """Physically redact a PDF and fail closed unless every verification passes."""

    source, error = _resolve_input(file_path, "PDF redaction reads")
    if error:
        return error
    assert source is not None
    result = await DocumentService().redact(
        source,
        terms,
        approved_path=source,
        output_name=output_name or f"{source.stem}-redacted",
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _artifact_output(result, "Secure PDF redaction")


async def generate_pptx(title: str, slides: list[dict[str, Any]]) -> str:
    """Generate a verified PPTX through the isolated renderer worker."""

    result = await DocumentService().generate(
        "pptx",
        {"title": title, "slides": slides or []},
        output_name=title,
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _artifact_output(result, "PowerPoint")
