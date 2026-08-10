"""One-page-at-a-time PDF and image rasterization with live readiness."""

from __future__ import annotations

import importlib
import importlib.metadata
import math
import shutil
import uuid
from pathlib import Path
from typing import Protocol

from ..errors import DocumentLimitError, DocumentOcrError, DocumentOcrUnavailableError
from ..models import DocumentPage
from ..sandbox import SandboxRequest, run_isolated_subprocess
from .base import OcrComponentHealth, OcrReadiness

__all__ = [
    "PdfRasterizer",
    "get_image_parser_health",
    "get_pdf_rasterizer",
    "render_image_page",
]


class PdfRasterizer(Protocol):
    name: str
    version: str | None

    def render_page(
        self,
        source: Path,
        page: DocumentPage,
        *,
        dpi: int,
        max_pixels: int,
        timeout_seconds: float,
        work_dir: Path,
    ) -> Path: ...


def _pixel_dimensions(page: DocumentPage, dpi: int) -> tuple[int, int] | None:
    if page.width is None or page.height is None:
        return None
    return (
        max(1, math.ceil(page.width / 72.0 * dpi)),
        max(1, math.ceil(page.height / 72.0 * dpi)),
    )


def _enforce_page_pixels(page: DocumentPage, dpi: int, max_pixels: int) -> None:
    dimensions = _pixel_dimensions(page, dpi)
    if dimensions and dimensions[0] * dimensions[1] > max_pixels:
        raise DocumentLimitError(
            f"OCR raster for page {page.page_number} exceeds the configured pixel limit"
        )


def _validate_output(path: Path, max_pixels: int) -> Path:
    try:
        from PIL import Image
    except ImportError as exc:
        raise DocumentOcrUnavailableError(
            "Pillow is required to validate rasterized OCR pages"
        ) from exc
    if not path.is_file():
        raise DocumentOcrError("PDF rasterizer produced no page image")
    with Image.open(path) as image:
        width, height = image.size
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise DocumentLimitError("OCR raster exceeds the configured pixel limit")
        image.verify()
    return path


class _PdftoppmRasterizer:
    name = "pdftoppm"

    def __init__(self, executable: str, version: str | None) -> None:
        self.executable = executable
        self.version = version

    def render_page(
        self,
        source: Path,
        page: DocumentPage,
        *,
        dpi: int,
        max_pixels: int,
        timeout_seconds: float,
        work_dir: Path,
    ) -> Path:
        _enforce_page_pixels(page, dpi, max_pixels)
        prefix = work_dir / f"pdf-page-{page.page_number}-{uuid.uuid4().hex}"
        result = run_isolated_subprocess(
            SandboxRequest(
                command=(
                    self.executable,
                    "-f",
                    str(page.page_number),
                    "-l",
                    str(page.page_number),
                    "-singlefile",
                    "-r",
                    str(dpi),
                    "-png",
                    str(source),
                    str(prefix),
                ),
                work_dir=work_dir,
                timeout_seconds=timeout_seconds,
                stdout_limit_bytes=4_096,
                stderr_limit_bytes=65_536,
            )
        )
        if result.timed_out:
            raise DocumentOcrError(
                "PDF page rasterization exceeded its time limit",
                code="ocr_rasterizer_timeout",
            )
        if result.output_limit_exceeded:
            raise DocumentLimitError("PDF rasterizer output exceeded its limit")
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[:500]
            raise DocumentOcrError(
                f"PDF page rasterization failed{': ' + detail if detail else ''}"
            )
        return _validate_output(prefix.with_suffix(".png"), max_pixels)


class _PyMuPdfRasterizer:
    name = "pymupdf"

    def __init__(self, version: str | None) -> None:
        self.version = version

    def render_page(
        self,
        source: Path,
        page: DocumentPage,
        *,
        dpi: int,
        max_pixels: int,
        timeout_seconds: float,
        work_dir: Path,
    ) -> Path:
        del timeout_seconds
        _enforce_page_pixels(page, dpi, max_pixels)
        import fitz

        output = work_dir / f"pdf-page-{page.page_number}-{uuid.uuid4().hex}.png"
        with fitz.open(source) as document:
            pixmap = document.load_page(page.page_number - 1).get_pixmap(
                matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0),
                alpha=False,
            )
            if pixmap.width * pixmap.height > max_pixels:
                raise DocumentLimitError("OCR raster exceeds the configured pixel limit")
            pixmap.save(output)
        return _validate_output(output, max_pixels)


class _PdfiumRasterizer:
    name = "pypdfium2"

    def __init__(self, version: str | None) -> None:
        self.version = version

    def render_page(
        self,
        source: Path,
        page: DocumentPage,
        *,
        dpi: int,
        max_pixels: int,
        timeout_seconds: float,
        work_dir: Path,
    ) -> Path:
        del timeout_seconds
        _enforce_page_pixels(page, dpi, max_pixels)
        import pypdfium2

        output = work_dir / f"pdf-page-{page.page_number}-{uuid.uuid4().hex}.png"
        document = pypdfium2.PdfDocument(str(source))
        try:
            pdf_page = document[page.page_number - 1]
            try:
                bitmap = pdf_page.render(scale=dpi / 72.0)
                try:
                    image = bitmap.to_pil()
                    if image.width * image.height > max_pixels:
                        raise DocumentLimitError("OCR raster exceeds the configured pixel limit")
                    image.save(output, format="PNG")
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            document.close()
        return _validate_output(output, max_pixels)


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "present"


def get_pdf_rasterizer() -> tuple[PdfRasterizer | None, OcrComponentHealth]:
    executable = shutil.which("pdftoppm")
    if executable:
        result = run_isolated_subprocess(
            SandboxRequest(
                command=(executable, "-v"),
                work_dir=Path.cwd(),
                timeout_seconds=5,
                stdout_limit_bytes=8_192,
                stderr_limit_bytes=8_192,
            )
        )
        if not result.timed_out and not result.output_limit_exceeded and result.returncode == 0:
            version = (result.stderr or result.stdout).decode(
                "utf-8", errors="replace"
            ).splitlines()[0][:200]
            return _PdftoppmRasterizer(executable, version), OcrComponentHealth(
                OcrReadiness.READY, "pdftoppm", version
            )
    for module, distribution, factory in (
        ("fitz", "PyMuPDF", _PyMuPdfRasterizer),
        ("pypdfium2", "pypdfium2", _PdfiumRasterizer),
    ):
        try:
            importlib.import_module(module)
            version = _distribution_version(distribution)
            return factory(version), OcrComponentHealth(
                OcrReadiness.READY, factory.name, version
            )
        except Exception:
            continue
    return None, OcrComponentHealth(
        OcrReadiness.UNAVAILABLE,
        None,
        reason="No healthy one-page PDF rasterizer is installed "
        "(pdftoppm, PyMuPDF, or pypdfium2)",
    )


def get_image_parser_health() -> OcrComponentHealth:
    try:
        importlib.import_module("PIL.Image")
        return OcrComponentHealth(
            OcrReadiness.READY,
            "Pillow",
            _distribution_version("Pillow"),
        )
    except Exception as exc:
        return OcrComponentHealth(
            OcrReadiness.UNAVAILABLE,
            "Pillow",
            reason=f"Pillow image parser is unavailable ({type(exc).__name__})",
        )


def render_image_page(
    source: Path,
    page_number: int,
    *,
    max_pixels: int,
    work_dir: Path,
) -> Path:
    try:
        from PIL import Image
    except ImportError as exc:
        raise DocumentOcrUnavailableError("Pillow image support is unavailable") from exc
    output = work_dir / f"image-page-{page_number}-{uuid.uuid4().hex}.png"
    try:
        with Image.open(source) as image:
            image.seek(page_number - 1)
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise DocumentLimitError("OCR image exceeds the configured pixel limit")
            if image.mode not in {"1", "L", "RGB", "RGBA"}:
                image = image.convert("RGB")
            image.save(output, format="PNG")
    except EOFError as exc:
        raise DocumentOcrError(f"Image page {page_number} does not exist") from exc
    return _validate_output(output, max_pixels)
