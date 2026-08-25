"""Hard-PDF salvage after the isolated parser returns a weak extract.

Local Docling (optional extra) and remote LlamaParse / Reducto run in the
**parent** process — the parser sandbox is scrubbed of API keys on purpose.
Kill-switches: ``KAZMA_DOCLING=0``, ``KAZMA_REMOTE_PARSE=0``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from .models import BlockType, DocumentIR
from .parsers.common import IRBuilder, ParseContext
from .quality import score_document_extraction

logger = logging.getLogger(__name__)

__all__ = ["SALVAGE_SCORE", "maybe_salvage_extract", "ir_from_markdown"]

SALVAGE_SCORE = 0.55


def _env_off(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() in ("0", "false", "off", "no")


def _has_text(document: DocumentIR) -> bool:
    return any(
        (block.text or "").strip()
        for page in document.pages
        for block in page.blocks
    )


def maybe_salvage_extract(path: Path, document: DocumentIR) -> DocumentIR:
    """If the native extract is weak, try Docling then remote parsers."""
    try:
        score = float(document.metadata.get("extraction_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    if score <= 0.0:
        try:
            score = float(score_document_extraction(document))
        except Exception:
            score = 0.0
    if score >= SALVAGE_SCORE and _has_text(document):
        return document

    best = document
    best_score = score
    if not _env_off("KAZMA_DOCLING"):
        try:
            salvaged = try_docling(path, document)
        except Exception:
            logger.debug("[extract] Docling salvage failed", exc_info=True)
            salvaged = None
        if salvaged is not None:
            s = float(score_document_extraction(salvaged))
            if s > best_score:
                best, best_score = salvaged, s
                if s >= SALVAGE_SCORE:
                    return best

    if best_score < SALVAGE_SCORE and not _env_off("KAZMA_REMOTE_PARSE"):
        try:
            salvaged = try_remote_parse(path, document)
        except Exception:
            logger.debug("[extract] remote salvage failed", exc_info=True)
            salvaged = None
        if salvaged is not None:
            s = float(score_document_extraction(salvaged))
            if s > best_score:
                best = salvaged
    return best


def _context_for(path: Path, document: DocumentIR) -> ParseContext:
    from .config import DocumentConfig, get_document_config

    meta = dict(document.metadata or {})
    try:
        cfg = get_document_config()
    except Exception:
        cfg = DocumentConfig(storage_root=path.parent)
    return ParseContext(
        config=cfg,
        source_sha256=str(meta.get("source_sha256") or ""),
        mime_type=str(meta.get("mime_type") or "application/pdf"),
        extension=path.suffix.lower() or ".pdf",
        parser_id="salvage",
        parser_version="1",
    )


def ir_from_markdown(
    path: Path,
    document: DocumentIR,
    markdown: str,
    *,
    extractor: str,
) -> DocumentIR:
    """Map markdown (Docling / LlamaParse / Reducto) onto DocumentIR."""
    builder = IRBuilder(path, _context_for(path, document))
    chunks = [p.strip() for p in (markdown or "").split("\n\n") if p.strip()]
    if not chunks:
        chunks = [(markdown or "").strip() or "(empty salvage)"]
    page_blocks: list[tuple[BlockType, str, dict[str, Any] | None]] = []
    for chunk in chunks:
        page_blocks.append((BlockType.PARAGRAPH, chunk[:20_000], None))
        if len(page_blocks) >= 40:
            builder.add_page(page_blocks)
            page_blocks = []
    if page_blocks:
        builder.add_page(page_blocks)
    ir = builder.build()
    meta = {
        **dict(document.metadata or {}),
        **dict(ir.metadata or {}),
        "extractor": extractor,
        "salvaged": True,
    }
    from dataclasses import replace

    return replace(ir, metadata=meta)


def try_docling(path: Path, document: DocumentIR) -> DocumentIR | None:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None
    converter = DocumentConverter()
    result = converter.convert(str(path))
    md = result.document.export_to_markdown()
    if not (md or "").strip():
        return None
    return ir_from_markdown(path, document, md, extractor="docling")


def try_remote_parse(path: Path, document: DocumentIR) -> DocumentIR | None:
    llama = os.environ.get("LLAMAPARSE_API_KEY", "").strip()
    reducto = os.environ.get("REDUCTO_API_KEY", "").strip()
    if llama:
        md = _llamaparse(path, llama)
        if md:
            return ir_from_markdown(path, document, md, extractor="llamaparse")
    if reducto:
        md = _reducto(path, reducto)
        if md:
            return ir_from_markdown(path, document, md, extractor="reducto")
    return None


def _llamaparse(path: Path, api_key: str) -> str:
    import httpx

    upload = "https://api.cloud.llamaindex.ai/api/parsing/upload"
    with path.open("rb") as fh:
        resp = httpx.post(
            upload,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, fh, "application/pdf")},
            timeout=120.0,
        )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    job_id = str(data.get("id") or data.get("job_id") or "")
    md = data.get("markdown") or data.get("text")
    if isinstance(md, str) and md.strip():
        return md
    if not job_id:
        return ""
    result = httpx.get(
        f"https://api.cloud.llamaindex.ai/api/parsing/job/{job_id}/result/markdown",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120.0,
    )
    result.raise_for_status()
    body = result.json() if result.headers.get("content-type", "").startswith("application/json") else {}
    if isinstance(body, dict):
        return str(body.get("markdown") or body.get("text") or "")
    return result.text


def _reducto(path: Path, api_key: str) -> str:
    import httpx

    with path.open("rb") as fh:
        resp = httpx.post(
            "https://platform.reducto.ai/parse",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"document": (path.name, fh, "application/pdf")},
            timeout=120.0,
        )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    if isinstance(data.get("text"), str):
        return data["text"]
    chunks = data.get("result") or data.get("chunks") or []
    if isinstance(chunks, list):
        parts = []
        for item in chunks:
            if isinstance(item, dict) and item.get("content"):
                parts.append(str(item["content"]))
            elif isinstance(item, str):
                parts.append(item)
        return "\n\n".join(parts)
    return str(data.get("markdown") or "")
