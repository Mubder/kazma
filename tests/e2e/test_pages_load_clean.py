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


#: What Cloudflare serves for every request while the server restarts.
BAD_GATEWAY = "<!DOCTYPE html><html><head><title>502</title></head><body>Bad gateway</body></html>"


def _json_noise(text: str) -> bool:
    """A console line that is a page parsing an error page as JSON."""
    return "not valid JSON" in text or "Unexpected token '<'" in text


#: What a route answers when a store behind it fails (``safe_error``).
ERROR_JSON = '{"detail": "Internal Server Error", "error": "internal_error"}'


def _watch(pg, path: str, found: list[str]) -> None:
    """Record what the page threw, a JSON parse of an error page, and every
    Alpine expression error. Alpine reports an expression that throws as a
    console WARNING and rethrows it from a ``setTimeout(0)``; under a fake
    clock the rethrow may never reach the page, the warning always does
    (X Studio, 2026-09-27: CI saw the rethrow, a local run only the warning)."""
    pg.on("pageerror", lambda exc: found.append(f"{path}: uncaught {str(exc).splitlines()[0][:160]}"))
    pg.on("console", lambda msg: found.append(f"{path}: {msg.text[:160]}")
          if (msg.type == "error" and _json_noise(msg.text)) or "Alpine Expression Error" in msg.text
          else None)


def _answer_api_gets(pg, *, status: int, content_type: str, body: str) -> None:
    pg.route("**/api/**", lambda route: route.fulfill(status=status, content_type=content_type, body=body)
             if route.request.method == "GET" else route.continue_())


def _run(pg, ms: int, path: str, found: list[str]) -> None:
    """Advance the page's clock *ms*, then let real time settle the fetches it
    started. A timer that throws surfaces from ``run_for`` -- Alpine rethrows
    an expression error from a ``setTimeout(0)`` -- so it is recorded, not
    raised: every page is still visited and every problem listed."""
    from playwright.sync_api import Error as PlaywrightError

    try:
        pg.clock.run_for(ms)
    except PlaywrightError as exc:  # a timer the page set threw: a finding
        found.append(f"{path}: timer threw {str(exc).splitlines()[0][:160]}")
    pg.wait_for_timeout(1200)


def _settle_and_flush(pg, path: str, found: list[str]) -> None:
    # Real time for the page's first fetches to answer, then the clock for
    # every poller (65 s: each fires at least once) and for the rethrows the
    # answers caused. Run before the fetches answered, the clock missed an
    # error thrown from their results (X Studio, local run, 2026-09-27).
    pg.wait_for_timeout(1500)
    _run(pg, 65000, path, found)
    _run(pg, 1000, path, found)


def restart_problems(pg, path: str, base: str) -> list[str]:
    """Load *path*, then answer every API GET with the 502 page and run the
    page's timers for 65 s (the clock, not the wall): what the page threw or
    logged as a JSON parse of the error page."""
    found: list[str] = []
    _watch(pg, path, found)
    pg.clock.install()
    pg.goto(f"{base}{path}", wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_timeout(1500)
    _run(pg, 3000, path, found)
    _answer_api_gets(pg, status=502, content_type="text/html", body=BAD_GATEWAY)
    _settle_and_flush(pg, path, found)
    return found


def failing_api_problems(pg, path: str, base: str) -> list[str]:
    """Load *path* while every API GET answers 500 with an error body -- a
    store behind the routes failing -- and run its timers for 65 s: what the
    page threw. X Studio kept such a body as its status and threw on every
    render (CI, 2026-09-27: its store could not open there)."""
    found: list[str] = []
    _watch(pg, path, found)
    pg.clock.install()
    _answer_api_gets(pg, status=500, content_type="application/json", body=ERROR_JSON)
    pg.goto(f"{base}{path}", wait_until="domcontentloaded", timeout=30000)
    _settle_and_flush(pg, path, found)
    return found


def _every_page(harness: Harness, visit) -> list[str]:
    """*visit* on every nav page and Settings tab, each in its own tab."""
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
            tabs_page = context.new_page()
            tabs_page.goto(f"{harness.base}/settings", wait_until="domcontentloaded", timeout=30000)
            _settle(tabs_page)
            tabs = tabs_page.evaluate(_SETTINGS_TABS_JS)
            tabs_page.close()
            assert len(pages) >= 10 and len(tabs) >= 10, (pages, tabs)
            for path in pages + [f"/settings?tab={t}" for t in tabs]:
                pg = context.new_page()
                problems += visit(pg, path, harness.base)
                pg.close()
        finally:
            context.close()
            browser.close()
    return sorted(set(problems))


def test_every_page_survives_a_restart(harness: Harness) -> None:
    """While the server restarts, the pages stay quiet (2026-09-27).

    The Memory and Swarm pages' pollers parsed Cloudflare's 502 HTML page as
    JSON and logged "Unexpected token '<'" into the console on every restart;
    pollers read through ``window.kazmaGetJson`` now, which answers null for an
    error page. Every nav page and Settings tab, in its own tab: no uncaught
    error and no JSON parse of the error page. (Chrome's own "Failed to load
    resource" lines are the browser's, not the page's, and are not counted.)"""
    problems = _every_page(harness, restart_problems)
    assert not problems, "pages that break while the server restarts:\n  " + "\n  ".join(problems)


def test_every_page_survives_its_apis_failing(harness: Harness) -> None:
    """With every API answering 500 and an error body -- a store behind the
    routes failing -- every page still renders without throwing
    (2026-09-27). X Studio kept the error body as its status and threw on
    every render; CI found it where the X store could not open."""
    problems = _every_page(harness, failing_api_problems)
    assert not problems, "pages that break when their APIs fail:\n  " + "\n  ".join(problems)


def test_negative_control_a_page_keeping_an_error_body_is_caught(harness: Harness) -> None:
    """X Studio's shape as it shipped, on a page of its own with the real
    Alpine: the error body becomes the state and a nested read throws. The
    instrument reports it; the same page reading through kazmaGetJson and
    keeping its shape does not."""
    from playwright.sync_api import sync_playwright

    def page(loader: str) -> str:
        return (
            "<html><body><div x-data=\"{status: {caps: {}}, async init() {" + loader + "}}\">"
            "<span x-text=\"status.caps.posts_today || 0\"></span></div>"
            "<script src=\"/static/js/auth-guard.js\"></script>"
            "<script defer src=\"/static/js/alpine.min.js\"></script></body></html>"
        )

    shipped = page("const r = await fetch('/api/x/status'); const d = await r.json(); if (d) this.status = d;")
    fixed = page("const d = await window.kazmaGetJson('/api/x/status');"
                 " if (d && d.caps) this.status = Object.assign({caps: {}}, d);")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            found = {}

            def serve(html: str):
                # Playwright passes (route, request) to a two-argument handler.
                return lambda route: route.fulfill(status=200, content_type="text/html", body=html)

            for name, body in (("shipped", shipped), ("fixed", fixed)):
                pg = browser.new_page()
                pg.route("**/probe-page", serve(body))
                found[name] = failing_api_problems(pg, "/probe-page", harness.base)
                pg.close()
        finally:
            browser.close()
    assert any("posts_today" in f for f in found["shipped"]), found
    assert found["fixed"] == [], found


def test_negative_control_a_poller_reading_the_error_page_is_caught(harness: Harness) -> None:
    """The poller shape that shipped, on a page of its own: the instrument
    reports it."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.route("**/probe-page", lambda route: route.fulfill(status=200, content_type="text/html", body=(
                "<html><body><script>setInterval(function () {"
                " fetch('/api/system/status').then(function (r) { return r.json(); })"
                ".catch(function (e) { console.error('Failed to poll:', e); }); }, 5000);"
                "</script></body></html>")))
            found = restart_problems(pg, "/probe-page", harness.base)
        finally:
            browser.close()
    assert found and all("not valid JSON" in f or "Unexpected token" in f for f in found), found


def test_the_alert_banner_shows_what_the_store_holds(harness: Harness) -> None:
    """The system-alerts banner reads the header's notifications store; it
    polled /api/alerts/recent on a second poller of its own until 2026-09-26.
    An alert in the store shows in the banner, and a dismissed one goes."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.goto(f"{harness.base}/dashboard", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            pg.evaluate(
                "() => { Alpine.store('notifications').items = [{ id: 'probe-1',"
                " title: 'Probe alert', reason: 'from the store', timestamp: Date.now() / 1000 }]; }"
            )
            banner = pg.locator(".system-alerts-banner")
            banner.wait_for(state="visible", timeout=5000)
            assert "Probe alert" in banner.inner_text()
            pg.click(".system-alerts-banner button[title='Dismiss']")
            banner.wait_for(state="hidden", timeout=5000)
        finally:
            browser.close()
