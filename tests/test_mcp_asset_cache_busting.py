"""Regression coverage for MCP page client-asset cache invalidation."""

from __future__ import annotations

from pathlib import Path


def test_mcp_script_participates_in_global_asset_version() -> None:
    """A changed MCP action handler must receive a new URL version."""
    app_source = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "app.py"
    ).read_text(encoding="utf-8")

    # js_version() derives from the WHOLE static/js tree (2026-08-26
    # split-brain fix) — every JS file participates, mcp.js included, with no
    # hand-maintained whitelist to drift.
    assert 'rglob("*.js")' in app_source

    mcp_html = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "templates"
        / "mcp.html"
    ).read_text(encoding="utf-8")
    assert "mcp.js?v={{ js_version() }}" in mcp_html
