"""A page's own ``<style>`` lives where a soft navigation carries it (2026-11).

``modules/nav.js`` swaps ``.page-body`` (a template's ``{% block content %}``)
and copies the incoming ``<head>``'s styles; the body's ``{% block scripts %}``
only has its scripts re-run. The Dashboard kept its style block there, so a
Dashboard opened from the sidebar never had it: found moving the page onto the
UI refresh's components, where its layout (the banner, the tile grid) fell
apart after a sidebar click and only a reload drew it.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates"

_SCRIPTS_BLOCK = re.compile(r"\{%-?\s*block scripts\s*-?%\}(.*?)\{%-?\s*endblock", re.S)


def _styles_in_scripts_block(text: str) -> int:
    m = _SCRIPTS_BLOCK.search(text)
    return m.group(1).count("<style") if m else 0


def test_no_template_keeps_a_style_in_its_scripts_block() -> None:
    found = [
        str(p.relative_to(TEMPLATES))
        for p in sorted(TEMPLATES.rglob("*.html"))
        if _styles_in_scripts_block(p.read_text(encoding="utf-8"))
    ]
    assert not found, (
        "A <style> in {% block scripts %} never reaches a page opened from the "
        "sidebar (modules/nav.js carries .page-body and head styles only); put it "
        f"at the end of {{% block content %}}: {found}"
    )


def test_negative_control_a_style_in_the_scripts_block_is_caught() -> None:
    planted = (
        "{% block content %}<div></div>{% endblock %}\n"
        "{% block scripts %}<script src=\"/x.js\"></script>\n<style>.x{}</style>\n{% endblock %}"
    )
    assert _styles_in_scripts_block(planted) == 1
    moved = (
        "{% block content %}<div></div><style>.x{}</style>{% endblock %}\n"
        "{% block scripts %}<script src=\"/x.js\"></script>\n{% endblock %}"
    )
    assert _styles_in_scripts_block(moved) == 0


def test_the_soft_navigation_still_drops_the_scripts_block_styles() -> None:
    """The premise: nav.js re-runs the body's inline scripts and merges head
    styles; nothing copies a body ``<style>`` outside ``.page-body``."""
    nav = (TEMPLATES.parent / "static" / "js" / "modules" / "nav.js").read_text(encoding="utf-8")
    assert "function mergePageHead(doc)" in nav and "doc.head.children" in nav
    assert "function runIncomingBodyScripts(doc)" in nav
    body = nav[nav.index("function runIncomingBodyScripts(doc)"):]
    body = body[: body.index("\n    }\n")]
    assert "querySelectorAll('script')" in body and "style" not in body
