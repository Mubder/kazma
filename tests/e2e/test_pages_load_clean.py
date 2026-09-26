"""Every page of the app loads without an uncaught error; the IDE's editor is whole.

Found touring the live install one page at a time, 2026-09-26. Every IDE load
threw twice -- the vendored CodeMirror bundle called ``defineSimpleMode``
without the addon that defines it (which also stopped every editor mode after
Rust from registering), and an Alpine binding sat on a ``<template x-for>``
outside its loop -- and the workspace threw once per file-tree row. No test
loaded those pages. This one loads each page the real navigation links to --
and each Settings tab, by its ``?tab=`` deep link, since a tab's loader runs
only when it opens -- in its own browser tab, and fails on:

* an uncaught error (Playwright ``pageerror``);
* an Alpine directive that does not compile, found in the rendered DOM and
  inside every ``<template>`` (``tests/test_alpine_templates.py`` checks the
  template sources; this checks what the browser actually parsed);
* on ``/ide``, any mode ``scripts/vendor_codemirror.py`` bundles that
  ``CodeMirror.modes`` does not have.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]

#: The pages the app's own navigation links to.
_NAV_JS = r"""() => Array.from(new Set(Array.from(document.querySelectorAll('a[href^="/"]'))
  .map((a) => a.getAttribute('href').split(/[?#]/)[0])))
  .filter((h) => h.length > 1 && !h.startsWith('/static') && !h.startsWith('/api'))"""

#: Every Alpine directive on the page, compiled the way Alpine compiles it.
_PROBE_JS = r"""() => {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const DIRECTIVE = /^(x-(data|init|show|if|for|text|html|model|effect|bind:[\w.-]+|on:[\w.:-]+)|:[\w.-]+|@[\w.:-]+)$/;
  const bad = [];
  const seen = new Set();
  const compiles = (v) => {
    try { new AsyncFunction('scope', 'with (scope) { return (' + v + '\n) }'); return null; } catch (e) {}
    try { new AsyncFunction('scope', 'with (scope) { ' + v + '\n }'); return null; } catch (e) { return e.message; }
  };
  const visit = (root) => root.querySelectorAll('*').forEach((el) => {
    if (el.tagName === 'TEMPLATE') visit(el.content);
    for (const a of Array.from(el.attributes)) {
      let v = a.value;
      if (!DIRECTIVE.test(a.name) || !v.trim()) continue;
      if (a.name === 'x-for') v = v.replace(/^[\s\S]*?\s+(in|of)\s+/, '');
      if (seen.has(a.name + '=' + v)) continue;
      seen.add(a.name + '=' + v);
      const err = compiles(v);
      if (err) bad.push({ attr: a.name, value: v.slice(0, 120), err });
    }
  });
  visit(document);
  return bad;
}"""


#: Settings is one page of many tabs, and a tab's loader runs only when the tab
#: opens. The ids come from the page's own tab buttons; ?tab= deep-links each.
_SETTINGS_TABS_JS = r"""() => Array.from(document.querySelectorAll('button.settings-tab'))
  .map((b) => ((b.getAttribute('@click') || '').match(/onTabChange\('([\w-]+)'\)/) || [])[1])
  .filter(Boolean)"""


def bundled_modes() -> list[str]:
    """The mode names scripts/vendor_codemirror.py puts in the bundle."""
    tree = ast.parse((REPO / "scripts" / "vendor_codemirror.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "JS_FILES" for t in node.targets
        ):
            files = ast.literal_eval(node.value)
            return [f.split("/")[1] for f in files if f.startswith("mode/") and f.count("/") == 2]
    raise AssertionError("JS_FILES not found in scripts/vendor_codemirror.py")


def _settle(pg) -> None:
    """The page's scripts ran and its first fetches came back. A page that
    holds a stream open never goes network-idle; the bounded wait is the cap."""
    pg.wait_for_function(
        "() => document.readyState === 'complete' && !!window.Alpine", timeout=30000
    )
    try:
        pg.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: BLE001 - a live SSE stream never idles
        pass


def page_problems(pg, path: str, errors: list[str]) -> list[str]:
    found = [f"{path}: uncaught {e.splitlines()[0][:200]}" for e in errors]
    found += [
        f"{path}: {b['attr']}={b['value'][:80]!r} does not compile: {b['err']}"
        for b in pg.evaluate(_PROBE_JS)
    ]
    return found


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def test_every_page_loads_clean(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            first = context.new_page()
            first.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            first.wait_for_function("() => !!window.KazmaChat", timeout=30000)
            pages = first.evaluate(_NAV_JS)
            first.close()
            assert len(pages) >= 10 and "/ide" in pages and "/settings" in pages, pages
            tabs_page = context.new_page()
            tabs_page.goto(f"{harness.base}/settings", wait_until="domcontentloaded", timeout=30000)
            _settle(tabs_page)
            tabs = tabs_page.evaluate(_SETTINGS_TABS_JS)
            tabs_page.close()
            assert len(tabs) >= 10, tabs
            pages = pages + [f"/settings?tab={t}" for t in tabs]
            for path in pages:
                pg = context.new_page()
                errors: list[str] = []
                pg.on("pageerror", lambda exc, sink=errors: sink.append(str(exc)))
                pg.goto(f"{harness.base}{path}", wait_until="domcontentloaded", timeout=30000)
                _settle(pg)
                problems += page_problems(pg, path, errors)
                if path == "/ide":
                    modes = pg.evaluate(
                        "() => window.CodeMirror ? Object.keys(window.CodeMirror.modes) : []"
                    )
                    missing = [m for m in bundled_modes() if m not in modes]
                    if missing:
                        problems.append(f"/ide: CodeMirror modes never registered: {missing}")
                pg.close()
        finally:
            context.close()
            browser.close()
    assert not problems, "pages that do not load clean:\n  " + "\n  ".join(problems)


def test_negative_control_the_instrument_sees_both_kinds() -> None:
    """A page with the shipped quoting bug and a throwing script: both reported."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.set_content(
                """<div><span x-text="item.is_dir ? '<span class="ki"></span>' : 1"></span>"""
                """<template x-for="s in skills"><b :title="s.name"></b></template></div>"""
                """<script>setTimeout(() => { throw new Error('boom'); }, 0)</script>"""
            )
            pg.wait_for_function("() => document.readyState === 'complete'")
            pg.wait_for_timeout(200)  # the setTimeout above
            found = page_problems(pg, "/probe", errors)
        finally:
            browser.close()
    assert any("uncaught" in f and "boom" in f for f in found), found
    assert any("x-text" in f and "does not compile" in f for f in found), found
    assert len(found) == 2, found  # the template's own binding compiles fine
    assert "rust" in bundled_modes() and "lua" in bundled_modes()
