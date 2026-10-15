"""Hard-PDF salvage after the isolated parser returns a weak extract.

Two tiers, both governed by ConfigStore policy, the remote one audited.

**Local (Docling)** runs inside the bounded parser child with a scrubbed
environment. Governed by
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
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .config import DocumentConfig
from .errors import DocumentPolicyError, DocumentSecurityError
from .models import BlockType, DocumentIR
from .parsers.common import IRBuilder, ParseContext
from .quality import score_document_extraction

logger = logging.getLogger(__name__)

__all__ = [
    "SALVAGE_SCORE",
    "maybe_salvage_extract",
    "ir_from_markdown",
    "salvage_audit_scope",
]

SALVAGE_SCORE = 0.55

# The durable operation supplies attribution. A missing recorder refuses egress.
AuditHook = Callable[[str, Path, int, str], None]
_audit_hook: ContextVar[AuditHook | None] = ContextVar("document_egress_audit", default=None)


@contextmanager
def salvage_audit_scope(hook: AuditHook):
    """Bind the immutable operation's recorder; restore the prior scope."""
    token = _audit_hook.set(hook)
    try:
        yield
    finally:
        _audit_hook.reset(token)


def _record_egress(provider: str, path: Path, size: int, phase: str = "attempt") -> None:
    hook = _audit_hook.get()
    if hook is None:
        raise DocumentSecurityError(
            "Remote parsing requires an authorized document audit scope",
            code="remote_parse_audit_unavailable",
        )
    # Required audit persistence precedes upload. No unknown/system tenant
    # fallback is allowed to conceal missing attribution.
    hook(provider, path, size, phase)
    logger.warning(
        "[extract] remote parse event: provider=%s bytes=%d phase=%s",
        provider,
        size,
        phase,
    )


def _env_veto(name: str) -> bool:
    """Legacy env kill switch, retained as an ADDITIONAL veto only."""
    return os.environ.get(name, "1").strip().lower() in ("0", "false", "off", "no")


def _resolve_config(config: DocumentConfig | None) -> DocumentConfig | None:
    if config is not None:
        return config
    try:
        from .config import get_document_config

        return get_document_config(required=True)
    except DocumentPolicyError:
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
    allow_local: bool = True,
    allow_remote: bool = True,
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
    local_allowed = allow_local and bool(
        getattr(cfg, "security_local_salvage", False)
    ) and not _env_veto("KAZMA_DOCLING")
    remote_allowed = allow_remote and bool(
        getattr(cfg, "security_remote_parse", False)
    ) and not _env_veto("KAZMA_REMOTE_PARSE")

    best = document
    best_score = score
    if local_allowed:
        try:
            salvaged = try_docling(path, document, config=cfg)
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
        except Exception as exc:
            # The operator turned remote parsing on: a refusal (no audit
            # scope, an audit that could not be written) or a failure is said,
            # not left at DEBUG (Q04 defensive check C10, 2026-10-15). Only
            # the type: a provider's message may carry request details.
            logger.warning(
                "[extract] remote parsing did not run or failed (%s); the local reading is kept",
                type(exc).__name__,
            )
            logger.debug("[extract] remote salvage failure", exc_info=True)
            salvaged = None
        if salvaged is not None:
            rescored = float(score_document_extraction(salvaged))
            if rescored > best_score:
                best = salvaged
    return best


def _context_for(path: Path, document: DocumentIR, config: DocumentConfig | None = None) -> ParseContext:
    from .config import DocumentConfig as _Config
    from .config import get_document_config

    meta = dict(document.metadata or {})
    cfg = config
    if cfg is None:
        try:
            cfg = get_document_config()
        except Exception:
            cfg = _Config(storage_root=path.parent)
    return ParseContext(
        config=cfg,
        source_sha256=str(meta.get("source_sha256") or ""),
        mime_type=str(meta.get("source_mime") or meta.get("mime_type") or "application/pdf"),
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
    config: DocumentConfig | None = None,
) -> DocumentIR:
    """Map markdown (Docling / LlamaParse / Reducto) onto DocumentIR."""
    builder = IRBuilder(path, _context_for(path, document, config))
    chunks = [p.strip() for p in (markdown or "").split("\n\n") if p.strip()]
    if not chunks:
        chunks = [(markdown or "").strip() or "(empty salvage)"]
    page_blocks: list[tuple[BlockType, str, dict[str, Any] | None]] = []
    segment_metadata = {"kind": "segment", "physical_page_mapping": False}
    builder.warnings.append("Salvage segments have no verified source-page mapping")
    for chunk in chunks:
        page_blocks.append((BlockType.PARAGRAPH, chunk, {"physical_page_mapping": False}))
        if len(page_blocks) >= 40:
            builder.add_page(page_blocks, metadata=segment_metadata)
            page_blocks = []
    if page_blocks:
        builder.add_page(page_blocks, metadata=segment_metadata)
    ir = builder.build()
    meta = {
        **dict(document.metadata or {}),
        **dict(ir.metadata or {}),
        "extractor": extractor,
        "salvaged": True,
        "page_semantics": "synthetic_segment",
        "physical_page_mapping": False,
    }
    from dataclasses import replace

    return replace(ir, metadata=meta)


def try_docling(path: Path, document: DocumentIR, *, config: DocumentConfig | None = None) -> DocumentIR | None:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None
    converter = DocumentConverter()
    result = converter.convert(str(path))
    md = result.document.export_to_markdown()
    if not (md or "").strip():
        return None
    return ir_from_markdown(path, document, md, extractor="docling", config=config)


#: Decoded bytes a remote parser may return; a larger answer is refused.
_PROVIDER_RESPONSE_LIMIT = 32 * 1024 * 1024


def try_remote_parse(path: Path, document: DocumentIR) -> DocumentIR | None:
    """Upload the document to a third-party parser, trying each configured provider.

    The caller MUST have checked ``documents.security.remote_parse`` first.
    Before EACH provider the live policy is read again (a revocation stops
    the next upload) and the attempt is durably audited before a byte moves;
    the outcome is audited as ``success`` or ``failure``. A failed provider
    no longer ends salvage: the next configured one is tried, under the same
    rules (until 2026-10-13 a first failure re-raised and the fallback never
    ran).
    """
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    for provider, key_name, parse in (
        ("llamaparse", "LLAMAPARSE_API_KEY", _llamaparse),
        ("reducto", "REDUCTO_API_KEY", _reducto),
    ):
        # This is a separate revocation check, immediately at each egress
        # boundary. A job snapshot or API key cannot override live denial.
        live = _resolve_config(None)
        if not getattr(live, "security_remote_parse", False) or _env_veto("KAZMA_REMOTE_PARSE"):
            return None
        key = os.environ.get(key_name, "").strip()
        if not key:
            continue
        _record_egress(provider, path, size)
        try:
            md = parse(path, key)
        except Exception as exc:  # noqa: BLE001 - audited, then the next provider
            _record_egress(provider, path, size, "failure")
            logger.warning(
                "[extract] remote parse provider=%s failed (%s); trying the next one",
                provider,
                type(exc).__name__,
            )
            continue
        _record_egress(provider, path, size, "success")
        if md:
            return ir_from_markdown(path, document, md, extractor=provider)
    return None


def _provider_client(transport: Any = None) -> Any:
    """The one HTTP client for remote parsers.

    Never an ambient proxy: customer bytes go to the named provider only,
    not through a proxy an environment variable happens to name (acquisition
    clients follow the same rule). No redirects: an upload is never re-sent
    to another host.
    """

    import httpx

    return httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(120.0, connect=15.0),
        transport=transport,  # tests only: an in-memory provider
    )


def _read_capped(response: Any) -> bytes:
    """The decoded body, refusing past :data:`_PROVIDER_RESPONSE_LIMIT`."""

    response.raise_for_status()
    data = bytearray()
    for chunk in response.iter_bytes():
        data.extend(chunk)
        if len(data) > _PROVIDER_RESPONSE_LIMIT:
            raise DocumentSecurityError(
                "The remote parser's answer exceeded its size limit",
                code="remote_parse_response_limit",
            )
    return bytes(data)


def _json_or_empty(payload: bytes) -> Any:
    import json

    return json.loads(payload.decode("utf-8")) if payload.strip() else {}


def _llamaparse(path: Path, api_key: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}"}
    with _provider_client() as client:
        with path.open("rb") as fh, client.stream(
            "POST",
            "https://api.cloud.llamaindex.ai/api/parsing/upload",
            headers=headers,
            files={"file": (path.name, fh, "application/pdf")},
        ) as resp:
            data = _json_or_empty(_read_capped(resp))
        job_id = str(data.get("id") or data.get("job_id") or "") if isinstance(data, dict) else ""
        md = data.get("markdown") or data.get("text") if isinstance(data, dict) else None
        if isinstance(md, str) and md.strip():
            return md
        if not job_id:
            return ""
        with client.stream(
            "GET",
            f"https://api.cloud.llamaindex.ai/api/parsing/job/{job_id}/result/markdown",
            headers=headers,
        ) as result:
            payload = _read_capped(result)
            is_json = result.headers.get("content-type", "").startswith("application/json")
    if is_json:
        body = _json_or_empty(payload)
        if isinstance(body, dict):
            return str(body.get("markdown") or body.get("text") or "")
        return ""
    return payload.decode("utf-8", errors="replace")


def _reducto(path: Path, api_key: str) -> str:
    with _provider_client() as client, path.open("rb") as fh, client.stream(
        "POST",
        "https://platform.reducto.ai/parse",
        headers={"Authorization": f"Bearer {api_key}"},
        files={"document": (path.name, fh, "application/pdf")},
    ) as resp:
        data = _json_or_empty(_read_capped(resp))
    if not isinstance(data, dict):
        return ""
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
