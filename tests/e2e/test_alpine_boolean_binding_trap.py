"""The rule ``tests/test_alpine_boolean_bindings.py`` rests on, in a browser.

The vendored Alpine (``static/js/alpine.min.js``) runs in Chromium over a
page whose every member is missing. Each binding is judged by the gate's own
reading (``traps``) and by the browser: the gate flags a binding exactly when
the browser disables its button. A dotted ``undefined`` disables (Alpine
turns it into ``""``), ``!!`` enables it again, and a bare ``undefined``
enables. ``0`` and ``""`` set the attribute too, which is why the rule is
"bind a boolean", not only "beware the dot".

No page of Kazma is opened: the witness is Alpine's behaviour, which every
page shares.
"""

from __future__ import annotations

import html
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from tests.test_alpine_boolean_bindings import ALPINE, traps  # noqa: E402

pytestmark = [pytest.mark.e2e]

#: Bindings judged by the gate and by the browser, with every member missing.
BINDINGS = (
    "s.missing",
    "!!s.missing",
    "busy",
    "s.a && s.b",
    "!!(s.a && s.b)",
    "list.length > 0 || busy",
    "list.length > 0 || !!busy",
    "s.count === 1",
)

#: Not dotted, so outside the gate, and still not a boolean: each disables.
NOT_BOOLEANS = ("zero", "empty")


def _page(bindings: tuple[str, ...]) -> str:
    buttons = "\n".join(
        f'<button id="b{i}" :disabled="{html.escape(expr, quote=True)}">b{i}</button>'
        for i, expr in enumerate(bindings))
    return (
        "<!doctype html><html><body>"
        "<div x-data=\"{ s: {}, busy: undefined, list: [], zero: 0, empty: '' }\">"
        f"{buttons}<span id='ready' x-text=\"'ready'\"></span></div></body></html>")


def _disabled(bindings: tuple[str, ...]) -> list[bool]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(_page(bindings))
            page.add_script_tag(path=str(Path(ALPINE)))
            page.wait_for_selector("#ready:has-text('ready')")
            return [page.locator(f"#b{i}").is_disabled() for i in range(len(bindings))]
        finally:
            browser.close()


def test_the_gate_flags_exactly_what_the_browser_disables() -> None:
    names = frozenset({"disabled"})
    flagged = [bool(traps({"page.html": f'<button :disabled="{expr}"></button>'}, names))
               for expr in BINDINGS]
    disabled = _disabled(BINDINGS)
    assert disabled == flagged, list(zip(BINDINGS, flagged, disabled))
    # The trap itself and its fix, said plainly.
    assert disabled[0] is True and disabled[1] is False and disabled[2] is False


def test_zero_and_the_empty_string_set_the_attribute_too() -> None:
    assert _disabled(NOT_BOOLEANS) == [True, True]
