"""Multilingual OCR providers, readiness, rasterization, and routing."""

from __future__ import annotations

from .base import (
    OcrCapability,
    OcrComponentHealth,
    OcrHealth,
    OcrPageResult,
    OcrProvider,
    OcrReadiness,
)
from .pipeline import apply_ocr, get_ocr_health, merge_page_content
from .tesseract import TesseractProvider, select_language

__all__ = [
    "OcrCapability",
    "OcrComponentHealth",
    "OcrHealth",
    "OcrPageResult",
    "OcrProvider",
    "OcrReadiness",
    "TesseractProvider",
    "apply_ocr",
    "get_ocr_health",
    "merge_page_content",
    "select_language",
]
