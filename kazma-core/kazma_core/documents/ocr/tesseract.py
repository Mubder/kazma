"""Bounded direct Tesseract adapter with TSV geometry extraction."""

from __future__ import annotations

import csv
import io
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path

from ..errors import DocumentLimitError, DocumentOcrError, DocumentOcrUnavailableError
from ..models import BlockType, BoundingBox, DocumentBlock
from ..sandbox import SandboxRequest, run_isolated_subprocess
from .base import OcrCapability, OcrPageResult, OcrReadiness

__all__ = ["TesseractProvider", "select_language"]

_LANGUAGE_RE = re.compile(r"^[a-z0-9_-]+$")


def _normalized_languages(value: str | tuple[str, ...]) -> tuple[str, ...]:
    raw = value.split("+") if isinstance(value, str) else value
    result = tuple(dict.fromkeys(str(item).strip().lower() for item in raw if str(item).strip()))
    if not result or any(not _LANGUAGE_RE.fullmatch(item) for item in result):
        raise DocumentOcrUnavailableError("OCR language must use installed Tesseract codes")
    return result


def select_language(
    requested: str | None,
    *,
    configured: tuple[str, ...],
    installed: tuple[str, ...],
    native_text: str = "",
) -> str:
    """Select eng, ara, or eng+ara without claiming absent language data."""

    configured = _normalized_languages(configured)
    installed_set = set(installed)
    if requested and requested.strip().lower() not in {"auto", "default"}:
        selected = _normalized_languages(requested)
    else:
        has_arabic = any(
            "ARABIC" in unicodedata.name(char, "") for char in native_text
        )
        has_latin = any("LATIN" in unicodedata.name(char, "") for char in native_text)
        configured_set = set(configured)
        # Prefer ara *before* eng when both are configured. Tesseract's eng+ara
        # often misreads pure-Arabic pages as Latin gibberish; ara+eng keeps
        # Arabic readable while still OCR'ing English accurately.
        dual = tuple(item for item in ("ara", "eng") if item in configured_set)
        # Empty native text (scanned PDF / image-only): dual when available.
        if not native_text.strip():
            selected = dual if dual else configured
        elif has_arabic and has_latin and {"eng", "ara"} <= configured_set:
            selected = dual
        elif has_arabic and "ara" in configured_set:
            selected = ("ara",)
        elif has_latin and "eng" in configured_set:
            selected = ("eng",)
        else:
            selected = dual if dual else configured
    missing = [item for item in selected if item not in installed_set]
    if missing:
        raise DocumentOcrUnavailableError(
            "Tesseract language data is missing: "
            f"{', '.join(missing)}. Install the requested traineddata files."
        )
    return "+".join(selected)


def _direction(text: str) -> str:
    has_rtl = any(unicodedata.bidirectional(char) in {"R", "AL", "AN"} for char in text)
    has_ltr = any(unicodedata.bidirectional(char) == "L" for char in text)
    if has_rtl and has_ltr:
        return "mixed"
    return "rtl" if has_rtl else "ltr"


def _parse_tsv(
    payload: bytes,
    *,
    page_number: int,
    language: str,
    engine_version: str | None,
    dpi: int,
) -> OcrPageResult:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentOcrError("Tesseract returned invalid UTF-8 TSV output") from exc
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    required = {
        "level", "block_num", "par_num", "line_num", "left", "top",
        "width", "height", "conf", "text",
    }
    if reader.fieldnames is None or not required <= set(reader.fieldnames):
        raise DocumentOcrError("Tesseract returned malformed TSV output")
    lines: dict[tuple[int, int, int], list[tuple[str, int, int, int, int, float]]] = defaultdict(list)
    for row in reader:
        word = (row.get("text") or "").strip()
        if not word:
            continue
        try:
            confidence = float(row["conf"]) / 100.0
            if confidence < 0:
                continue
            left = int(row["left"])
            top = int(row["top"])
            width = int(row["width"])
            height = int(row["height"])
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise DocumentOcrError("Tesseract returned invalid TSV coordinates") from exc
        lines[key].append((word, left, top, width, height, min(confidence, 1.0)))

    blocks: list[DocumentBlock] = []
    all_confidences: list[float] = []
    for index, key in enumerate(sorted(lines), start=1):
        words = lines[key]
        line_text = " ".join(word[0] for word in words)
        x0 = min(word[1] for word in words)
        y0 = min(word[2] for word in words)
        x1 = max(word[1] + word[3] for word in words)
        y1 = max(word[2] + word[4] for word in words)
        confidence = sum(word[5] for word in words) / len(words)
        all_confidences.extend(word[5] for word in words)
        blocks.append(
            DocumentBlock(
                block_id=f"p{page_number}-ocr-{index}",
                block_type=BlockType.TEXT,
                text=line_text,
                bounding_box=BoundingBox(x0, y0, x1, y1),
                confidence=round(confidence, 6),
                metadata={
                    "ocr": True,
                    "language": language,
                    "engine": "tesseract",
                    "engine_version": engine_version,
                    "dpi": dpi,
                    "direction": _direction(line_text),
                    "coordinate_space": "image_pixels",
                },
            )
        )
    mean = sum(all_confidences) / len(all_confidences) if all_confidences else None
    return OcrPageResult(
        page_number=page_number,
        blocks=tuple(blocks),
        language=language,
        engine="tesseract",
        engine_version=engine_version,
        dpi=dpi,
        confidence=round(mean, 6) if mean is not None else None,
    )


class TesseractProvider:
    """Tesseract CLI provider. No pytesseract process indirection is used."""

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("tesseract")
        self._base_capability: OcrCapability | None = None

    def _run(
        self,
        arguments: tuple[str, ...],
        *,
        work_dir: Path,
        timeout: float,
        stdout_limit: int,
    ):
        if not self.executable:
            raise DocumentOcrUnavailableError(
                "Tesseract OCR is not installed or is not on PATH"
            )
        try:
            result = run_isolated_subprocess(
                SandboxRequest(
                    command=(self.executable, *arguments),
                    work_dir=work_dir,
                    timeout_seconds=timeout,
                    stdout_limit_bytes=stdout_limit,
                    stderr_limit_bytes=65_536,
                )
            )
        except OSError as exc:
            raise DocumentOcrUnavailableError("Tesseract could not be started") from exc
        if result.timed_out:
            raise DocumentOcrError("Tesseract OCR exceeded its time limit", code="ocr_timeout")
        if result.output_limit_exceeded:
            raise DocumentLimitError("Tesseract OCR output exceeds the configured limit")
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()[:500]
            raise DocumentOcrError(
                f"Tesseract OCR failed{': ' + detail if detail else ''}"
            )
        return result

    def probe(self, requested_languages: tuple[str, ...] = ()) -> OcrCapability:
        if self._base_capability is not None:
            base = self._base_capability
            missing = sorted(set(requested_languages) - set(base.languages))
            if missing and base.available:
                return OcrCapability(
                    base.provider,
                    base.engine,
                    base.engine_version,
                    base.languages,
                    base.features,
                    OcrReadiness.UNAVAILABLE,
                    f"Tesseract language data is missing: {', '.join(missing)}",
                )
            return base
        if not self.executable:
            self._base_capability = OcrCapability(
                "tesseract-cli",
                "tesseract",
                None,
                (),
                ("text", "tsv", "bounding_boxes", "confidence"),
                OcrReadiness.UNAVAILABLE,
                "Tesseract binary was not found on PATH",
            )
            return self._base_capability
        try:
            version_result = self._run(
                ("--version",), work_dir=Path.cwd(), timeout=5, stdout_limit=32_768
            )
            version_line = (
                version_result.stdout or version_result.stderr
            ).decode("utf-8", errors="replace").splitlines()[0][:200]
            language_result = self._run(
                ("--list-langs",), work_dir=Path.cwd(), timeout=5, stdout_limit=65_536
            )
            lines = language_result.stdout.decode("utf-8", errors="replace").splitlines()
            languages = tuple(
                sorted(
                    line.strip()
                    for line in lines
                    if line.strip() and not line.lower().startswith("list of available")
                )
            )
        except Exception as exc:
            reason = (
                exc.safe_message
                if isinstance(exc, (DocumentOcrError, DocumentOcrUnavailableError))
                else f"Tesseract health probe failed ({type(exc).__name__})"
            )
            self._base_capability = OcrCapability(
                "tesseract-cli",
                "tesseract",
                None,
                (),
                ("text", "tsv", "bounding_boxes", "confidence"),
                OcrReadiness.UNAVAILABLE,
                reason,
            )
            return self._base_capability
        missing = sorted(set(requested_languages) - set(languages))
        reason = (
            f"Tesseract language data is missing: {', '.join(missing)}"
            if missing
            else None
        )
        capability = OcrCapability(
            "tesseract-cli",
            "tesseract",
            version_line,
            languages,
            ("text", "tsv", "bounding_boxes", "confidence", "unicode"),
            OcrReadiness.UNAVAILABLE if missing else OcrReadiness.READY,
            reason,
        )
        if not requested_languages:
            self._base_capability = capability
        else:
            self._base_capability = OcrCapability(
                capability.provider,
                capability.engine,
                capability.engine_version,
                capability.languages,
                capability.features,
                OcrReadiness.READY,
                None,
            )
        return capability

    def recognize(
        self,
        image_path: Path,
        *,
        page_number: int,
        language: str,
        dpi: int,
        max_pixels: int,
        timeout_seconds: float,
        output_limit_bytes: int,
        work_dir: Path,
    ) -> OcrPageResult:
        try:
            from PIL import Image
        except ImportError as exc:
            raise DocumentOcrUnavailableError(
                "Pillow is required to validate OCR images"
            ) from exc
        with Image.open(image_path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise DocumentLimitError("OCR image exceeds the configured pixel limit")
        capability = self.probe(tuple(language.split("+")))
        if not capability.available:
            raise DocumentOcrUnavailableError(
                capability.reason or "Tesseract OCR is unavailable"
            )
        result = self._run(
            (
                str(image_path),
                "stdout",
                "-l",
                language,
                "--dpi",
                str(dpi),
                "tsv",
            ),
            work_dir=work_dir,
            timeout=timeout_seconds,
            stdout_limit=output_limit_bytes,
        )
        return _parse_tsv(
            result.stdout,
            page_number=page_number,
            language=language,
            engine_version=capability.engine_version,
            dpi=dpi,
        )
