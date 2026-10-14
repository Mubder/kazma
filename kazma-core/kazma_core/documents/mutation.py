"""PDF mutation engine registry and live readiness."""

from __future__ import annotations

from .renderers import RendererPlugin, RendererRegistry

__all__ = ["get_mutation_registry"]


_MUTATION_PLUGINS = (
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


def get_mutation_registry() -> RendererRegistry:
    """Live PDF mutation readiness; the worker image's in the container profile."""

    from .worker_container import image_renderer_registry, uses_worker_image

    if uses_worker_image():
        return image_renderer_registry("mutators", _MUTATION_PLUGINS)
    return RendererRegistry(_MUTATION_PLUGINS)
