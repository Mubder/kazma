"""Thin compatibility tools for isolated DocumentService generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kazma_core.documents.service import DocumentService

DOC_DIR = Path("kazma-data/documents")


def _scope() -> dict[str, str]:
    """Resolve generation scope from request context, not hard-coded tenant.

    Uses ``get_current_tenant_id()`` with a safe single-user fallback and the
    active/scoped workspace identity (used for artifact provenance only).
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


def _sections(values: list[Any] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for value in values or []:
        if isinstance(value, dict):
            result.append(
                {
                    "heading": str(value.get("heading", "")),
                    "body": str(value.get("body", "")),
                }
            )
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            result.append({"heading": str(value[0]), "body": str(value[1])})
    return result


def _output(result: Any, label: str) -> str:
    if not result.ok:
        return f"Error: {result.message}"
    artifact = result.data
    path = artifact.export_path if artifact is not None else None
    return (
        f"{label} generated successfully.\n"
        f"  Artifact: {result.artifact_id}\n"
        f"  Saved to: {path}"
    )


async def generate_pdf(
    title: str,
    sections: list[dict[str, str]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    images: list[dict[str, str]] | None = None,
) -> str:
    """Generate a verified PDF through the isolated renderer worker."""

    payload = {
        "title": title,
        "sections": _sections(sections),
        "tables": tables or [],
        "images": [{} for _ in images or []],
        "page_numbers": True,
    }
    result = await DocumentService().generate(
        "pdf",
        payload,
        output_name=title,
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _output(result, "PDF")


async def generate_docx(title: str, sections: list[dict[str, str]]) -> str:
    """Generate a verified DOCX through the isolated renderer worker."""

    result = await DocumentService().generate(
        "docx",
        {"title": title, "sections": _sections(sections)},
        output_name=title,
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _output(result, "DOCX")


async def generate_xlsx(
    sheets: list[dict[str, Any]], filename: str = "workbook"
) -> str:
    """Generate a verified XLSX through the isolated renderer worker."""

    result = await DocumentService().generate(
        "xlsx",
        {"title": filename, "sheets": sheets or []},
        output_name=filename,
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _output(result, "XLSX")


async def generate_markdown_doc(
    title: str, sections: list[dict[str, str]]
) -> str:
    """Generate a verified UTF-8 Markdown artifact."""

    result = await DocumentService().generate(
        "markdown",
        {"title": title, "sections": _sections(sections)},
        output_name=title,
        export_dir=DOC_DIR,
        **_scope(),
    )
    return _output(result, "Markdown")
