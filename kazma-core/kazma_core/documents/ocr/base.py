"""Typed OCR provider, capability, readiness, and result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from ..models import DocumentBlock

__all__ = [
    "OcrCapability",
    "OcrComponentHealth",
    "OcrHealth",
    "OcrPageResult",
    "OcrProvider",
    "OcrReadiness",
]


class OcrReadiness(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OcrComponentHealth:
    readiness: OcrReadiness
    name: str | None
    version: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "readiness": self.readiness.value,
            "name": self.name,
            "version": self.version,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class OcrCapability:
    provider: str
    engine: str
    engine_version: str | None
    languages: tuple[str, ...]
    features: tuple[str, ...]
    readiness: OcrReadiness
    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.readiness is not OcrReadiness.UNAVAILABLE

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "languages": list(self.languages),
            "features": list(self.features),
            "readiness": self.readiness.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class OcrHealth:
    readiness: OcrReadiness
    engine: OcrComponentHealth
    rasterizer: OcrComponentHealth
    image_parser: OcrComponentHealth
    languages: tuple[str, ...]
    requested_languages: tuple[str, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "readiness": self.readiness.value,
            "reason": self.reason,
            "languages": list(self.languages),
            "requested_languages": list(self.requested_languages),
            "engine": self.engine.to_dict(),
            "rasterizer": self.rasterizer.to_dict(),
            "image_parser": self.image_parser.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    page_number: int
    blocks: tuple[DocumentBlock, ...]
    language: str
    engine: str
    engine_version: str | None
    dpi: int
    confidence: float | None


class OcrProvider(Protocol):
    def probe(self, requested_languages: tuple[str, ...] = ()) -> OcrCapability: ...

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
    ) -> OcrPageResult: ...
