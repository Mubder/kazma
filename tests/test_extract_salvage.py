"""Docling / remote PDF salvage (no live APIs)."""

from __future__ import annotations

from pathlib import Path

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.extract_salvage import (
    SALVAGE_SCORE,
    ir_from_markdown,
    maybe_salvage_extract,
)
from kazma_core.documents.indexer import chunk_document_ir
from kazma_core.documents.models import (
    BlockType,
    DocumentBlock,
    DocumentId,
    DocumentIR,
    DocumentPage,
    Provenance,
    VersionId,
)
from kazma_core.documents.service import DocumentService


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


@pytest.mark.parametrize('text', ['Recovered source text.', 'نص المصدر المستخرج.'])
def test_salvage_segments_never_claim_physical_pdf_pages(tmp_path, text):
    from dataclasses import replace
    original = replace(_ir('', 0), metadata={'source_sha256': 'a' * 64})
    cfg = DocumentConfig(storage_root=tmp_path / 'store')
    recovered = ir_from_markdown(tmp_path / 'source.pdf', original,
        '\n\n'.join(f'{text} {i}' for i in range(81)), extractor='inert-fixture', config=cfg)
    assert len(recovered.pages) == 3  # synthetic grouping of forty blocks
    assert recovered.metadata['physical_page_mapping'] is False
    assert all(page.metadata['kind'] == 'segment' for page in recovered.pages)
    read = DocumentService(config=cfg).read_ir(recovered, fence=False)
    assert 'source page unknown' in read.text and '--- Page ' not in read.text
    chunks = chunk_document_ir(recovered, cfg)
    assert chunks and all(' page:' not in c.citation_label for c in chunks)
    assert all(' segment:' in c.citation_label for c in chunks)
    assert all(c.to_knowledge_dict(library_id='fixture')['metadata']['physical_page_mapping'] is False for c in chunks)


def test_a_remote_parse_that_does_not_run_is_said(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog) -> None:
    """Remote parsing switched on, then refused or failing (no audit scope, an
    audit that could not be written, a provider error): a WARNING naming the
    error type, never only DEBUG, and the local reading is kept (Q04
    defensive check C10, 2026-10-15). The provider's message is not logged."""
    import logging

    from kazma_core.documents import extract_salvage

    def refuse(path, document):
        raise RuntimeError("provider said: secret-request-detail")

    monkeypatch.setattr(extract_salvage, "try_remote_parse", refuse)
    monkeypatch.delenv("KAZMA_REMOTE_PARSE", raising=False)
    config = DocumentConfig(storage_root=tmp_path / "store", security_local_salvage=False,
                            security_remote_parse=True)
    doc = _ir("weak", 0.1)
    with caplog.at_level(logging.WARNING, logger="kazma_core.documents.extract_salvage"):
        out = maybe_salvage_extract(tmp_path / "a.pdf", doc, config=config)
    assert out is doc
    warned = [r for r in caplog.records
              if r.name == "kazma_core.documents.extract_salvage" and r.levelno == logging.WARNING]
    assert warned and "RuntimeError" in warned[0].getMessage()
    assert "secret-request-detail" not in warned[0].getMessage()


def _salvage_calls_allowing_local(source: str) -> list[int]:
    """Lines calling maybe_salvage_extract without allow_local=False."""
    import ast

    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "maybe_salvage_extract":
            local = next((kw.value for kw in node.keywords if kw.arg == "allow_local"), None)
            if not (isinstance(local, ast.Constant) and local.value is False):
                lines.append(node.lineno)
    return lines


def test_local_salvage_runs_only_in_the_parser_worker() -> None:
    """Local salvage (Docling) runs only inside the bounded worker
    (AGENTS.md section 19 K): every other caller passes allow_local=False.
    Its default is True, so a new caller in the Kazma process would run it
    there (Q04 defensive check C10, 2026-10-15)."""
    repo = Path(__file__).resolve().parents[1]
    worker = repo / "kazma-core/kazma_core/documents/parser_worker.py"
    offenders = []
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"):
        for path in (repo / package).rglob("*.py"):
            if path == worker:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "maybe_salvage_extract(" in text:
                offenders += [f"{path.relative_to(repo)}:{line}" for line in _salvage_calls_allowing_local(text)]
    assert offenders == [], offenders
    # Negative control: a caller relying on the default is seen.
    assert _salvage_calls_allowing_local("maybe_salvage_extract(p, d, config=c)") == [1]
