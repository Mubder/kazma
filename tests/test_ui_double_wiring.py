"""A control is wired once: never an inline handler AND a listener for the same event.

The Swarm page's Start All and Stop All buttons carried
``onclick="KazmaSwarm.start()"`` in the template, and ``swarm.js`` ``init()``
also added a click listener calling the same action, so every click sent two
POSTs and showed two toasts (found on live 2026-09-28). This reads every
template element that has both an ``id`` and an inline ``on<event>`` handler,
and fails when any static script also listens for that event on that id --
through ``$('id')``, ``document.getElementById('id')``, or a variable holding
either.
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui"
TEMPLATES = UI / "templates"
JS = UI / "static" / "js"

_TAG = re.compile(r"<[a-zA-Z][^<>]*?>", re.S)
_ID = re.compile(r"""\sid=["']([\w-]+)["']""")
_INLINE = re.compile(r"""\son([a-z]+)=(["'])(.*?)\2""", re.S)
#: A handler that only stops the browser's default (``<form
#: onsubmit="return false;">``) takes no action of its own, so a script's
#: listener beside it is the one action, not a second.
_ONLY_PREVENTS_DEFAULT = re.compile(r"^\s*(?:return\s+false|event\.preventDefault\(\))\s*;?\s*$")


def inline_handlers(html: str) -> set[tuple[str, str]]:
    """(id, event) for every tag with an id and an inline on<event> handler
    that does something."""
    found: set[tuple[str, str]] = set()
    for tag in _TAG.findall(html):
        ident = _ID.search(tag)
        if not ident or "{{" in ident.group(1):
            continue
        for event, _quote, body in _INLINE.findall(tag):
            if not _ONLY_PREVENTS_DEFAULT.match(body):
                found.add((ident.group(1), event))
    return found


def js_listeners(js: str) -> set[tuple[str, str]]:
    """(id, event) for every listener a script adds to an element it looks up by id."""
    lookup = r"""(?:\$|document\.getElementById)\(\s*['"]([\w-]+)['"]\s*\)"""
    found: set[tuple[str, str]] = set()
    # $('id').addEventListener('click', ...)
    for ident, event in re.findall(lookup + r"""\s*\.addEventListener\(\s*['"](\w+)['"]""", js):
        found.add((ident, event))
    # var btn = $('id'); ... btn.addEventListener('click', ...)
    for var, ident in re.findall(r"""\b(\w+)\s*=\s*""" + lookup, js):
        for event in re.findall(
            r"\b" + re.escape(var) + r"""\s*\.addEventListener\(\s*['"](\w+)['"]""", js
        ):
            found.add((ident, event))
    return found


def double_wired(templates: dict[str, str], scripts: dict[str, str]) -> list[str]:
    listeners: dict[tuple[str, str], list[str]] = {}
    for name, js in scripts.items():
        for key in js_listeners(js):
            listeners.setdefault(key, []).append(name)
    problems = []
    for name, html in templates.items():
        for ident, event in sorted(inline_handlers(html)):
            if (ident, event) in listeners:
                problems.append(
                    f"{name}: #{ident} has on{event}= and "
                    f"{', '.join(sorted(listeners[(ident, event)]))} also listens for {event}"
                )
    return problems


def _read(root: Path, pattern: str) -> dict[str, str]:
    return {
        p.relative_to(UI).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob(pattern))
        if "vendor" not in p.parts and not p.name.endswith(".min.js")
    }


def test_no_control_is_wired_twice():
    templates = _read(TEMPLATES, "*.html")
    scripts = _read(JS, "*.js")
    assert len(templates) > 20 and len(scripts) > 40  # not blind
    assert sum(len(inline_handlers(h)) for h in templates.values()) > 20
    assert double_wired(templates, scripts) == []


def test_the_old_start_all_wiring_is_caught():
    """Negative control: the Swarm page as it was until 2026-09-28."""
    html = (
        '<button id="swarm-start" class="btn btn-primary" onclick="KazmaSwarm.start()">'
        "Start All</button>"
    )
    js = (
        "var startBtn = $('swarm-start');\n"
        "if (startBtn) startBtn.addEventListener('click', function() { swarmAction('start'); });\n"
    )
    assert double_wired({"swarm.html": html}, {"swarm.js": js}) == [
        "swarm.html: #swarm-start has onclick= and swarm.js also listens for click"
    ]
    # Either lookup, and a chained call, are seen too.
    chained = "document.getElementById('swarm-start').addEventListener('click', go);"
    assert double_wired({"swarm.html": html}, {"x.js": chained})
