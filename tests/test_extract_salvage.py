"""Docling / remote PDF salvage (no live APIs)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.documents.extract_salvage import (
    SALVAGE_SCORE,
    ir_from_markdown,
    maybe_salvage_extract,
)
from kazma_core.documents.models import (
    BlockType,
    DocumentBlock,
    DocumentId,
    DocumentIR,
    DocumentPage,
    Provenance,
    VersionId,
)


def _ir(text: str, score: float) -> DocumentIR:
    page = DocumentPage(
        page_number=1,
        blocks=(
            DocumentBlock(
                block_id="p1-b1",
                block_type=BlockType.PARAGRAPH,
                text=text,
                metadata={},
            ),
        ),
    )
    return DocumentIR(
        document_id=DocumentId("00000000-0000-0000-0000-000000000001"),
        version_id=VersionId("00000000-0000-0000-0000-000000000002"),
        pages=(page,),
        provenance=Provenance(source="x.pdf", parser="pdf", parser_version="1"),
        metadata={"extraction_score": score, "source_sha256": "abc"},
    )


def test_high_score_skipped(tmp_path: Path) -> None:
    doc = _ir("Plenty of native text from PyMuPDF for this page.", 0.9)
    out = maybe_salvage_extract(tmp_path / "a.pdf", doc)
    assert out is doc


def test_markdown_maps_to_ir(tmp_path: Path) -> None:
    doc = _ir("", 0.1)
    out = ir_from_markdown(
        tmp_path / "a.pdf",
        doc,
        "Hello salvage.\n\nSecond block.",
        extractor="docling",
    )
    assert out.metadata.get("extractor") == "docling"
    assert out.metadata.get("salvaged") is True
    texts = [b.text for p in out.pages for b in p.blocks]
    assert any("Hello salvage" in t for t in texts)


def test_remote_off_does_not_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KAZMA_DOCLING", "0")
    monkeypatch.setenv("KAZMA_REMOTE_PARSE", "0")
    monkeypatch.setenv("LLAMAPARSE_API_KEY", "should-not-use")
    doc = _ir("weak", 0.1)
    out = maybe_salvage_extract(tmp_path / "a.pdf", doc)
    assert out is doc or out.metadata.get("extractor") != "llamaparse"


def test_salvage_threshold() -> None:
    assert SALVAGE_SCORE < 0.8
