"""No literal colour in a template or a page script (the UI refresh, 2026-11-09).

The owner saw different colours from page to page; four audits traced it to
colours written straight into templates, inline styles, template ``<style>``
blocks and page scripts (418 in templates, about 230 in scripts) instead of
the tokens in ``kazma.css``. The refresh moved every one onto the tokens and
the shared classes (AGENTS.md, UI Conventions: "One colour system"). This gate
keeps it there: a hex, ``rgb()``/``rgba()``/``hsl()`` or a named colour in a
colour property is a failure, except the few declared below with their reason.
The stylesheets have their own ratchet (``tests/test_ui_color_tokens.py``).

Canvas, chart and Mermaid code reads the tokens with ``getComputedStyle`` and
builds its colour strings at draw time; that is not a literal.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "kazma-ui" / "kazma_ui"

HEX = re.compile(r"(?<![\w&])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![\w-])")
FUNC = re.compile(r"\b(?:rgba?|hsla?)\(\s*[\d.]")
NAMED = re.compile(
    r"(?:color|background|border|fill|stroke|outline)[\w-]*\s*:\s*"
    r"(white|black|red|green|blue|yellow|orange|purple|gray|grey)\b",
    re.I,
)

#: (path under kazma-ui/kazma_ui, a substring of the line) -> why the literal
#: stays. Each must still match a line (a stale entry fails).
DECLARED: dict[tuple[str, str], str] = {
    ("templates/base.html", 'name="theme-color"'):
        "the browser's own canvas colour, read before any stylesheet loads",
    ("templates/base.html", "html { background-color:"):
        "the first paint, before kazma.css loads (no white flash in dark)",
    ("templates/base.html", "own dark gray (#121212)"):
        "a Jinja comment naming the browser's colour",
    ("templates/login.html", 'name="theme-color"'):
        "the browser's own canvas colour, read before any stylesheet loads",
    ("templates/login.html", "html { background-color:"):
        "the first paint, before kazma.css loads (no white flash in dark)",
    ("static/js/modules/components.js", "const _THEME_COLOR"):
        "the theme-color fallback when --bg cannot be read",
    ("static/js/modules/components.js", "const darkOn ="):
        "the accent maths: the ink chosen for a chosen accent (accent_colors.py's twin)",
    ("static/js/modules/components.js", "'--accent-chosen-light-on'"):
        "the accent maths: the light variant's ink",
    ("static/js/settings_ops.js", "the retired indigo #5e6ad2"):
        "a comment naming the retired default",
    ("templates/settings.html", '<input type="color" :value="appearance.accent_color'):
        "the accent picker's value when nothing is chosen (an <input type=color> needs a hex)",
    ("templates/settings.html", 'x-model="appearance.accent_color" style="width:120px" placeholder='):
        "the accent field's placeholder: an example of what to type",
    ("templates/settings.html", "<template x-for=\"color in ['#3b82f6'"):
        "the accent swatches: colours the owner can choose, data the picker writes",
}


def _files() -> list[Path]:
    templates = sorted((UI / "templates").rglob("*.html"))
    scripts = sorted(
        p for p in (UI / "static" / "js").rglob("*.js")
        if "vendor" not in p.parts and not p.name.endswith(".min.js")
    )
    return templates + scripts


def _literals(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if HEX.search(line) or FUNC.search(line) or NAMED.search(line):
            found.append((number, line))
    return found


def _undeclared(rel: str, text: str) -> list[str]:
    out = []
    for number, line in _literals(text):
        if any(rel == path and part in line for (path, part) in DECLARED):
            continue
        out.append(f"{rel}:{number}: {line.strip()[:140]}")
    return out


def test_no_literal_colour_in_templates_or_page_scripts():
    problems: list[str] = []
    for path in _files():
        rel = path.relative_to(UI).as_posix()
        problems += _undeclared(rel, path.read_text(encoding="utf-8", errors="replace"))
    assert not problems, (
        "a literal colour in a template or page script -- use a token from "
        "kazma.css or a shared class (AGENTS.md, UI Conventions):\n" + "\n".join(problems[:60])
    )


def test_every_declaration_still_matches_a_line():
    stale = []
    for (rel, part), _why in DECLARED.items():
        path = UI / rel
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        if not any(part in line for _n, line in _literals(text)):
            stale.append(f"{rel}: {part!r}")
    assert not stale, "declared literal colours that no longer exist:\n" + "\n".join(stale)


def test_negative_control_planted_literals_are_caught():
    planted = (
        '<div class="card" style="background:#1a1a2e">x</div>\n'
        "<span style=\"color: rgba(255,255,255,.5)\">y</span>\n"
        "el.style.borderColor = 'hsl(220, 40%, 20%)';\n"
        "<style>.x { color: white; }</style>\n"
        "<p style=\"color: var(--text-muted)\">token: fine</p>\n"
        "document.querySelector('#chat-root');\n"
    )
    found = _undeclared("templates/planted.html", planted)
    assert len(found) == 4, found
    assert not any("--text-muted" in f or "chat-root" in f for f in found)
