"""Kazma TUI — Professional Textual-based terminal dashboard."""

from __future__ import annotations

try:
    from kazma_core.version import get_version as _get_version

    __version__ = _get_version()
except Exception:  # pragma: no cover - bare install without core
    __version__ = "0.10.0"
