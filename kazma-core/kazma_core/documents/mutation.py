"""PDF mutation engine registry and live readiness."""

from __future__ import annotations

from .renderers import RendererPlugin, RendererRegistry

__all__ = ["get_mutation_registry"]


def get_mutation_registry() -> RendererRegistry:
    return RendererRegistry(
        (
            RendererPlugin(
                "pypdf",
                "1",
                ("pdf:merge", "pdf:split", "pdf:info", "pdf:fill-form"),
                ("pdf",),
                ("page-bounds", "form-validation", "script-rejection"),
                ("pypdf",),
            ),
            RendererPlugin(
                "pymupdf-raster-redaction",
                "1",
                ("pdf:redact",),
                ("pdf",),
                (
                    "physical-raster-redaction",
                    "flattened-output",
                    "metadata-strip",
                    "text-byte-structure-render-verification",
                ),
                ("fitz:PyMuPDF", "PIL:Pillow"),
            ),
        )
    )
