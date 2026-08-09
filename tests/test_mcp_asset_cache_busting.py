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

    assert '_STATIC_DIR / "js" / "mcp.js"' in app_source
