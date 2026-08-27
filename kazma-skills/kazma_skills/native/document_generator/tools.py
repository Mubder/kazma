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


# Words too generic to prove a section body reached the PDF.
_VERIFY_STOP = {
    "the", "and", "with", "that", "this", "from", "for", "are", "was",
    "were", "have", "has", "will", "your", "you", "all", "not", "but",
    "into", "than", "then", "them", "they", "their", "there", "these",
    "those", "been", "being", "when", "what", "which", "while", "about",
}


def _verify_pdf_content(
    path: Any, sections: list[dict[str, str]] | None, tables: list[dict[str, Any]] | None,
) -> str | None:
    """Post-render verification: the PDF must actually CONTAIN the content
    the caller asked for (2026-08-27 incident: the model passed a SUMMARY as
    section bodies — "26 names, all confirmed available…" — and reported
    success for a PDF holding only headings; 30 names never made it in).

    Extracts the rendered text and requires ≥70% of each section body's
    distinctive LATIN tokens (≥4 chars, stopwords dropped) plus every table
    header and first-row cell to appear. Latin-only matching deliberately
    sidesteps RTL/Arabic extraction presentation-forms. Returns an error
    string for the model to self-correct with, or None when verified
    (or when verification cannot run — never blocks generation).
    """
    try:
        import re as _re

        pdf_path = str(path)
        if not pdf_path.lower().endswith(".pdf") or not Path(pdf_path).is_file():
            return None
        import fitz  # pymupdf — same parser stack the document platform uses

        doc = fitz.open(pdf_path)
        try:
            rendered = " ".join(page.get_text() for page in doc)
        finally:
            doc.close()
        norm = _re.sub(r"[^a-z0-9]+", " ", rendered.lower())

        def _tokens(text: str) -> list[str]:
            words = _re.sub(r"[^a-zA-Z0-9]+", " ", str(text or "").lower()).split()
            return [w for w in words if len(w) >= 4 and w not in _VERIFY_STOP]

        for i, sec in enumerate(sections or []):
            toks = _tokens(sec.get("body", ""))
            if not toks:
                continue  # nothing Latin/provable to check (e.g. Arabic body)
            found = sum(1 for t in toks if t in norm)
            if found / len(toks) < 0.7:
                missing = [t for t in toks if t not in norm]
                return (
                    f"Error: PDF VERIFICATION FAILED — section "
                    f"{i + 1} ('{str(sec.get('heading', ''))[:60]}') body did "
                    f"not reach the PDF ({found}/{len(toks)} distinctive terms "
                    f"found; first missing: {', '.join(missing[:6])}). "
                    f"The file was created but is INCOMPLETE — do NOT report "
                    f"success. Re-call with the COMPLETE body text (or write "
                    f"a markdown file and use markdown_path)."
                )
        for t_i, table in enumerate(tables or []):
            cells = [str(c) for c in (table.get("headers") or [])]
            rows = table.get("rows") or []
            if rows:
                cells.extend(str(c) for c in (rows[0] or []))
            missing = [c for c in cells if c.strip() and str(c).lower() not in norm]
            if missing:
                return (
                    f"Error: PDF VERIFICATION FAILED — table {t_i + 1} is "
                    f"missing content in the rendered PDF (missing: "
                    f"{', '.join(missing)[:120]}). The file was created but "
                    f"is INCOMPLETE — do NOT report success. Re-call with the "
                    f"full table rows (or use markdown_path)."
                )
        return None
    except Exception:
        # Verification is a guardrail, never a blocker.
        return None


async def generate_pdf(
    title: str,
    sections: list[dict[str, str]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    images: list[dict[str, str]] | None = None,
    lang: str | None = None,
    rtl: bool | None = None,
    markdown_path: str | None = None,
) -> str:
    """Generate a verified, styled PDF through the isolated renderer worker.

    Content source (pick ONE — markdown_path preferred for large documents):
      - ``markdown_path``: workspace-relative path to a .md file written via
        file_write (chunked). Headings (``#``/``##``/``###``) become PDF
        sections automatically. Keeps the tool call tiny regardless of
        document size — inline sections that exceed the model's output
        token limit get truncated into unparseable JSON.
      - ``sections``: inline [{"heading": …, "body": …}] (small docs only).
        The body MUST be the COMPLETE verbatim final text — every list
        item, every table row. NEVER a summary of it ("26 names, all
        confirmed available…" is a WRONG body; the actual names are the
        body). The rendered PDF is verified against the bodies and an
        incomplete document returns an error, not a success.

    Section bodies support lightweight markdown for real formatting:
      - ``#`` / ``##`` / ``###`` headings
      - ``-`` / ``*`` bullets and ``1.`` numbered lists
      - ``**bold**``, ``*italic*``, ``code``, links
      - blank-line paragraphs (justified); blockquotes with ``>``

    Arabic is auto-detected (or set ``lang="ar"`` / ``rtl=True``). PDF path
    applies arabic-reshaper + python-bidi so letters join and order correctly.
    """
    from pathlib import Path as _P

    if markdown_path:
        # Resolve within the workspace (same ladder as file_read).
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root()
        p = _P(markdown_path)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if not p.is_file():
            return f"Error: markdown_path '{markdown_path}' not found (resolved: {p}). Write the file first via file_write."
        md = p.read_text(encoding="utf-8", errors="replace")
        # Parse markdown headings into sections (same split as the export endpoints).
        parsed: list[dict[str, str]] = []
        cur_h = "Report"
        cur_b: list[str] = []
        for line in md.splitlines():
            if line.startswith("#"):
                if cur_b or parsed:
                    parsed.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})
                cur_h = line
                cur_b = []
            else:
                cur_b.append(line)
        if cur_b or not parsed:
            parsed.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})
        sections = parsed
    payload: dict[str, Any] = {
        "title": title,
        "sections": _sections(sections),
        "tables": tables or [],
        "images": [{} for _ in images or []],
        "page_numbers": True,
    }
    if lang:
        payload["lang"] = lang
    if rtl is not None:
        payload["rtl"] = bool(rtl)
    result = await DocumentService().generate(
        "pdf",
        payload,
        output_name=title,
        export_dir=DOC_DIR,
        **_scope(),
    )
    if result.ok:
        # Guardrail (2026-08-27 incident): verify the rendered PDF actually
        # CONTAINS the requested bodies/tables before reporting success —
        # a lazily-summarized call (or a renderer truncation) must surface
        # as an error the model can self-correct from, never a false ✅.
        _path = getattr(result.data, "export_path", None) if result.data else None
        _err = _verify_pdf_content(_path, _sections(sections), tables)
        if _err:
            return _err
    return _output(result, "PDF")


async def generate_docx(
    title: str,
    sections: list[dict[str, str]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    lang: str | None = None,
    rtl: bool | None = None,
    markdown_path: str | None = None,
) -> str:
    """Generate a verified DOCX with PDF-parity styling.

    Content source (pick ONE — markdown_path preferred for large documents):
      - ``markdown_path``: workspace-relative path to a .md file written via
        file_write (chunked). Headings become DOCX sections automatically.
      - ``sections``: inline [{"heading": …, "body": …}] (small docs only)

    Body markdown: headings, lists, **bold**, GFM tables, quotes.
    Also accepts structured ``tables=[{heading, headers, rows}]`` like PDF.
    Word shapes Arabic; we set ``w:bidi``, justify (``w:jc=both``), heading
    fills, and styled tables.
    """
    from pathlib import Path as _P

    if markdown_path:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root()
        p = _P(markdown_path)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if not p.is_file():
            return f"Error: markdown_path '{markdown_path}' not found (resolved: {p}). Write the file first via file_write."
        md = p.read_text(encoding="utf-8", errors="replace")
        parsed: list[dict[str, str]] = []
        cur_h = "Report"
        cur_b: list[str] = []
        for line in md.splitlines():
            if line.startswith("#"):
                if cur_b or parsed:
                    parsed.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})
                cur_h = line
                cur_b = []
            else:
                cur_b.append(line)
        if cur_b or not parsed:
            parsed.append({"heading": cur_h, "body": "\n".join(cur_b).strip()})
        sections = parsed
    payload: dict[str, Any] = {
        "title": title,
        "sections": _sections(sections),
        "tables": tables or [],
    }
    if lang:
        payload["lang"] = lang
    if rtl is not None:
        payload["rtl"] = bool(rtl)
    result = await DocumentService().generate(
        "docx",
        payload,
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
