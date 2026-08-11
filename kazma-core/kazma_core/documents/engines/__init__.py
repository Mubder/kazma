"""Format engines for the unified document layer.

Each engine consumes a
:class:`~kazma_core.documents.content_model.ContentModel` + a
:class:`~kazma_core.documents.profile.DocProfile` and renders to one format.
Adding a format = adding a module here that implements ``render(model, profile,
output)``. The alignment/direction semantics come from the profile, so a new
engine never re-derives the Word BiDi rules (or any other gotcha) itself.
"""

from __future__ import annotations

__all__ = ["DocxEngine"]
