"""JSON-file protocol worker for bounded PDF operations and secure redaction."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

_PROTOCOL_VERSION = 1


class WorkerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    temporary = path.with_suffix(".writing")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("protocol_version") != _PROTOCOL_VERSION:
        raise WorkerError("invalid_request", "Invalid PDF mutation request")
    return value


def _sources(request: dict[str, Any]) -> tuple[Path, ...]:
    values = request.get("sources")
    if not isinstance(values, list) or not values:
        raise WorkerError("invalid_request", "PDF mutation requires source files")
    if len(values) > int(request.get("max_files", 10)):
        raise WorkerError("document_limit_exceeded", "Too many PDF inputs")
    result: list[Path] = []
    aggregate = 0
    for item in values:
        if not isinstance(item, dict):
            raise WorkerError("invalid_request", "Invalid PDF source record")
        path = Path(str(item.get("path", ""))).resolve(strict=True)
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            raise WorkerError("document_changed", "A PDF source changed before mutation")
        aggregate += path.stat().st_size
        if aggregate > int(request.get("max_aggregate_bytes", 50 * 1024 * 1024)):
            raise WorkerError("document_limit_exceeded", "PDF inputs exceed aggregate limit")
        result.append(path)
    return tuple(result)


def _reader(path: Path) -> Any:
    from pypdf import PdfReader

    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise WorkerError("encrypted_document", "Encrypted PDFs are not supported")
    root = reader.trailer.get("/Root", {})
    forbidden = ("/OpenAction", "/AA", "/JavaScript", "/Launch")
    if any(key in root for key in forbidden):
        raise WorkerError("unsafe_document", "PDF contains executable actions")
    names = root.get("/Names")
    if names is not None:
        names = names.get_object()
        if any(key in names for key in ("/JavaScript", "/EmbeddedFiles")):
            raise WorkerError("unsafe_document", "PDF contains scripts or attachments")
    for page in reader.pages:
        if "/AA" in page:
            raise WorkerError("unsafe_document", "PDF page contains executable actions")
        for reference in page.get("/Annots", ()):
            annotation = reference.get_object()
            if any(key in annotation for key in ("/A", "/AA", "/JS", "/JavaScript")):
                raise WorkerError("unsafe_document", "PDF annotation contains executable actions")
    return reader


def _merge(sources: tuple[Path, ...], output: Path, max_pages: int) -> dict[str, Any]:
    from pypdf import PdfWriter

    writer = PdfWriter()
    page_count = 0
    for source in sources:
        reader = _reader(source)
        page_count += len(reader.pages)
        if page_count > max_pages:
            writer.close()
            raise WorkerError("document_limit_exceeded", "Merged PDF exceeds page limit")
        for page in reader.pages:
            writer.add_page(page)
    writer.metadata = {}
    with output.open("wb") as handle:
        writer.write(handle)
    writer.close()
    return {"page_count": page_count, "input_count": len(sources)}


def _split(
    source: Path, output: Path, max_pages: int, start_page: int, end_page: int
) -> dict[str, Any]:
    from pypdf import PdfWriter

    reader = _reader(source)
    total = len(reader.pages)
    end = total if end_page == 0 else end_page
    if start_page < 1 or end < start_page or end > total:
        raise WorkerError("invalid_page_range", "Requested PDF page range is invalid")
    count = end - start_page + 1
    if count > max_pages:
        raise WorkerError("document_limit_exceeded", "Split output exceeds page limit")
    writer = PdfWriter()
    for index in range(start_page - 1, end):
        writer.add_page(reader.pages[index])
    writer.metadata = {}
    with output.open("wb") as handle:
        writer.write(handle)
    writer.close()
    return {"page_count": count, "source_page_count": total, "start_page": start_page, "end_page": end}


def _info(source: Path, max_pages: int) -> dict[str, Any]:
    reader = _reader(source)
    pages = len(reader.pages)
    if pages > max_pages:
        raise WorkerError("document_limit_exceeded", "PDF exceeds page limit")
    metadata = reader.metadata or {}
    fields = reader.get_fields() or {}
    first = reader.pages[0] if pages else None
    return {
        "page_count": pages,
        "title": str(metadata.get("/Title", "")),
        "author": str(metadata.get("/Author", "")),
        "subject": str(metadata.get("/Subject", "")),
        "creator": str(metadata.get("/Creator", "")),
        "producer": str(metadata.get("/Producer", "")),
        "field_names": sorted(str(name) for name in fields),
        "first_page": (
            {
                "width": float(first.mediabox.width),
                "height": float(first.mediabox.height),
                "rotation": int(first.get("/Rotate", 0)),
            }
            if first is not None
            else None
        ),
    }


def _fill_form(
    source: Path, output: Path, max_pages: int, fields: dict[str, str]
) -> tuple[dict[str, Any], list[str]]:
    from pypdf import PdfWriter

    reader = _reader(source)
    if len(reader.pages) > max_pages:
        raise WorkerError("document_limit_exceeded", "PDF exceeds page limit")
    root = reader.trailer.get("/Root", {})
    acroform = root.get("/AcroForm")
    if acroform is not None and "/XFA" in acroform.get_object():
        raise WorkerError("unsupported_pdf_form", "XFA forms are not supported")
    available = reader.get_fields() or {}
    unknown = sorted(set(fields) - set(available))
    if unknown:
        raise WorkerError("unknown_form_field", "One or more requested form fields do not exist")
    if not fields:
        raise WorkerError("invalid_request", "At least one form field value is required")
    writer = PdfWriter()
    writer.append(reader)
    for page in writer.pages:
        writer.update_page_form_field_values(page, fields, auto_regenerate=False)
    writer.metadata = {}
    with output.open("wb") as handle:
        writer.write(handle)
    writer.close()
    return (
        {"page_count": len(reader.pages), "fields_filled": len(fields)},
        ("Output form fields remain editable; scripts/actions were rejected",),
    )


def _term_in_bytes(payload: bytes, term: str) -> bool:
    variants = (
        term.encode("utf-8"),
        term.encode("utf-16-le"),
        term.encode("utf-16-be"),
    )
    lowered = payload.lower()
    return any(value.lower() in lowered for value in variants if value)


def _redact(
    source: Path,
    output: Path,
    max_pages: int,
    terms: list[str],
) -> dict[str, Any]:
    import fitz
    from PIL import Image, ImageDraw

    if not terms or any(not isinstance(term, str) or not term.strip() for term in terms):
        raise WorkerError("invalid_request", "Redaction requires non-empty text terms")
    if len(terms) > 100 or sum(len(term) for term in terms) > 10_000:
        raise WorkerError("document_limit_exceeded", "Redaction term limit exceeded")
    source_doc = fitz.open(source)
    if source_doc.needs_pass:
        source_doc.close()
        raise WorkerError("encrypted_document", "Encrypted PDFs are not supported")
    if source_doc.page_count > max_pages:
        source_doc.close()
        raise WorkerError("document_limit_exceeded", "PDF exceeds page limit")
    for page_index in range(source_doc.page_count):
        page = source_doc.load_page(page_index)
        raw_blocks = page.get_text("rawdict").get("blocks", [])
        has_non_text_blocks = any(block.get("type") != 0 for block in raw_blocks)
        if has_non_text_blocks or page.get_images(full=True) or page.get_drawings():
            source_doc.close()
            raise WorkerError(
                "redaction_source_not_verifiable",
                "Secure automatic redaction requires text-only PDF pages; "
                "image or vector content requires reviewed redaction",
            )
    output_doc = fitz.open()
    counts: list[int] = []
    matched_pages: list[int] = []
    term_counts = [0 for _ in terms]
    matrix = fitz.Matrix(200 / 72, 200 / 72)
    for page_index in range(source_doc.page_count):
        page = source_doc.load_page(page_index)
        rectangles = []
        for term_index, term in enumerate(terms):
            matches = page.search_for(term, quads=False)
            term_counts[term_index] += len(matches)
            rectangles.extend(matches)
        pixmap = page.get_pixmap(matrix=matrix, alpha=False, annots=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        draw = ImageDraw.Draw(image)
        scale_x = pixmap.width / page.rect.width
        scale_y = pixmap.height / page.rect.height
        for rect in rectangles:
            draw.rectangle(
                (
                    max(0, int(rect.x0 * scale_x) - 2),
                    max(0, int(rect.y0 * scale_y) - 2),
                    min(pixmap.width, int(rect.x1 * scale_x) + 2),
                    min(pixmap.height, int(rect.y1 * scale_y) + 2),
                ),
                fill="black",
            )
        if rectangles:
            matched_pages.append(page_index + 1)
        counts.append(len(rectangles))
        image_bytes = io.BytesIO()
        image.save(image_bytes, format="PNG", optimize=False)
        rebuilt = output_doc.new_page(width=page.rect.width, height=page.rect.height)
        rebuilt.insert_image(rebuilt.rect, stream=image_bytes.getvalue())
        image.close()
    source_doc.close()
    if any(count == 0 for count in term_counts):
        output_doc.close()
        raise WorkerError(
            "redaction_term_not_located",
            "One or more redaction terms could not be located with verifiable coordinates",
        )
    output_doc.set_metadata({})
    output_doc.save(output, garbage=4, clean=True, deflate=True, incremental=False)
    output_doc.close()

    checks = {
        "text_reextract": False,
        "raw_bytes": False,
        "structure": False,
        "rendered_pages": False,
    }
    verify = fitz.open(output)
    try:
        extracted = "\n".join(page.get_text("text") for page in verify)
        if any(term.casefold() in extracted.casefold() for term in terms):
            raise WorkerError("redaction_verification_failed", "Redacted text remains extractable")
        checks["text_reextract"] = True
        raw = output.read_bytes()
        if any(_term_in_bytes(raw, term) for term in terms):
            raise WorkerError("redaction_verification_failed", "Redacted text remains in PDF bytes")
        checks["raw_bytes"] = True
        metadata_fields = (
            "title",
            "author",
            "subject",
            "keywords",
            "creator",
            "producer",
            "creationDate",
            "modDate",
            "trapped",
        )
        if any(verify.metadata.get(key) for key in metadata_fields) or verify.embfile_count():
            raise WorkerError("redaction_verification_failed", "PDF metadata or attachments remain")
        for page in verify:
            if page.first_annot is not None or page.first_widget is not None:
                raise WorkerError("redaction_verification_failed", "PDF annotations or forms remain")
        checks["structure"] = True
        if verify.page_count != len(counts) or verify.page_count == 0:
            raise WorkerError(
                "redaction_verification_failed", "Redacted PDF page structure changed"
            )
        for page in verify:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(0.25, 0.25), alpha=False)
            if pixmap.width <= 0 or pixmap.height <= 0 or not pixmap.samples:
                raise WorkerError("redaction_verification_failed", "A redacted page did not render")
        checks["rendered_pages"] = True
    finally:
        verify.close()
    return {
        "matches_removed": sum(counts),
        "pages_redacted": matched_pages,
        "page_match_counts": counts,
        "checks": checks,
    }


def _validate_output(path: Path, expected_pages: int | None = None) -> None:
    reader = _reader(path)
    if not reader.pages:
        raise WorkerError("invalid_output", "PDF output contains no pages")
    if expected_pages is not None and len(reader.pages) != expected_pages:
        raise WorkerError("invalid_output", "PDF output page count changed unexpectedly")


def execute(request_path: Path, result_path: Path) -> int:
    try:
        request = _load(request_path)
        request_path.unlink(missing_ok=True)
        operation = str(request.get("operation", ""))
        sources = _sources(request)
        max_pages = int(request.get("max_pages", 500))
        report: dict[str, Any]
        warnings: tuple[str, ...] = ()
        if operation == "pdf:info":
            report = _info(sources[0], max_pages)
            if _sha256(sources[0]) != request["sources"][0]["sha256"]:
                raise WorkerError("document_changed", "PDF source changed during inspection")
            _write(
                result_path,
                {
                    "protocol_version": _PROTOCOL_VERSION,
                    "ok": True,
                    "code": "ok",
                    "message": "PDF inspected",
                    "source_sha256": [item["sha256"] for item in request["sources"]],
                    "report": report,
                },
            )
            return 0
        output = result_path.parent / str(request.get("output_name", ""))
        if output.parent != result_path.parent or output.name != "output.pdf":
            raise WorkerError("invalid_request", "Invalid PDF worker output path")
        if operation == "pdf:merge":
            report = _merge(sources, output, max_pages)
        elif operation == "pdf:split":
            report = _split(
                sources[0],
                output,
                max_pages,
                int(request.get("start_page", 1)),
                int(request.get("end_page", 0)),
            )
        elif operation == "pdf:fill-form":
            values = request.get("fields")
            if not isinstance(values, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in values.items()
            ):
                raise WorkerError("invalid_request", "PDF form fields must map strings to strings")
            report, warnings = _fill_form(sources[0], output, max_pages, values)
        elif operation == "pdf:redact":
            values = request.get("terms")
            if not isinstance(values, list):
                raise WorkerError("invalid_request", "Invalid redaction terms")
            report = _redact(sources[0], output, max_pages, values)
        else:
            raise WorkerError("unsupported_operation", "Unsupported PDF operation")
        for path, record in zip(sources, request["sources"], strict=True):
            if _sha256(path) != record["sha256"]:
                raise WorkerError("document_changed", "A PDF source changed during mutation")
        if output.stat().st_size > int(request.get("max_output_bytes", 256 * 1024 * 1024)):
            output.unlink(missing_ok=True)
            raise WorkerError("document_limit_exceeded", "PDF output exceeds configured limit")
        _validate_output(output, int(report["page_count"]) if "page_count" in report else None)
        _write(
            result_path,
            {
                "protocol_version": _PROTOCOL_VERSION,
                "ok": True,
                "code": "ok",
                "message": "PDF operation completed",
                "renderer": str(request.get("renderer")),
                "renderer_version": str(request.get("renderer_version", "1")),
                "source_sha256": [item["sha256"] for item in request["sources"]],
                "output_name": output.name,
                "output_extension": "pdf",
                "output_mime_type": "application/pdf",
                "output_size": output.stat().st_size,
                "output_sha256": _sha256(output),
                "warnings": list(warnings),
                "report": report,
            },
        )
        return 0
    except WorkerError as exc:
        _write(
            result_path,
            {
                "protocol_version": _PROTOCOL_VERSION,
                "ok": False,
                "code": exc.code,
                "message": exc.safe_message,
            },
        )
        return 0
    except Exception as exc:
        _write(
            result_path,
            {
                "protocol_version": _PROTOCOL_VERSION,
                "ok": False,
                "code": "mutation_worker_failure",
                "message": f"PDF operation failed safely ({type(exc).__name__})",
            },
        )
        return 1


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    return execute(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
