"""Kazma UI — FastAPI + HTMX dashboard (Arabic RTL)."""

# Import i18n first so the Jinja2Templates patch is applied before any
# Jinja2Templates instance is created (including in tests).
from kazma_ui.i18n import make_translator, t  # noqa: I001,F401

from kazma_ui.app import create_app

__all__ = ["create_app", "make_translator", "t"]
