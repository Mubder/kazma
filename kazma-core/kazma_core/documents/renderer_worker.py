"""JSON-file protocol worker for document generation and conversion."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .resources import validate_restricted_render_resources

_PROTOCOL_VERSION = 1
_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "html": "text/html",
    "markdown": "text/markdown",
    "md": "text/markdown",
}


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


def _request(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("protocol_version") != _PROTOCOL_VERSION:
        raise WorkerError("invalid_request", "Invalid renderer worker request")
    return value


def _verify_assets(request: dict[str, Any], work_dir: Path) -> None:
    records = request.get("approved_assets", [])
    if not isinstance(records, list):
        raise WorkerError("invalid_request", "Invalid approved render assets")
    expected: set[str] = set()
    assets = work_dir / "assets"
    for record in records:
        if not isinstance(record, dict) or set(record) != {"name", "sha256"}:
            raise WorkerError("invalid_request", "Invalid approved render asset record")
        name = str(record["name"])
        if Path(name).name != name or name in expected:
            raise WorkerError("invalid_request", "Invalid approved render asset name")
        path = assets / name
        if not path.is_file() or _sha256(path) != record["sha256"]:
            raise WorkerError("document_changed", "An approved render asset changed")
        expected.add(name)
    actual = {path.name for path in assets.iterdir() if path.is_file()} if assets.is_dir() else set()
    if actual != expected:
        raise WorkerError("invalid_request", "Unexpected files in render asset directory")


def _validate_payload_limits(request: dict[str, Any]) -> None:
    payload = request.get("payload")
    limits = request.get("limits")
    if not isinstance(payload, dict) or not isinstance(limits, dict):
        raise WorkerError("invalid_request", "Invalid renderer payload limits")
    sheets = payload.get("sheets", [])
    if isinstance(sheets, list):
        if len(sheets) > int(limits["max_sheets"]):
            raise WorkerError("document_limit_exceeded", "Workbook exceeds sheet limit")
        cells = 0
        for sheet in sheets:
            rows = sheet.get("rows", []) if isinstance(sheet, dict) else []
            if isinstance(rows, list) and len(rows) > int(limits["max_rows_per_sheet"]):
                raise WorkerError("document_limit_exceeded", "Workbook exceeds row limit")
            if isinstance(rows, list):
                cells += sum(len(row) for row in rows if isinstance(row, list))
        if cells > int(limits["max_cells"]):
            raise WorkerError("document_limit_exceeded", "Workbook exceeds cell limit")
    slides = payload.get("slides", [])
    if isinstance(slides, list) and len(slides) + 1 > int(limits["max_slides"]):
        raise WorkerError("document_limit_exceeded", "Presentation exceeds slide limit")
    images = payload.get("images", [])
    if isinstance(images, list) and len(images) > int(limits["max_images"]):
        raise WorkerError("document_limit_exceeded", "Document exceeds image limit")


def _sections(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(
                {"heading": str(item.get("heading", "")), "body": str(item.get("body", ""))}
            )
    return result


def _build_model_and_profile(
    payload: dict[str, Any],
) -> tuple["ContentModel", "DocProfile"]:
    """Build a format-agnostic ContentModel + DocProfile from a render payload.

    This is the single payload→content translation shared by EVERY format
    engine (DOCX, PDF, …). Both generators call it, so a generated DOCX and a
    generated PDF are projections of the *same* model under the *same* profile
    — one design, regardless of extension.

    Direction is auto-detected from the title + section sample (honouring
    explicit ``lang`` / ``rtl`` overrides). Blocks are added in document order;
    engines interpret each block type in their format-native way.
    """
    from kazma_core.documents.content_model import (
        BodyBlock,
        CitationBlock,
        ContentModel,
        HeadingBlock,
        ImageBlock,
        TableBlock,
        TitleBlock,
        TOCBlock,
    )
    from kazma_core.documents.profile import DocProfile

    sections = _sections(payload.get("sections"))
    sample_parts = [str(payload.get("title", ""))]
    for item in sections:
        sample_parts.append(item.get("heading", ""))
        sample_parts.append((item.get("body") or "")[:1500])
    sample = "\n".join(sample_parts)
    profile = DocProfile.for_content(
        sample,
        language=payload.get("lang") or payload.get("language"),
        rtl=payload.get("rtl"),
    )

    fill_title = str(profile.theme["accent"]).lstrip("#")
    fill_h = str(profile.theme["heading_fill"]).lstrip("#")

    model = ContentModel()
    model.header = str(payload.get("header") or profile.chrome["brand"])
    model.footer = str(payload.get("footer") or profile.chrome["brand"])
    model.page_numbers = bool(payload.get("page_numbers", True))
    model.images_present = bool(payload.get("images"))
    # Document metadata → file core properties.
    model.author = str(payload.get("author") or "Kazma")
    model.subject = str(payload.get("subject") or "")
    model.keywords = str(payload.get("keywords") or "")

    # Title (+ optional subtitle).
    model.add(TitleBlock(text=str(payload.get("title", "Document")),
                         level=0, fill=fill_title))
    if payload.get("subtitle"):
        model.add(TitleBlock(text=str(payload["subtitle"]), level=3, fill=fill_h))

    # Optional table of contents.
    if payload.get("toc"):
        model.add(TOCBlock(
            entries=[it["heading"] for it in sections if it.get("heading")]
        ))

    # Sections: heading bar + rich Markdown body.
    for item in sections:
        if item.get("heading"):
            model.add(HeadingBlock(text=item["heading"].lstrip("#").strip(),
                                   level=1, fill=fill_h))
        body = item.get("body") or ""
        if body.strip():
            model.add(BodyBlock(text=body))

    # Structured tables (engine emits the optional heading bar).
    tables = payload.get("tables")
    if isinstance(tables, list):
        for value in tables:
            if not isinstance(value, dict):
                continue
            headers = value.get("headers")
            rows = value.get("rows")
            if isinstance(headers, list) and isinstance(rows, list) and headers:
                model.add(TableBlock(
                    headers=[str(c) for c in headers],
                    rows=[[str(c) for c in row]
                          for row in rows if isinstance(row, list)],
                    heading=(str(value.get("heading", "")) or None),
                ))

    # Images (approved-asset references; engines embed only validated files).
    images = payload.get("images")
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict) and img.get("name"):
                try:
                    width = float(img.get("width_in", img.get("width", 5.0)) or 5.0)
                except (TypeError, ValueError):
                    width = 5.0
                model.add(ImageBlock(
                    name=str(img["name"]),
                    caption=str(img.get("caption", "")),
                    width_in=max(1.0, min(7.0, width)),
                ))

    # Citations / references.
    citations = payload.get("citations")
    if isinstance(citations, list) and citations:
        model.add(CitationBlock(items=[str(c) for c in citations]))

    return model, profile


def _markdown(payload: dict[str, Any]) -> str:
    """Build a Markdown document from a payload.

    The Contents / References labels come from the localized chrome via a
    :class:`DocProfile` (auto-detected direction), so an Arabic payload gets
    ``## المحتويات`` / ``## المراجع`` matching the DOCX/PDF/HTML output.
    """
    from kazma_core.documents.profile import DocProfile

    sample_parts = [str(payload.get("title", ""))]
    for section in _sections(payload.get("sections")):
        sample_parts.append(section.get("heading", ""))
    chrome = DocProfile.for_content(
        "\n".join(sample_parts),
        language=payload.get("lang") or payload.get("language"),
        rtl=payload.get("rtl"),
    ).chrome

    lines = [f"# {payload.get('title', 'Document')}", ""]
    if payload.get("toc"):
        lines.extend((f"## {chrome['toc']}", ""))
        for section in _sections(payload.get("sections")):
            heading = section["heading"].lstrip("#").strip()
            if heading:
                anchor = re.sub(r"[^\w\u0600-\u06ff -]", "", heading.lower()).replace(" ", "-")
                lines.append(f"- [{heading}](#{anchor})")
        lines.append("")
    for section in _sections(payload.get("sections")):
        if section["heading"]:
            lines.extend((f"## {section['heading'].lstrip('#').strip()}", ""))
        if section["body"]:
            lines.extend((section["body"], ""))
    images = payload.get("images")
    if isinstance(images, list):
        for img in images:
            if isinstance(img, dict) and img.get("name"):
                alt = str(img.get("caption") or img["name"])
                lines.append(f'![{alt}]({img["name"]})')
        if any(isinstance(i, dict) and i.get("name") for i in images):
            lines.append("")
    citations = payload.get("citations")
    if isinstance(citations, list) and citations:
        lines.extend((f"## {chrome['references']}", ""))
        lines.extend(f"{index}. {item}" for index, item in enumerate(citations, 1))
    return "\n".join(lines).rstrip() + "\n"


def _markdown_html(text: str) -> str:
    """Themed, direction-aware HTML wrap of a Markdown string.

    Direction + theme + chrome come from a :class:`DocProfile` built from the
    text, so the convert paths (``convert:markdown:html``, WeasyPrint PDF) share
    one design with the generate paths. Used for raw-Markdown sources that have
    no generation payload.
    """
    from kazma_core.documents.engines.html import HtmlEngine
    from kazma_core.documents.profile import DocProfile

    profile = DocProfile.for_content(text)
    return HtmlEngine(profile).render_markdown(text)


def _generate_html(output: Path, payload: dict[str, Any], *,
                   assets_dir: Path | None = None) -> None:
    """Generate HTML via the unified document layer (ContentModel + DocProfile)."""
    from kazma_core.documents.engines.html import HtmlEngine

    model, profile = _build_model_and_profile(payload)
    html = HtmlEngine(profile).render(model, assets_dir=assets_dir)
    output.write_text(html, encoding="utf-8")


def _safe_html(text: str) -> None:
    try:
        validate_restricted_render_resources(text)
    except Exception as exc:
        raise WorkerError(
            "external_resource_denied",
            "External or unapproved local resources are forbidden during rendering",
        ) from exc


def _font_paths() -> tuple[Path | None, Path | None]:
    """Prefer fonts with solid Arabic coverage (glyphs + metrics)."""
    candidates = (
        # Windows — Calibri first for exact user preference
        (Path("C:/Windows/Fonts/calibri.ttf"), Path("C:/Windows/Fonts/calibrib.ttf")),
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (Path("C:/Windows/Fonts/tahoma.ttf"), Path("C:/Windows/Fonts/tahomabd.ttf")),
        (Path("C:/Windows/Fonts/trado.ttf"), Path("C:/Windows/Fonts/trado.ttf")),
        (
            Path("C:/Windows/Fonts/NotoSansArabic-Regular.ttf"),
            Path("C:/Windows/Fonts/NotoSansArabic-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ),
    )
    return next(((regular, bold) for regular, bold in candidates if regular.is_file()), (None, None))


def _generate_pdf(output: Path, payload: dict[str, Any], warnings: list[str],
                  *, assets_dir: Path | None = None) -> None:
    """Generate a PDF via the unified document layer.

    Builds a format-agnostic :class:`ContentModel` + :class:`DocProfile` (shared
    with the DOCX path) and hands them to :class:`PdfEngine`, which owns all
    ReportLab emission + Arabic shaping + direction semantics. Direction and
    alignment come from the profile policy, so the PDF shares one design
    language with the DOCX output.
    """
    from kazma_core.documents.engines.pdf import PdfEngine

    model, profile = _build_model_and_profile(payload)
    PdfEngine(profile, warnings).render(model, output, assets_dir=assets_dir)


def _generate_docx(output: Path, payload: dict[str, Any], *,
                   assets_dir: Path | None = None) -> None:
    """Generate a DOCX via the unified document layer (shared model + profile)."""
    from kazma_core.documents.engines.docx import DocxEngine

    model, profile = _build_model_and_profile(payload)
    DocxEngine(profile).render(model, output, assets_dir=assets_dir)


def _generate_xlsx(output: Path, payload: dict[str, Any]) -> None:
    """Generate XLSX via the unified document layer.

    Spreadsheets keep their own sheets/rows payload but pull theme + direction
    (RTL sheet view) from a :class:`DocProfile`, so an Arabic workbook matches
    the Arabic DOCX/PDF/HTML in design.
    """
    from kazma_core.documents.engines.xlsx import XlsxEngine
    from kazma_core.documents.profile import DocProfile

    sheets = payload.get("sheets") if isinstance(payload.get("sheets"), list) else []
    sample_parts = [str(payload.get("title", ""))]
    for s in sheets:
        if isinstance(s, dict):
            sample_parts.append(str(s.get("name", "")))
            for row in (s.get("rows") or [])[:6]:
                if isinstance(row, list):
                    sample_parts.append(" ".join(str(c) for c in row))
    profile = DocProfile.for_content(
        "\n".join(sample_parts),
        language=payload.get("lang") or payload.get("language"),
        rtl=payload.get("rtl"),
    )
    XlsxEngine(profile).render(payload, output)


def _generate_pptx(output: Path, payload: dict[str, Any]) -> None:
    """Generate PPTX via the unified document layer.

    Slides keep their own slides/bullets payload but pull theme + direction
    (RTL paragraphs) from a :class:`DocProfile`, so an Arabic deck matches the
    Arabic DOCX/PDF/HTML/XLSX in design.
    """
    from kazma_core.documents.engines.pptx import PptxEngine
    from kazma_core.documents.profile import DocProfile

    slides = payload.get("slides") if isinstance(payload.get("slides"), list) else []
    sample_parts = [str(payload.get("title", "")), str(payload.get("subtitle", ""))]
    for s in slides:
        if isinstance(s, dict):
            sample_parts.append(str(s.get("heading", "")))
            sample_parts.append(str(s.get("body", "")))
            bullets = s.get("bullets")
            if isinstance(bullets, list):
                sample_parts.extend(str(b) for b in bullets)
    profile = DocProfile.for_content(
        "\n".join(sample_parts),
        language=payload.get("lang") or payload.get("language"),
        rtl=payload.get("rtl"),
    )
    PptxEngine(profile).render(payload, output)


def _weasy_pdf(output: Path, source: str, assets: Path) -> None:
    from weasyprint import HTML, default_url_fetcher

    try:
        validate_restricted_render_resources(
            source,
            approved_asset_names=frozenset(
                path.name for path in assets.iterdir() if path.is_file()
            ),
        )
    except Exception as exc:
        raise WorkerError(
            "external_resource_denied",
            "External or unapproved local resources are forbidden during rendering",
        ) from exc
    assets_root = assets.resolve()

    def fetch(url: str, *args: Any, **kwargs: Any) -> Any:
        if url.startswith("data:"):
            return default_url_fetcher(url, *args, **kwargs)
        if url.startswith("file:"):
            candidate = Path(url[5:]).resolve()
            if candidate.is_relative_to(assets_root) and candidate.is_file():
                return default_url_fetcher(url, *args, **kwargs)
        raise WorkerError("external_resource_denied", "External rendering fetch was denied")

    HTML(string=source, base_url=assets.as_uri() + "/", url_fetcher=fetch).write_pdf(output)


def _extract_docx_text(source: Path) -> tuple[str, list[str]]:
    """Pull paragraph + table text from a DOCX for lossy PDF conversion."""

    try:
        from docx import Document
    except ImportError as exc:
        raise WorkerError(
            "renderer_unavailable",
            "python-docx is required for DOCX→PDF text fallback (install python-docx)",
        ) from exc

    warnings: list[str] = []
    document = Document(str(source))
    chunks: list[str] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if text:
            chunks.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            line = " | ".join(cell for cell in cells if cell)
            if line:
                chunks.append(line)
    if not chunks:
        warnings.append("DOCX had no extractable text; emitted a titled blank PDF")
        return "", warnings
    return "\n\n".join(chunks), warnings


def _office_text_to_pdf(
    output: Path,
    *,
    title: str,
    body: str,
    warnings: list[str],
) -> None:
    """Render plain extracted text to PDF via the reportlab generator."""

    _generate_pdf(
        output,
        {
            "title": title or "Document",
            "sections": [{"heading": "", "body": body or "(empty document)"}],
            "page_numbers": True,
        },
        warnings,
    )
    warnings.append(
        "Converted via text extraction (reportlab-office); layout, images, and "
        "styles are not preserved. Install LibreOffice (soffice) for high-fidelity "
        "Office→PDF conversion."
    )


def _libreoffice(request: dict[str, Any], source: Path, output: Path) -> None:
    from kazma_core.documents.binaries import find_soffice, run_soffice_cli

    if not find_soffice():
        raise WorkerError(
            "renderer_unavailable",
            "Healthy headless LibreOffice is unavailable (soffice not found on PATH "
            "or under Program Files\\LibreOffice)",
        )
    profile = output.parent / "lo-profile"
    profile.mkdir()
    target = output.suffix.lstrip(".")
    env = {
        key: os.environ[key]
        for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
        if key in os.environ
    }
    try:
        result = run_soffice_cli(
            (
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--norestore",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                target,
                "--outdir",
                str(output.parent),
                str(source),
            ),
            timeout=float(request.get("library_timeout_seconds", 120)),
            cwd=output.parent,
            env=env,
        )
    except FileNotFoundError as exc:
        raise WorkerError(
            "renderer_unavailable",
            "Healthy headless LibreOffice is unavailable",
        ) from exc
    except Exception as exc:
        raise WorkerError(
            "conversion_failed",
            f"Headless LibreOffice conversion failed ({type(exc).__name__})",
        ) from exc
    produced = output.parent / f"{source.stem}.{target}"
    if result.returncode or not produced.is_file():
        raise WorkerError("conversion_failed", "Headless LibreOffice conversion failed")
    if produced != output:
        os.replace(produced, output)


def _render(request: dict[str, Any], output: Path) -> tuple[str, str, list[str]]:
    operation = str(request.get("operation", ""))
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise WorkerError("invalid_request", "Renderer payload must be an object")
    warnings: list[str] = []
    template = payload.get("_template")
    if template not in (None, "default", "report", "compact"):
        raise WorkerError("unsupported_template", "Requested document template is unavailable")
    renderer = str(request.get("renderer", ""))
    # Approved render assets live next to the output under assets/ (validated by
    # _verify_assets, sha256-matched). Engines embed images from here by name.
    assets_dir: Path | None = None
    if request.get("approved_assets"):
        _candidate = output.parent / "assets"
        if _candidate.is_dir():
            assets_dir = _candidate
    source: Path | None = None
    if request.get("source_path") is not None:
        source = Path(str(request["source_path"])).resolve(strict=True)
        if _sha256(source) != request.get("source_sha256"):
            raise WorkerError("document_changed", "Source changed before rendering")
    if operation.startswith("generate:"):
        target = operation.split(":", 1)[1]
        if target == "markdown":
            output.write_text(_markdown(payload), encoding="utf-8")
        elif target == "html":
            _generate_html(output, payload, assets_dir=assets_dir)
        elif target == "pdf":
            _generate_pdf(output, payload, warnings, assets_dir=assets_dir)
        elif target == "docx":
            _generate_docx(output, payload, assets_dir=assets_dir)
        elif target == "xlsx":
            _generate_xlsx(output, payload)
        elif target == "pptx":
            _generate_pptx(output, payload)
        else:
            raise WorkerError("unsupported_operation", "Unsupported generation format")
    elif operation == "convert:markdown:html":
        assert source is not None
        text = source.read_text(encoding="utf-8")
        _safe_html(text)
        output.write_text(_markdown_html(text), encoding="utf-8")
    elif operation == "convert:markdown:docx":
        assert source is not None
        _safe_html(source.read_text(encoding="utf-8"))
        _generate_docx(
            output,
            {
                "title": source.stem,
                "sections": [{"heading": "", "body": source.read_text(encoding="utf-8")}],
            },
        )
    elif operation in {"convert:markdown:pdf", "convert:html:pdf"} and renderer != "reportlab-office":
        assert source is not None
        text = source.read_text(encoding="utf-8")
        if operation.startswith("convert:markdown"):
            text = _markdown_html(text)
        _weasy_pdf(output, text, output.parent / "assets")
    elif renderer == "reportlab-office":
        assert source is not None
        if operation == "convert:docx:pdf":
            body, extract_warnings = _extract_docx_text(source)
            warnings.extend(extract_warnings)
            _office_text_to_pdf(output, title=source.stem, body=body, warnings=warnings)
        elif operation in {
            "convert:markdown:pdf",
            "convert:md:pdf",
            "convert:txt:pdf",
            "convert:text:pdf",
        }:
            try:
                body = source.read_text(encoding="utf-8")
            except UnicodeError as exc:
                raise WorkerError(
                    "invalid_document_encoding",
                    "Text conversion source must be valid UTF-8",
                ) from exc
            _office_text_to_pdf(output, title=source.stem, body=body, warnings=warnings)
        else:
            raise WorkerError(
                "unsupported_operation",
                f"reportlab-office cannot handle {operation}",
            )
    elif renderer == "libreoffice":
        assert source is not None
        _libreoffice(request, source, output)
    else:
        raise WorkerError("unsupported_operation", "Unsupported document conversion")
    return renderer, str(request.get("renderer_version", "1")), warnings


def execute(request_path: Path, result_path: Path) -> int:
    try:
        request = _request(request_path)
        request_path.unlink(missing_ok=True)
        _verify_assets(request, result_path.parent)
        _validate_payload_limits(request)
        output = result_path.parent / str(request.get("output_name", ""))
        if output.parent != result_path.parent or not output.name.startswith("output."):
            raise WorkerError("invalid_request", "Invalid worker output path")
        renderer, version, warnings = _render(request, output)
        if request.get("source_path") is not None:
            source = Path(str(request["source_path"])).resolve(strict=True)
            if _sha256(source) != request.get("source_sha256"):
                raise WorkerError("document_changed", "Source changed during rendering")
        _verify_assets(request, result_path.parent)
        if not output.is_file() or output.stat().st_size <= 0:
            raise WorkerError("empty_output", "Renderer produced no document")
        if output.stat().st_size > int(request.get("max_output_bytes", 256 * 1024 * 1024)):
            output.unlink(missing_ok=True)
            raise WorkerError("document_limit_exceeded", "Rendered document exceeds output limit")
        extension = output.suffix.lower().lstrip(".")
        _write(
            result_path,
            {
                "protocol_version": _PROTOCOL_VERSION,
                "ok": True,
                "code": "ok",
                "message": "Document rendered",
                "renderer": renderer,
                "renderer_version": version,
                "source_sha256": request.get("source_sha256"),
                "output_name": output.name,
                "output_extension": extension,
                "output_mime_type": _MIME[extension],
                "output_size": output.stat().st_size,
                "output_sha256": _sha256(output),
                "warnings": warnings,
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
                "code": "renderer_worker_failure",
                "message": f"Document rendering failed safely ({type(exc).__name__})",
            },
        )
        return 1


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    return execute(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    raise SystemExit(main())
