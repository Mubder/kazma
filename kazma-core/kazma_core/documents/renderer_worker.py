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


def _markdown(payload: dict[str, Any]) -> str:
    lines = [f"# {payload.get('title', 'Document')}", ""]
    if payload.get("toc"):
        lines.extend(("## Contents", ""))
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
    citations = payload.get("citations")
    if isinstance(citations, list) and citations:
        lines.extend(("## References", ""))
        lines.extend(f"{index}. {item}" for index, item in enumerate(citations, 1))
    return "\n".join(lines).rstrip() + "\n"


def _markdown_html(text: str) -> str:
    try:
        import markdown

        body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc"])
    except ImportError:
        body = "<pre>" + html.escape(text) + "</pre>"
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>body{font-family:sans-serif;unicode-bidi:plaintext}"
        "[dir=rtl]{direction:rtl} table{border-collapse:collapse}</style></head>"
        f"<body>{body}</body></html>"
    )


def _safe_html(text: str) -> None:
    try:
        validate_restricted_render_resources(text)
    except Exception as exc:
        raise WorkerError(
            "external_resource_denied",
            "External or unapproved local resources are forbidden during rendering",
        ) from exc


def _font_paths() -> tuple[Path | None, Path | None]:
    candidates = (
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"),
        ),
    )
    return next(((regular, bold) for regular, bold in candidates if regular.is_file()), (None, None))


def _generate_pdf(output: Path, payload: dict[str, Any], warnings: list[str]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    regular, bold = _font_paths()
    font = "Helvetica"
    bold_font = "Helvetica-Bold"
    if regular:
        pdfmetrics.registerFont(TTFont("KazmaUnicode", str(regular)))
        font = "KazmaUnicode"
        if bold and bold.is_file():
            pdfmetrics.registerFont(TTFont("KazmaUnicodeBold", str(bold)))
            bold_font = "KazmaUnicodeBold"
        else:
            bold_font = font
    else:
        warnings.append("Unicode font unavailable; PDF uses a limited deterministic fallback")
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}

    def size(name: str, default: float, low: float, high: float) -> float:
        try:
            return min(high, max(low, float(style.get(name, default))))
        except (TypeError, ValueError):
            warnings.append(f"Invalid {name} style token; deterministic default applied")
            return default

    title_size = size("title_font_size", 19, 10, 36)
    heading_size = size("heading_font_size", 14, 8, 28)
    body_size = size("body_font_size", 10.5, 6, 18)
    title_style = ParagraphStyle(
        "KazmaTitle",
        fontName=bold_font,
        fontSize=title_size,
        leading=title_size * 1.3,
        spaceAfter=16,
    )
    heading_style = ParagraphStyle(
        "KazmaHeading",
        fontName=bold_font,
        fontSize=heading_size,
        leading=heading_size * 1.4,
        spaceBefore=10,
    )
    body_style = ParagraphStyle(
        "KazmaBody",
        fontName=font,
        fontSize=body_size,
        leading=body_size * 1.5,
        wordWrap="RTL",
    )
    header = str(payload.get("header", ""))
    footer = str(payload.get("footer", ""))
    page_numbers = bool(payload.get("page_numbers", True))

    def decorate(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont(font, 8)
        if header:
            canvas.drawString(document.leftMargin, A4[1] - 24, header)
        if footer:
            canvas.drawString(document.leftMargin, 20, footer)
        if page_numbers:
            canvas.drawRightString(A4[0] - document.rightMargin, 20, str(document.page))
        canvas.restoreState()

    story: list[Any] = [
        Paragraph(html.escape(str(payload.get("title", "Document"))), title_style)
    ]
    sections = _sections(payload.get("sections"))
    if payload.get("toc"):
        story.append(Paragraph("Contents", heading_style))
        story.extend(
            Paragraph(f"{index}. {html.escape(item['heading'])}", body_style)
            for index, item in enumerate(sections, 1)
            if item["heading"]
        )
        story.append(PageBreak())
    for item in sections:
        if item["heading"]:
            story.append(Paragraph(html.escape(item["heading"]), heading_style))
        for paragraph in item["body"].split("\n\n"):
            if paragraph.strip():
                story.extend(
                    (Paragraph(html.escape(paragraph).replace("\n", "<br/>"), body_style), Spacer(1, 6))
                )
    tables = payload.get("tables")
    if isinstance(tables, list):
        for value in tables:
            if not isinstance(value, dict):
                continue
            heading = str(value.get("heading", ""))
            headers = value.get("headers")
            rows = value.get("rows")
            if heading:
                story.append(Paragraph(html.escape(heading), heading_style))
            if isinstance(headers, list) and isinstance(rows, list) and headers:
                data = [
                    [str(cell) for cell in headers],
                    *(
                        [str(cell) for cell in row]
                        for row in rows
                        if isinstance(row, list)
                    ),
                ]
                table = Table(data, repeatRows=1)
                table.setStyle(
                    TableStyle(
                        (
                            ("FONTNAME", (0, 0), (-1, -1), font),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        )
                    )
                )
                story.extend((table, Spacer(1, 8)))
    citations = payload.get("citations")
    if isinstance(citations, list) and citations:
        story.append(Paragraph("References", heading_style))
        story.extend(
            Paragraph(f"{index}. {html.escape(str(item))}", body_style)
            for index, item in enumerate(citations, 1)
        )
    if payload.get("images"):
        warnings.append(
            "Images were omitted because generation accepts no unapproved filesystem resources"
        )
    SimpleDocTemplate(str(output), pagesize=A4).build(
        story, onFirstPage=decorate, onLaterPages=decorate
    )


def _generate_docx(output: Path, payload: dict[str, Any]) -> None:
    from docx import Document

    document = Document()
    document.add_heading(str(payload.get("title", "Document")), 0)
    header = str(payload.get("header", ""))
    footer = str(payload.get("footer", ""))
    for section in document.sections:
        if header:
            section.header.paragraphs[0].text = header
        if footer:
            section.footer.paragraphs[0].text = footer
    if payload.get("toc"):
        document.add_heading("Contents", 1)
        for index, item in enumerate(_sections(payload.get("sections")), 1):
            if item["heading"]:
                document.add_paragraph(f"{index}. {item['heading']}")
    for item in _sections(payload.get("sections")):
        if item["heading"]:
            document.add_heading(item["heading"].lstrip("#").strip(), 1)
        for paragraph in item["body"].split("\n\n"):
            if paragraph.strip():
                document.add_paragraph(paragraph.strip())
    citations = payload.get("citations")
    if isinstance(citations, list) and citations:
        document.add_heading("References", 1)
        for value in citations:
            document.add_paragraph(str(value), style="List Number")
    document.save(output)


def _generate_xlsx(output: Path, payload: dict[str, Any]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    workbook.remove(workbook.active)
    sheets = payload.get("sheets")
    if not isinstance(sheets, list):
        sheets = []
    for index, value in enumerate(sheets, 1):
        if not isinstance(value, dict):
            continue
        name = str(value.get("name", f"Sheet{index}"))[:31]
        sheet = workbook.create_sheet(name or f"Sheet{index}")
        rows = value.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, list):
                sheet.append([item if item is not None else "" for item in row])
        for cell in sheet[1] if sheet.max_row else ():
            cell.font = Font(bold=True)
    if not workbook.sheetnames:
        workbook.create_sheet("Sheet")
    workbook.save(output)


def _generate_pptx(output: Path, payload: dict[str, Any]) -> None:
    from pptx import Presentation

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    slide.shapes.title.text = str(payload.get("title", "Presentation"))
    for value in payload.get("slides", []) if isinstance(payload.get("slides"), list) else []:
        if not isinstance(value, dict):
            continue
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = str(value.get("heading", ""))
        frame = slide.placeholders[1].text_frame
        bullets = value.get("bullets")
        lines = bullets if isinstance(bullets, list) else str(value.get("body", "")).splitlines()
        for index, line in enumerate(lines):
            if index == 0:
                frame.text = str(line)
            else:
                frame.add_paragraph().text = str(line)
    presentation.save(output)


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


def _libreoffice(request: dict[str, Any], source: Path, output: Path) -> None:
    executable = shutil.which("soffice")
    if not executable:
        raise WorkerError("renderer_unavailable", "Healthy headless LibreOffice is unavailable")
    profile = output.parent / "lo-profile"
    profile.mkdir()
    target = output.suffix.lstrip(".")
    result = subprocess.run(
        [
            executable,
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
        ],
        cwd=output.parent,
        env={
            key: os.environ[key]
            for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        },
        capture_output=True,
        check=False,
        timeout=int(request.get("library_timeout_seconds", 120)),
        shell=False,
    )
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
            output.write_text(_markdown_html(_markdown(payload)), encoding="utf-8")
        elif target == "pdf":
            _generate_pdf(output, payload, warnings)
        elif target == "docx":
            _generate_docx(output, payload)
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
    elif operation in {"convert:markdown:pdf", "convert:html:pdf"}:
        assert source is not None
        text = source.read_text(encoding="utf-8")
        if operation.startswith("convert:markdown"):
            text = _markdown_html(text)
        _weasy_pdf(output, text, output.parent / "assets")
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
