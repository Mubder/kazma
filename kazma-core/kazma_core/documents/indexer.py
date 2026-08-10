"""Structural, citation-preserving chunking for canonical :class:`DocumentIR`."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from .config import DocumentConfig
from .models import BlockType, BoundingBox, DocumentBlock, DocumentIR

__all__ = ["DocumentChunk", "chunk_document_ir"]

_SPACE_RE = re.compile(r"\s+")
_OVERLAP_TYPES = frozenset({BlockType.PARAGRAPH, BlockType.TEXT})


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_text(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _token_count(value: str) -> int:
    """Conservative dependency-free token estimate used by document limits."""

    return max(1, (len(value) + 3) // 4)


def _box_dict(box: BoundingBox | None) -> dict[str, float] | None:
    return box.to_dict() if box is not None else None


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One immutable DocumentIR-derived indexing unit."""

    chunk_id: str
    chunk_hash: str
    content_hash: str
    document_id: str
    version_id: str
    source_sha256: str
    content: str
    chunk_index: int
    page_start: int
    page_end: int
    block_ids: tuple[str, ...]
    block_hashes: tuple[str, ...]
    coordinates: tuple[dict[str, Any], ...]
    parser: str
    parser_version: str | None
    language: tuple[str, ...]
    direction: tuple[str, ...]
    ocr: bool
    confidence: float | None
    metadata: dict[str, Any]

    @property
    def source_url(self) -> str:
        return f"document://{self.document_id}/{self.version_id}"

    @property
    def citation_label(self) -> str:
        pages = (
            str(self.page_start)
            if self.page_start == self.page_end
            else f"{self.page_start}-{self.page_end}"
        )
        return (
            f"document:{self.document_id}@{self.version_id} "
            f"page:{pages} blocks:{','.join(self.block_ids)}"
        )

    def to_knowledge_dict(self, *, library_id: str, title: str = "") -> dict[str, Any]:
        metadata = {
            **self.metadata,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "source_sha256": self.source_sha256,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "block_ids": list(self.block_ids),
            "block_hashes": list(self.block_hashes),
            "coordinates": list(self.coordinates),
            "parser": self.parser,
            "parser_version": self.parser_version,
            "language": list(self.language),
            "direction": list(self.direction),
            "ocr": self.ocr,
            "confidence": self.confidence,
            "citation_label": self.citation_label,
        }
        return {
            "id": f"{library_id}:{self.chunk_id}",
            "library_id": library_id,
            "source_url": self.source_url,
            "document_title": title,
            "section_header": self.citation_label,
            "chunk_index": self.chunk_index,
            "content_hash": self.content_hash,
            "has_code": bool(self.metadata.get("has_code")),
            "char_count": len(self.content),
            "content": self.content,
            "metadata": metadata,
            "document_id": self.document_id,
            "version_id": self.version_id,
        }


@dataclass(frozen=True, slots=True)
class _Piece:
    page: int
    block: DocumentBlock
    text: str
    segment: int = 0
    page_metadata: dict[str, Any] | None = None


def _split_block(
    block: DocumentBlock,
    page: int,
    page_metadata: dict[str, Any],
    config: DocumentConfig,
) -> list[_Piece]:
    text = block.text.strip()
    if not text:
        return []
    budget_chars = max(4, config.indexing_chunk_tokens * 4)
    if len(text) <= budget_chars or (
        block.block_type is BlockType.TABLE and config.indexing_preserve_tables
    ):
        return [_Piece(page, block, text, page_metadata=page_metadata)]
    overlap = (
        min(config.indexing_overlap_tokens * 4, budget_chars - 1)
        if block.block_type in _OVERLAP_TYPES
        else 0
    )
    pieces: list[_Piece] = []
    start = 0
    segment = 0
    while start < len(text):
        end = min(len(text), start + budget_chars)
        if end < len(text):
            boundary = text.rfind(" ", start, end)
            if boundary > start + budget_chars // 2:
                end = boundary
        value = text[start:end].strip()
        if value:
            pieces.append(_Piece(page, block, value, segment, page_metadata))
            segment += 1
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return pieces


def _source_sha(document: DocumentIR) -> str:
    value = str(
        document.metadata.get("source_sha256")
        or document.provenance.metadata.get("sha256")
        or ""
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("DocumentIR must carry its canonical lowercase source SHA-256")
    return value


def _metadata_values(piece: _Piece, key: str) -> list[str]:
    values: list[str] = []
    raw = piece.block.metadata.get(key)
    if isinstance(raw, str) and raw.strip():
        values.extend(item.strip() for item in raw.split("+") if item.strip())
    elif isinstance(raw, list):
        values.extend(str(item).strip() for item in raw if str(item).strip())
    return values


def _mapping_value(metadata: dict[str, Any], key: str) -> Any:
    value = metadata.get(key)
    if value is not None:
        return value
    ocr = metadata.get("ocr")
    return ocr.get(key) if isinstance(ocr, dict) else None


def _values(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split("+") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _build_chunk(
    document: DocumentIR,
    source_sha: str,
    pieces: list[_Piece],
    index: int,
) -> DocumentChunk:
    content = "\n\n".join(piece.text for piece in pieces)
    pages = [piece.page for piece in pieces]
    block_ids = tuple(dict.fromkeys(piece.block.block_id for piece in pieces))
    block_hashes = tuple(
        _digest(_normalized_text(piece.block.text)) for piece in pieces
    )
    coordinates = tuple(
        {
            "page": piece.page,
            "block_id": piece.block.block_id,
            "segment": piece.segment,
            "bounding_box": _box_dict(piece.block.bounding_box),
        }
        for piece in pieces
        if piece.block.bounding_box is not None
    )
    languages = tuple(
        dict.fromkeys(
            value
            for piece in pieces
            for value in (
                _metadata_values(piece, "language")
                or _values(_mapping_value(piece.page_metadata or {}, "language"))
                or _values(document.metadata.get("language"))
            )
        )
    )
    directions = tuple(
        dict.fromkeys(
            value
            for piece in pieces
            for value in (
                _metadata_values(piece, "direction")
                or _values(_mapping_value(piece.page_metadata or {}, "direction"))
                or _values(document.metadata.get("direction"))
            )
        )
    )
    confidences = [
        (
            piece.block.confidence
            if piece.block.confidence is not None
            else _mapping_value(piece.page_metadata or {}, "confidence")
        )
        for piece in pieces
        if (
            piece.block.confidence is not None
            or isinstance(_mapping_value(piece.page_metadata or {}, "confidence"), (int, float))
        )
    ]
    structural = {
        "document_id": str(document.document_id),
        "version_id": str(document.version_id),
        "pages": [min(pages), max(pages)],
        "blocks": [
            {
                "id": piece.block.block_id,
                "segment": piece.segment,
                "hash": _digest(_normalized_text(piece.text)),
            }
            for piece in pieces
        ],
        "content": _normalized_text(content),
    }
    chunk_hash = _digest(_canonical(structural))
    chunk_id = f"doc:{chunk_hash}"
    return DocumentChunk(
        chunk_id=chunk_id,
        chunk_hash=chunk_hash,
        content_hash=chunk_hash,
        document_id=str(document.document_id),
        version_id=str(document.version_id),
        source_sha256=source_sha,
        content=content,
        chunk_index=index,
        page_start=min(pages),
        page_end=max(pages),
        block_ids=block_ids,
        block_hashes=block_hashes,
        coordinates=coordinates,
        parser=document.provenance.parser,
        parser_version=document.provenance.parser_version,
        language=languages,
        direction=directions,
        ocr=any(
            bool(piece.block.metadata.get("ocr"))
            or str(_mapping_value(piece.page_metadata or {}, "status"))
            in {"completed", "partial"}
            for piece in pieces
        ),
        confidence=(
            round(sum(confidences) / len(confidences), 6) if confidences else None
        ),
        metadata={
            "schema_version": document.schema_version,
            "has_code": any(piece.block.block_type is BlockType.CODE for piece in pieces),
            "block_types": list(
                dict.fromkeys(piece.block.block_type.value for piece in pieces)
            ),
        },
    )


def chunk_document_ir(
    document: DocumentIR,
    config: DocumentConfig,
) -> tuple[DocumentChunk, ...]:
    """Chunk canonical IR structurally; parser-rendered ad-hoc strings are rejected."""

    if not config.indexing_enabled:
        return ()
    source_sha = _source_sha(document)
    budget = config.indexing_chunk_tokens
    overlap_budget = config.indexing_overlap_tokens
    chunks: list[DocumentChunk] = []
    current: list[_Piece] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if not current:
            return
        chunks.append(_build_chunk(document, source_sha, current, len(chunks)))
        eligible: list[_Piece] = []
        used = 0
        for piece in reversed(current):
            cost = _token_count(piece.text)
            if piece.block.block_type not in _OVERLAP_TYPES or used + cost > overlap_budget:
                break
            eligible.append(piece)
            used += cost
        current = list(reversed(eligible))
        current_tokens = used

    for page in document.pages:
        if config.indexing_preserve_page_boundaries and current:
            flush()
            current = []
            current_tokens = 0
        for block in page.blocks:
            for piece in _split_block(
                block, page.page_number, dict(page.metadata), config
            ):
                cost = _token_count(piece.text)
                if current and current_tokens + cost > budget:
                    flush()
                current.append(piece)
                current_tokens += cost
                if cost >= budget:
                    flush()
                    current = []
                    current_tokens = 0
        if config.indexing_preserve_page_boundaries:
            flush()
            current = []
            current_tokens = 0
    flush()
    return tuple(chunks)
