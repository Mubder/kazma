"""Multilingual OCR providers, readiness, rasterization, and routing."""

from __future__ import annotations

from .base import (
    OcrCapability,
    OcrComponentHealth,
    OcrEngineReport,
    OcrHealth,
    OcrPageResult,
    OcrProvider,
    OcrReadiness,
)
from .neural import NeuralOcrProvider
from .pipeline import apply_ocr, get_ocr_health, merge_page_content, select_ocr_provider
from .tesseract import TesseractProvider, select_language

__all__ = [
    "NeuralOcrProvider",
    "OcrCapability",
    "OcrComponentHealth",
    "OcrEngineReport",
    "OcrHealth",
    "OcrPageResult",
    "OcrProvider",
    "OcrReadiness",
    "TesseractProvider",
    "apply_ocr",
    "get_ocr_health",
    "merge_page_content",
    "select_language",
    "select_ocr_provider",
]
