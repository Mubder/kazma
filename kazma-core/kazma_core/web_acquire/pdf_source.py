"""A PDF the web answered with, read through the document worker (2026-10-31).

The acquisition ladder refused every response that was not text, so a deep
research run skipped every thesis, journal article and report its searches
found as a PDF (live: the Kuwait history paper read none of its academic
sources). A PDF's text layer is now read by the document worker -- the PDF
active-content policy, the bounded sandbox or the per-job container
(AGENTS.md §19 E, E3) -- from a private temporary copy deleted afterwards. No
PDF library opens the bytes in this process, and nothing is uploaded to a
third-party parser: a transient read has no durable egress scope (§19 K).

No OCR: a scanned page takes 15-45 s, past any fetch deadline. A PDF with no
text on any page is refused as a scan, by name; one whose every page OCR
would read better is read for what it has and called thin; one with some
scanned pages keeps the pages that have text.

Knowledge crawls keep PDFs out (their documents come in through the
Documents service, and the recorded crawl corpus locks their outcomes);
the reader asks ``reads_pdfs`` for the running fetch's profile.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

__all__ = ["PDF_MAX_BYTES", "PdfText", "is_pdf", "is_pdf_type", "read_pdf_text", "reads_pdfs"]

logger = logging.getLogger(__name__)

#: A web PDF is read whole or not at all; a cut PDF has no cross-reference
#: table to read. Theses and reports run past the 5 MB a page may take.
PDF_MAX_BYTES = 20_000_000

_PDF_TYPES = {"application/pdf", "application/x-pdf", "application/acrobat", "applications/vnd.pdf"}
#: Servers that send a PDF without saying so. Only its own bytes decide then.
_UNNAMED_TYPES = {"", "application/octet-stream", "binary/octet-stream",
                  "application/download", "application/force-download", "application/unknown"}
#: The PDF header may follow up to 1 KiB of junk (PDF 32000-1, 7.5.2 note).
_HEADER_WINDOW = 1024


def _base(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _named_pdf(url: str) -> bool:
    return (urlsplit(url or "").path or "").lower().endswith(".pdf")


def reads_pdfs(profile: str | None) -> bool:
    """Whether a fetch of this profile reads a PDF answer (not Knowledge)."""
    return profile in (None, "generic")


def is_pdf_type(content_type: str, url: str) -> bool:
    """From the headers alone: will this answer be read as a PDF?

    Decides the body limit before the body is read: a named PDF type, or a
    nameless binary type at an address ending in ``.pdf``.
    """
    base = _base(content_type)
    return base in _PDF_TYPES or (base in _UNNAMED_TYPES and _named_pdf(url))


def is_pdf(content_type: str, body: object, url: str = "") -> bool:
    """A PDF answer: a PDF type, or a nameless type whose bytes open as a PDF."""
    base = _base(content_type)
    if base in _PDF_TYPES:
        return True
    if base not in _UNNAMED_TYPES or not isinstance(body, (bytes, bytearray)):
        return False
    return b"%PDF-" in bytes(body[:_HEADER_WINDOW])


@dataclass(frozen=True)
class PdfText:
    """A PDF's text layer as read: page-labelled text and what it covers."""

    text: str
    pages: int
    scanned_pages: int
    cut: bool

    @property
    def thin(self) -> bool:
        """Every page is one OCR would read better: what was read is weak."""
        return bool(self.pages) and self.scanned_pages >= self.pages

    def evidence(self) -> dict[str, object]:
        return {"format": "pdf", "pages": self.pages, "scanned_pages": self.scanned_pages,
                "text_cut": self.cut}


def _staged(body: bytes) -> Path:
    folder = Path(tempfile.mkdtemp(prefix="kazma-web-pdf-"))
    path = folder / "source.pdf"
    path.write_bytes(body)
    return path


async def read_pdf_text(body: bytes, *, max_chars: int) -> PdfText:
    """Read *body*'s text layer through the document worker.

    Raises ``AcquisitionError``: ``backend_unavailable`` (transient) when the
    worker or its broker cannot take it now, ``resource_limit`` past the
    document limits, ``unsupported_content`` when the worker refuses or
    cannot read the PDF (active content, encryption, damage),
    ``empty_extract`` for a scan, ``storage_error`` when it cannot be staged.
    """
    from kazma_core.documents.errors import DocumentLimitError, DocumentParseError
    from kazma_core.documents.service import DocumentService

    try:
        path = await asyncio.to_thread(_staged, bytes(body))
    except OSError as exc:
        raise AcquisitionError(FetchErrorCode.STORAGE,
                               f"The PDF could not be staged for reading ({type(exc).__name__}).") from exc
    try:
        service = await asyncio.to_thread(DocumentService)
        document = await service.parse_transient(path, approved_path=path, allow_ocr=False)
        scanned = await asyncio.to_thread(service.pages_needing_ocr, document)
        total = len(document.pages)
        # A scan by what it holds: no text on any page. A page OCR would
        # read better (thin, mangled) still gives what it has.
        if total and not any(block.text and block.text.strip()
                             for page in document.pages for block in page.blocks):
            from kazma_core.english_count import count_noun

            raise AcquisitionError(
                FetchErrorCode.EMPTY,
                f"This PDF is a scan: none of its {count_noun(total, 'page')} has a text layer, "
                "and a web read does not run OCR (15-45 s a page).")
        read = DocumentService.read_ir(document, max_chars=max_chars, fence=False)
    except AcquisitionError:
        raise
    except DocumentParseError as exc:
        if exc.retryable:
            raise AcquisitionError(
                FetchErrorCode.UNAVAILABLE,
                f"The document worker cannot read this PDF right now ({exc.code}); try again later.",
                transient=True) from exc
        code = FetchErrorCode.LIMIT if isinstance(exc, DocumentLimitError) else FetchErrorCode.UNSUPPORTED_CONTENT
        raise AcquisitionError(code, f"This PDF could not be read: {exc.safe_message}") from exc
    except (OSError, ValueError, RuntimeError) as exc:
        # The worker's own failures, never taken for the web's: a socket
        # error here must not send the ladder to browser recovery.
        logger.warning("[pdf_source] the document worker failed on a web PDF: %s",
                       type(exc).__name__, exc_info=True)
        raise AcquisitionError(FetchErrorCode.INTERNAL,
                               f"The document worker failed on this PDF ({type(exc).__name__}).") from exc
    finally:
        await asyncio.to_thread(shutil.rmtree, path.parent, True)
    return PdfText(text=read.text, pages=total, scanned_pages=len(scanned),
                   cut=bool(read.continuation["has_more"]))
