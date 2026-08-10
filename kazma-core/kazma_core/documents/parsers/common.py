"""Shared bounded IR construction for document parsers."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import DocumentConfig
from ..errors import DocumentLimitError
from ..models import (
    BlockType,
    DocumentBlock,
    DocumentId,
    DocumentIR,
    DocumentPage,
    Provenance,
    VersionId,
)

__all__ = ["IRBuilder", "ParseContext", "read_utf8", "sha256_path"]

_ID_NAMESPACE = uuid.UUID("0e50731e-54ed-46c6-81bd-66288ad3df1e")


@dataclass(frozen=True, slots=True)
class ParseContext:
    config: DocumentConfig
    source_sha256: str
    mime_type: str
    extension: str
    parser_id: str
    parser_version: str


@dataclass(slots=True)
class IRBuilder:
    path: Path
    context: ParseContext
    pages: list[DocumentPage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _total_chars: int = 0
    _cells: int = 0
    _images: int = 0

    def add_page(
        self,
        blocks: list[tuple[BlockType, str, dict[str, Any] | None]],
        *,
        metadata: dict[str, Any] | None = None,
        width: float | None = None,
        height: float | None = None,
        rotation: int = 0,
    ) -> None:
        page_number = len(self.pages) + 1
        page_chars = 0
        output: list[DocumentBlock] = []
        for index, (block_type, text, block_metadata) in enumerate(blocks, start=1):
            remaining_page = self.context.config.max_output_chars_per_page - page_chars
            remaining_total = self.context.config.max_output_chars_total - self._total_chars
            allowed = min(remaining_page, remaining_total)
            if allowed <= 0:
                raise DocumentLimitError("Parsed document output exceeds the configured limit")
            normalized = str(text)
            if len(normalized) > allowed:
                normalized = normalized[:allowed]
                self.warnings.append(
                    f"Output truncated on page {page_number} at the configured character limit"
                )
            page_chars += len(normalized)
            self._total_chars += len(normalized)
            output.append(
                DocumentBlock(
                    block_id=f"p{page_number}-b{index}",
                    block_type=block_type,
                    text=normalized,
                    metadata=block_metadata or {},
                )
            )
            if len(normalized) < len(str(text)):
                break
        self.pages.append(
            DocumentPage(
                page_number=page_number,
                blocks=tuple(output),
                width=width,
                height=height,
                rotation=rotation,
                metadata=metadata or {},
            )
        )

    def count_cells(self, count: int) -> None:
        self._cells += count
        if self._cells > self.context.config.max_cells:
            raise DocumentLimitError("Spreadsheet/table cell count exceeds the configured limit")

    def count_images(self, count: int) -> None:
        self._images += count
        if self._images > self.context.config.max_images:
            raise DocumentLimitError("Document image count exceeds the configured limit")

    def build(self, *, metadata: dict[str, Any] | None = None) -> DocumentIR:
        seed = f"{self.context.source_sha256}:{self.context.parser_id}"
        document_id = DocumentId(uuid.uuid5(_ID_NAMESPACE, seed))
        version_id = VersionId(uuid.uuid5(_ID_NAMESPACE, f"{seed}:{self.context.parser_version}"))
        combined = {
            "source_sha256": self.context.source_sha256,
            "source_mime": self.context.mime_type,
            "source_extension": self.context.extension,
            "source_name": self.path.name,
            "warnings": list(dict.fromkeys(self.warnings)),
            **(metadata or {}),
        }
        return DocumentIR(
            document_id=document_id,
            version_id=version_id,
            pages=tuple(self.pages),
            provenance=Provenance(
                source=str(self.path),
                parser=self.context.parser_id,
                parser_version=self.context.parser_version,
                metadata={
                    "sha256": self.context.source_sha256,
                    "mime_type": self.context.mime_type,
                    "extension": self.context.extension,
                },
            ),
            metadata=combined,
        )


def read_utf8(path: Path) -> str:
    """Read supported text without lossy replacement."""

    return path.read_text(encoding="utf-8-sig")


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
