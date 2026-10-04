"""Compatibility links to the separately hosted user documentation."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from kazma_ui.i18n import current_language

router = APIRouter()
_GUIDES = frozenset({"email-integration", "x-publisher", "x-auto-reply", "x-evaluation-dataset"})


@router.get("/docs/guide/{guide}", include_in_schema=False)
def legacy_guide(guide: str) -> RedirectResponse:
    """Keep previously shared app links working without exposing API docs."""
    if guide not in _GUIDES:
        raise HTTPException(status_code=404, detail="Guide not found")
    locale = "/ar" if current_language() == "ar" else ""
    return RedirectResponse(f"https://kazma.ai{locale}/docs/{guide}/", status_code=302)
