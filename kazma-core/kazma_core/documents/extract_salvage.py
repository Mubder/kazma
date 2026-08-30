"""Hard-PDF salvage after the isolated parser returns a weak extract.

Two tiers, both governed by ConfigStore policy, the remote one audited.

**Local (Docling)** runs in the parent process — the parser sandbox is scrubbed
of API keys on purpose — and never leaves the machine. Governed by
``documents.security.local_salvage`` (default **on**).

**Remote (LlamaParse / Reducto)** uploads the *original document bytes* to a
third party. Governed by ``documents.security.remote_parse``, which defaults to
**off**, and it emits an audit record naming the provider and the byte count on
every call, before the bytes move.

The previous shape of this module was an env-var kill switch that defaulted to
enabled: any deployment with ``LLAMAPARSE_API_KEY`` in its environment shipped
customer documents to a third party with no config entry, no tenant consent and
no audit trail. Scanned Arabic scores low by construction, so the pages most
likely to trigger it were the scans a customer would least want uploaded.
Egress is now something an operator turns on deliberately.

Legacy kill switches ``KAZMA_DOCLING=0`` / ``KAZMA_REMOTE_PARSE=0`` still work,
but only as an additional veto — they can turn a tier off, never on.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import DocumentConfig
from .models import BlockType, DocumentIR
from .parsers.common import IRBuilder, ParseContext
from .quality import score_document_extraction

logger = logging.getLogger(__name__)

__all__ = [
    "SALVAGE_SCORE",
    "maybe_salvage_extract",
    "ir_from_markdown",
    "set_salvage_audit_hook",
]

SALVAGE_SCORE = 0.55

# Called as ``hook(provider, path, byte_count)`` when remote egress happens.
# The ingestion service installs the durable recorder; the default is a warning
# log line, so even a bare DocumentService leaves a trace.
AuditHook = Callable[[str, Path, int], None]
_audit_hook: AuditHook | None = None


def set_salvage_audit_hook(hook: AuditHook | None) -> None:
    """Install the durable audit recorder for remote-parse egress."""
    global _audit_hook
    _audit_hook = hook


def _record_egress(provider: str, path: Path, size: int) -> None:
    logger.warning(
        "[extract] remote parse egress: provider=%s bytes=%d — document content "
        "left this machine under documents.security.remote_parse",
        provider,
        size,
    )
    if _audit_hook is not None:
        try:
            _audit_hook(provider, path, size)
        except Exception:  # pragma: no cover - auditing must never break a parse
            logger.debug("[extract] salvage audit hook failed", exc_info=True)


def _env_veto(name: str) -> bool:
    """Legacy env kill switch, retained as an ADDITIONAL veto only."""
    return os.environ.get(name, "1").strip().lower() in ("0", "false", "off", "no")


def _resolve_config(config: DocumentConfig | None) -> DocumentConfig | None:
    if config is not None:
        return config
    try:
        from .config import get_document_config

        return get_document_config()
    except Exception:  # pragma: no cover - defensive
        logger.debug(
            "[extract] document config unavailable; salvage tiers stay disabled",
            exc_info=True,
        )
        return None


def _has_text(document: DocumentIR) -> bool:
    return any(
        (block.text or "").strip()
        for page in document.pages
        for block in page.blocks
    )


def maybe_salvage_extract(
    path: Path,
    document: DocumentIR,
    *,
    config: DocumentConfig | None = None,
) -> DocumentIR:
    """If the native extract is weak, try local salvage then (if permitted) remote.

    Remote salvage requires ``documents.security.remote_parse``. The presence of
    a provider API key in the environment is not consent and never was.
    """
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

    cfg = _resolve_config(config)
    local_allowed = bool(
        getattr(cfg, "security_local_salvage", False)
    ) and not _env_veto("KAZMA_DOCLING")
    remote_allowed = bool(
        getattr(cfg, "security_remote_parse", False)
    ) and not _env_veto("KAZMA_REMOTE_PARSE")

    best = document
    best_score = score
    if local_allowed:
        try:
            salvaged = try_docling(path, document)
        except Exception:
            logger.debug("[extract] Docling salvage failed", exc_info=True)
            salvaged = None
        if salvaged is not None:
            rescored = float(score_document_extraction(salvaged))
            if rescored > best_score:
                best, best_score = salvaged, rescored
                if rescored >= SALVAGE_SCORE:
                    return best

    if best_score < SALVAGE_SCORE and remote_allowed:
        try:
            salvaged = try_remote_parse(path, document)
        except Exception:
            logger.debug("[extract] remote salvage failed", exc_info=True)
            salvaged = None
        if salvaged is not None:
            rescored = float(score_document_extraction(salvaged))
            if rescored > best_score:
                best = salvaged
    return best


def _context_for(path: Path, document: DocumentIR) -> ParseContext:
    from .config import DocumentConfig as _Config
    from .config import get_document_config

    meta = dict(document.metadata or {})
    try:
        cfg = get_document_config()
    except Exception:
        cfg = _Config(storage_root=path.parent)
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
    """Upload the document to a third-party parser.

    The caller MUST have checked ``documents.security.remote_parse`` first.
    Egress is recorded *before* the bytes move, so an upload that then fails is
    still on the record.
    """
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    llama = os.environ.get("LLAMAPARSE_API_KEY", "").strip()
    reducto = os.environ.get("REDUCTO_API_KEY", "").strip()
    if llama:
        _record_egress("llamaparse", path, size)
        md = _llamaparse(path, llama)
        if md:
            return ir_from_markdown(path, document, md, extractor="llamaparse")
    if reducto:
        _record_egress("reducto", path, size)
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
    body = (
        result.json()
        if result.headers.get("content-type", "").startswith("application/json")
        else {}
    )
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
