"""A page's deep link is read from the page, not the address (2026-11-08).

Soft navigation (static/js/modules/nav.js) runs the incoming page's scripts
BEFORE it pushes the new address, so a page script that reads
``location.search`` at start reads the page before. Found through the bell:
a notification's link to ``/settings?tab=agent`` opened Settings on its first
tab, and so did every in-app link to a Settings tab (Agents, Memory,
Dashboard, Documents). Chat's ``?s=`` (the Dashboard's chat links) and
``?new=1``, and Replay's ``?thread=`` (the web chat's ``/replay`` link) read
the address the same way. Each page now carries what it was asked for on its
root element (``data-*``, from the request), as Research, Knowledge and the
Dashboard's incident link already did.

The gate: every read of the address's query in a page script also reads the
template's copy (a ``getAttribute`` beside it), or is declared with why the
address is right there. Behaviour: tests/e2e/test_bell_opens_the_page.py
(the bell's link through soft navigation; it failed before).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
TEMPLATES = ROOT / "kazma-ui" / "kazma_ui" / "templates"

#: Reads of the address that are right where they are, with why.
DECLARED = {
    "auth-guard.js": "builds ?next= from the address the browser shows when a request is refused",
    "streaming.js": "builds ?next= from the address the browser shows when a stream is refused",
    "login.html": "the sign-in page, always a full page load (never soft-navigated to)",
}
VENDORED = {"mermaid.min.js"}

READ = re.compile(r"location\.search")
NEAR = 10  # lines around a read that must hold the template's copy
_COMMENTS = re.compile(r"\{#.*?#\}|/\*.*?\*/", re.S)


def _code_only(text: str) -> str:
    """*text* with its Jinja and block comments blanked, lines kept."""
    return _COMMENTS.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _reads(text: str) -> list[int]:
    lines = _code_only(text).splitlines()
    found = []
    for i, line in enumerate(lines):
        if not READ.search(line) or "replaceState" in line or "pushState" in line:
            continue
        if line.strip().startswith("//"):
            continue
        window = "\n".join(lines[max(0, i - NEAR): i + NEAR + 1])
        if "getAttribute(" in window:
            continue
        found.append(i + 1)
    return found


def _sources() -> list[Path]:
    files = [p for p in STATIC.glob("*.js") if p.name not in VENDORED]
    files += list((STATIC / "modules").glob("*.js"))
    files += list(TEMPLATES.rglob("*.html"))
    return [p for p in files if p.name != "nav.js"]


def test_every_deep_link_is_read_from_the_page():
    found = []
    for path in _sources():
        if path.name in DECLARED:
            continue
        for line in _reads(path.read_text(encoding="utf-8")):
            found.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    assert not found, (
        "These read the address's query at start; a soft navigation has not pushed it yet. "
        "Carry the parameter on the page's root element (data-*, from the request) and read "
        "that first, or declare why the address is right:\n  " + "\n  ".join(found))


def test_control_a_bare_read_of_the_address_is_caught():
    planted = (
        "function init() {\n"
        "  var id = new URLSearchParams(window.location.search).get('thread');\n"
        "}\n"
    )
    assert _reads(planted) == [2]
    fixed = (
        "function init() {\n"
        "  var root = document.getElementById('replay-root');\n"
        "  var id = root ? root.getAttribute('data-thread') : new URLSearchParams(window.location.search).get('thread');\n"
        "}\n"
    )
    assert _reads(fixed) == []


def test_each_page_carries_what_it_was_asked_for():
    pairs = {
        "settings.html": 'id="settings-root" data-tab=',
        "chat.html": 'id="chat-root" data-s=',
        "replay.html": 'id="replay-root" data-thread=',
    }
    for name, marker in pairs.items():
        assert marker in (TEMPLATES / name).read_text(encoding="utf-8"), name


def test_declarations_name_real_reads():
    for name in DECLARED:
        hits = [p for p in _sources() if p.name == name]
        assert hits and any(READ.search(p.read_text(encoding="utf-8")) for p in hits), name
