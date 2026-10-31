"""A PDF the web answers with is read, through the document worker (2026-10-31).

Live: the fourth Kuwait history research run found theses and journal
articles as PDFs and read none of them -- every non-text answer was refused
``unsupported_content`` ("not extractable as a page"). The ladder now hands a
PDF's bytes to the document worker (the PDF policy and the sandbox, never a
PDF library here) and returns its text layer, page-labelled. Knowledge
crawls keep PDFs out. Real PDFs, the real worker, a mocked network.
"""

from __future__ import annotations

import importlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from kazma_core.web_acquire import FetchErrorCode, fetch_text, pdf_source

ru = importlib.import_module("kazma_core.tools.read_url")

FIRST = "The earliest written mention of the bay names the island of Failaka as Ikaros."
SECOND = "Kuwait Town was walled three times, in the 1760s, in 1814 and in 1920."


def _pdf(pages: list[str] | None = None, *, blank: int = 0) -> bytes:
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=(400, 300))
    for text in pages or []:
        page.drawString(20, 250, text[:70])
        page.drawString(20, 230, text[70:])
        page.showPage()
    for _ in range(blank):
        page.rect(40, 40, 200, 100, fill=1)  # ink, no text: what a scan looks like to the text layer
        page.showPage()
    page.save()
    return out.getvalue()


def _active_pdf() -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer._root_object[NameObject("/OpenAction")] = DictionaryObject({
        NameObject("/S"): NameObject("/JavaScript"),
        NameObject("/JS"): TextStringObject("/* inert regression fixture */")})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def target(monkeypatch):
    from kazma_core.proxy import client as pc
    from kazma_core.security import ssrf

    ru.clear_url_cache()
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_only")
    monkeypatch.delenv("KAZMA_FETCH_BACKEND", raising=False)
    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ("93.184.216.34",))
    monkeypatch.setattr(pc, "get_active_proxy_url", lambda: None)
    monkeypatch.setattr(ru, "_recover_hard_page", AsyncMock(return_value=None))
    original_client = httpx.AsyncClient

    def install(handler):
        def respond(request):
            result = handler(request)
            result.extensions["network_stream"] = SimpleNamespace(
                get_extra_info=lambda name: ("93.184.216.34", 443))
            return result
        monkeypatch.setattr(pc, "get_scraping_client", lambda **kwargs:
                            original_client(transport=httpx.MockTransport(respond)))
    yield install
    ru.clear_url_cache()


def _serve(body: bytes, content_type: str = "application/pdf"):
    return lambda request: httpx.Response(200, content=body, headers={"content-type": content_type})


@pytest.mark.asyncio
async def test_a_pdf_answer_is_read_as_its_text_layer(target):
    target(_serve(_pdf([FIRST, SECOND])))
    result = await fetch_text("https://journal.example/thesis.pdf", purpose="research_save")
    assert result.ok, result.evidence()
    assert "Ikaros" in result.text and "1814" in result.text
    assert "--- Page 2 ---" in result.text  # pages labelled, for citing
    assert result.meta["document"] == {"format": "pdf", "pages": 2, "scanned_pages": 0, "text_cut": False}
    assert result.links == () and result.links_complete is True and not result.truncated


@pytest.mark.asyncio
async def test_negative_control_the_old_gate_refuses_the_same_pdf(target, monkeypatch):
    monkeypatch.setattr(pdf_source, "reads_pdfs", lambda profile: False)
    target(_serve(_pdf([FIRST, SECOND])))
    result = await fetch_text("https://journal.example/thesis.pdf", purpose="research_save")
    assert not result.ok and result.error_code == FetchErrorCode.UNSUPPORTED_CONTENT


@pytest.mark.asyncio
async def test_a_pdf_sent_as_nameless_bytes_is_known_by_its_header(target):
    target(_serve(_pdf([FIRST]), "application/octet-stream"))
    result = await fetch_text("https://repository.example/bitstream/handle/123/456", purpose="chat")
    assert result.ok and "Ikaros" in result.text, result.evidence()


@pytest.mark.asyncio
async def test_other_binary_answers_are_still_refused(target):
    target(_serve(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png"))
    result = await fetch_text("https://journal.example/figure.png")
    assert not result.ok and result.error_code == FetchErrorCode.UNSUPPORTED_CONTENT
    target(_serve(b"PK\x03\x04" + b"\x00" * 64, "application/octet-stream"))
    result = await fetch_text("https://journal.example/data.zip")
    assert not result.ok and result.error_code == FetchErrorCode.UNSUPPORTED_CONTENT


@pytest.mark.asyncio
async def test_knowledge_crawls_keep_pdfs_out(target):
    target(_serve(_pdf([FIRST])))
    result = await fetch_text("https://journal.example/thesis.pdf", purpose="kb")
    assert not result.ok and result.error_code == FetchErrorCode.UNSUPPORTED_CONTENT


@pytest.mark.asyncio
async def test_a_pdf_is_read_whole_past_the_page_limit(target, monkeypatch):
    body = _pdf([FIRST, SECOND])
    monkeypatch.setattr(ru, "_fetch_max_bytes", lambda: 200)  # far below this PDF
    target(_serve(body))
    result = await fetch_text("https://journal.example/thesis.pdf", purpose="research_save")
    assert result.ok and "1814" in result.text, result.evidence()
    assert result.decoded_bytes == len(body)


@pytest.mark.asyncio
async def test_a_pdf_past_its_own_limit_is_refused_whole(target, monkeypatch):
    body = _pdf([FIRST, SECOND])
    monkeypatch.setattr(ru, "_fetch_max_bytes", lambda: 200)
    monkeypatch.setattr(pdf_source, "PDF_MAX_BYTES", 400)
    read = AsyncMock(side_effect=AssertionError("a cut PDF must not reach the worker"))
    monkeypatch.setattr(pdf_source, "read_pdf_text", read)
    target(_serve(body))
    result = await fetch_text("https://journal.example/thesis.pdf", purpose="research_save")
    assert not result.ok and result.error_code == FetchErrorCode.LIMIT
    assert "nothing of it was read" in result.error
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_scanned_pdf_is_refused_as_a_scan(target):
    target(_serve(_pdf(blank=2)))
    result = await fetch_text("https://archive.example/scan.pdf", purpose="research_save")
    assert not result.ok and result.error_code == FetchErrorCode.EMPTY, result.evidence()
    assert "scan" in result.error and "2 pages" in result.error


@pytest.mark.asyncio
async def test_a_partly_scanned_pdf_keeps_its_text_pages(target):
    target(_serve(_pdf([FIRST], blank=1)))
    result = await fetch_text("https://archive.example/mixed.pdf", purpose="research_save")
    assert result.ok and "Ikaros" in result.text, result.evidence()
    assert result.meta["document"]["scanned_pages"] == 1


@pytest.mark.asyncio
async def test_text_ocr_would_read_better_is_still_read_and_called_thin(target, monkeypatch):
    """A page OCR would read better (mangled, image-heavy) still has text: a
    scan is only a PDF with no text on any page."""
    from kazma_core.documents.service import DocumentService

    monkeypatch.setattr(DocumentService, "pages_needing_ocr",
                        lambda self, document: tuple(p.page_number for p in document.pages))
    target(_serve(_pdf([FIRST, SECOND])))
    result = await fetch_text("https://archive.example/weak.pdf", purpose="research_save")
    assert result.ok and result.quality == "thin" and "Ikaros" in result.text, result.evidence()
    assert result.meta["document"]["scanned_pages"] == 2


@pytest.mark.asyncio
async def test_a_pdf_with_active_content_is_refused_by_the_worker(target):
    target(_serve(_active_pdf()))
    result = await fetch_text("https://hostile.example/report.pdf", purpose="research_save")
    assert not result.ok and result.error_code == FetchErrorCode.UNSUPPORTED_CONTENT, result.evidence()
    assert "could not be read" in result.error


@pytest.mark.asyncio
async def test_a_busy_worker_is_a_passing_failure(target, monkeypatch):
    from kazma_core.documents.errors import DocumentParseError
    from kazma_core.documents.service import DocumentService

    async def busy(self, *args, **kwargs):
        raise DocumentParseError("The document worker broker is full.", code="worker_isolation_busy")

    monkeypatch.setattr(DocumentService, "parse_transient", busy)
    target(_serve(_pdf([FIRST])))
    result = await fetch_text("https://journal.example/thesis.pdf", purpose="research_save")
    assert not result.ok and result.error_code == FetchErrorCode.UNAVAILABLE and result.transient


@pytest.mark.asyncio
async def test_the_staged_copy_is_removed(target, monkeypatch):
    staged: list[Path] = []
    original = pdf_source._staged

    def remember(body):
        path = original(body)
        staged.append(path)
        return path

    monkeypatch.setattr(pdf_source, "_staged", remember)
    target(_serve(_pdf([FIRST])))
    assert (await fetch_text("https://journal.example/a.pdf", purpose="chat")).ok
    target(_serve(_active_pdf()))
    assert not (await fetch_text("https://journal.example/b.pdf", purpose="chat")).ok
    assert len(staged) == 2 and not any(path.parent.exists() for path in staged)


@pytest.mark.asyncio
async def test_research_saves_a_pdf_source(target, monkeypatch, tmp_path):
    monkeypatch.setattr(ru, "_workspace_root", lambda: tmp_path)
    target(_serve(_pdf([FIRST, SECOND])))
    saved = await ru.save_url_extract("https://journal.example/thesis.pdf", path="research/thesis.md")
    assert saved.ok, (saved.error, saved.acquisition.evidence())
    text = (tmp_path / "research" / "thesis.md").read_text(encoding="utf-8")
    assert "# Source: https://journal.example/thesis.pdf" in text and "Ikaros" in text and "1814" in text


@pytest.mark.asyncio
async def test_chat_reads_a_pdf_fenced(target):
    target(_serve(_pdf([FIRST, SECOND])))
    window = await ru.read_url("https://journal.example/thesis.pdf")
    assert "Ikaros" in window and "UNTRUSTED" in window.upper()
