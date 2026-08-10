"""Metadata-only image parser used before isolated OCR routing."""

from __future__ import annotations

from pathlib import Path

from ..errors import DocumentFormatError, DocumentLimitError, DocumentUnavailableError
from ..models import DocumentIR
from .common import IRBuilder, ParseContext

__all__ = ["ImageParser"]


class ImageParser:
    def parse(self, path: Path, context: ParseContext) -> DocumentIR:
        try:
            from PIL import Image
        except ImportError as exc:
            raise DocumentUnavailableError("Pillow image parser is unavailable") from exc
        builder = IRBuilder(path, context)
        try:
            with Image.open(path) as image:
                frame_count = int(getattr(image, "n_frames", 1))
                if frame_count > context.config.max_pages:
                    raise DocumentLimitError(
                        "Image frame count exceeds the configured page limit"
                    )
                for frame in range(frame_count):
                    image.seek(frame)
                    builder.count_images(1)
                    width, height = image.size
                    if width <= 0 or height <= 0:
                        raise DocumentFormatError("Image dimensions are invalid")
                    if width * height > context.config.max_pixels_per_image:
                        raise DocumentLimitError(
                            "Image exceeds the configured pixel limit"
                        )
                    builder.add_page(
                        [],
                        width=float(width),
                        height=float(height),
                        metadata={
                            "kind": "image",
                            "image_only": True,
                            "image_count": 1,
                            "format": str(image.format or "").upper(),
                            "mode": image.mode,
                        },
                    )
        except (DocumentFormatError, DocumentLimitError):
            raise
        except Exception as exc:
            raise DocumentFormatError("Image could not be decoded safely") from exc
        return builder.build(
            metadata={
                "page_count": len(builder.pages),
                "image_format": builder.pages[0].metadata.get("format", "")
                if builder.pages
                else "",
            }
        )
