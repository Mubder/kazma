"""Choices Kazma found are shown, never hidden in a ``<datalist>`` (2026-11-19).

The setup guide said "3 models found. Pick one from the list" over a text box:
the models were a ``<datalist>``, which a browser shows only once the box is
clicked, and then only the names that match what the box already holds (the
first model, filled in). Settings -> X's model was the same. Both are a
``<select>`` now, with "Another model" for a typed name. No page may hold a
datalist again: a template, or HTML a page script builds.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = "kazma-ui/kazma_ui"
_DATALIST = re.compile(r"<\s*datalist\b|createElement\(\s*['\"]datalist['\"]", re.IGNORECASE)
#: Comments may name it (they explain why it is not used).
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_JS_COMMENT = re.compile(r"/\*.*?\*/|^\s*//.*?$", re.S | re.M)


def _page_sources() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "--", f"{UI}/templates", f"{UI}/static/js"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    out: dict[str, str] = {}
    for name in listed:
        path = ROOT / name
        if not path.is_file() or not name.endswith((".html", ".js")):
            continue
        if name.endswith(".min.js") or "/vendor/" in name:
            continue  # vendored libraries
        out[name] = path.read_text(encoding="utf-8", errors="replace")
    return out


def _holders(sources: dict[str, str]) -> list[str]:
    found = []
    for name, text in sources.items():
        text = _JINJA_COMMENT.sub("", text)
        text = _HTML_COMMENT.sub("", text)
        if name.endswith(".js"):
            text = _JS_COMMENT.sub("", text)
        if _DATALIST.search(text):
            found.append(name)
    return sorted(found)


def test_no_page_hides_a_list_in_a_datalist() -> None:
    sources = _page_sources()
    assert f"{UI}/templates/setup.html" in sources and f"{UI}/templates/settings.html" in sources
    assert _holders(sources) == []


def test_control_a_planted_datalist_is_caught() -> None:
    planted = {
        "a.html": '<input list="x"><datalist id="x"><option value="m"></option></datalist>',
        "b.js": "const d = document.createElement('datalist');",
        "c.html": "{#- never a <datalist>, which hides the list -#}<select></select>",
        "d.js": "/* a <datalist> hides the list */ const s = '<select>';",
    }
    assert _holders(planted) == ["a.html", "b.js"]
