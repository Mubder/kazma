"""Regression coverage for Alpine pre-hydration visibility guards."""

from __future__ import annotations

from pathlib import Path


_TEMPLATES = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui" / "templates"


def test_known_initially_hidden_panels_are_cloaked() -> None:
    """State-gated panels must not render before Alpine evaluates their state."""
    checks = {
        "mcp.html": (
            'x-show="expanded" x-cloak class="mcp-tools-list"',
            'x-show="showAddModal" x-cloak',
        ),
        "skills.html": (
            'x-show="tab === \'installed\'" x-cloak',
            'x-show="tab === \'hub\'" x-cloak',
            'x-show="tab === \'validate\'" x-cloak',
        ),
        "ide.html": (
            'x-show="treePath" x-cloak',
            'x-show="tree.length === 0 && !busy" x-cloak',
        ),
        "components/header.html": (
            'class="user-menu-dropdown" x-show="open" x-transition x-cloak',
        ),
    }

    for relative_path, expected in checks.items():
        html = (_TEMPLATES / relative_path).read_text(encoding="utf-8")
        for fragment in expected:
            assert fragment in html, f"{relative_path} lacks x-cloak for: {fragment}"
