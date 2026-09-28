"""Every page reads in Arabic when the reader chose Arabic (2026-09-28).

``docs/FEATURES.md`` promises an English and Arabic interface on every page.
Touring the live install in Arabic found the Memory page almost entirely in
English, and English on nearly every other page: the IDE's toolbar, the
Documents page, the chat's composer bar, the header's tooltips, "msgs" and
"ago" under every chat. The catalog tests (``tests/test_i18n.py``) check
that each KEY has an Arabic value; nothing checked that a page USES a key.

This loads every page the navigation links to, and every Settings tab, with
the Arabic cookie, and lists each visible text, placeholder, title and
aria-label that is English: no Arabic letter, and a word that is not a name
or an acronym. What sits inside ``translate="no"`` -- the HTML attribute for
what must not be translated: the user's own words, file names, a tool's own
documentation -- is content, not interface, and is skipped, as are ``code``,
``pre``, ``kbd``, form values and the conversation's own words (a message's
text is the speakers', in whatever language they wrote).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)
from tests.e2e.test_pages_load_clean import (  # noqa: E402
    _NAV_JS,
    _SETTINGS_TABS_JS,
    _settle,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: Names are not translated: products, companies, platforms and protocols,
#: and unit symbols (one list with the static template gate).
from tests._ui_names import UI_NAMES as NAMES  # noqa: E402

#: Every English piece of interface on the page, as "text" or "attr: value".
_ENGLISH_JS = r"""(names) => {
  const NAMES = new Set(names);
  const ARABIC = /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/;
  const english = (s) => {
    if (!s || ARABIC.test(s)) return false;
    for (const raw of s.split(/\s+/)) {
      const tok = raw.replace(/^[^A-Za-z0-9]+|[^A-Za-z0-9]+$/g, '');
      if (!/^[A-Za-z]{2,}$/.test(tok)) continue;      // identifiers, numbers
      if (tok === tok.toUpperCase()) continue;         // acronyms: JSON, MCP
      if (NAMES.has(tok)) continue;
      return true;
    }
    return false;
  };
  // The conversation's words are the speakers', not the interface's.
  const content = (el) => el.closest(
    '[translate="no"], code, pre, kbd, samp, script, style, noscript, textarea, option, .message-text');
  const shown = (el) => {
    if (!el.getClientRects().length) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const found = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const el = n.parentElement;
    if (!el || content(el) || !shown(el)) continue;
    // bidi.js wraps each Latin run of an Arabic sentence in its own span
    // ("... عبر <span class="bidi-isolate">Playwright</span>."): the run is
    // judged with the sentence it belongs to.
    let sentence = el;
    while (sentence.classList.contains('bidi-isolate') && sentence.parentElement) sentence = sentence.parentElement;
    if (sentence !== el && ARABIC.test(sentence.textContent)) continue;
    const text = n.textContent.replace(/\s+/g, ' ').trim();
    if (english(text)) found.add(text.slice(0, 90));
  }
  document.querySelectorAll('[placeholder], [title], [aria-label]').forEach((el) => {
    if (el.closest('[translate="no"], .message-text') || !shown(el)) return;
    for (const a of ['placeholder', 'title', 'aria-label']) {
      const v = (el.getAttribute(a) || '').replace(/\s+/g, ' ').trim();
      if (english(v)) found.add(a + ': ' + v.slice(0, 90));
    }
  });
  // A dropdown's choices are read when it opens: every option of a shown
  // <select> counts, not only the selected one.
  document.querySelectorAll('select').forEach((sel) => {
    if (sel.closest('[translate="no"]') || !shown(sel)) return;
    for (const opt of sel.options) {
      if (opt.closest('[translate="no"]')) continue;
      const v = (opt.textContent || '').replace(/\s+/g, ' ').trim();
      if (english(v)) found.add('option: ' + v.slice(0, 90));
    }
  });
  return [...found];
}"""


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def english_on(pg) -> list[str]:
    return pg.evaluate(_ENGLISH_JS, sorted(NAMES))


def test_every_page_reads_in_arabic(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
        try:
            first = context.new_page()
            first.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            first.wait_for_function("() => !!window.KazmaChat", timeout=30000)
            assert first.evaluate("() => document.documentElement.dir") == "rtl"
            pages = first.evaluate(_NAV_JS)
            first.close()
            tabs_page = context.new_page()
            tabs_page.goto(f"{harness.base}/settings", wait_until="domcontentloaded", timeout=30000)
            _settle(tabs_page)
            tabs = tabs_page.evaluate(_SETTINGS_TABS_JS)
            tabs_page.close()
            assert len(pages) >= 10 and len(tabs) >= 10, (pages, tabs)
            # The login page is not in the navigation; a visitor sees it first.
            for path in pages + ["/login"] + [f"/settings?tab={t}" for t in tabs]:
                pg = context.new_page()
                pg.goto(f"{harness.base}{path}", wait_until="domcontentloaded", timeout=30000)
                if path == "/login":
                    # A page of its own: no app shell, no Alpine.
                    pg.wait_for_load_state("load", timeout=30000)
                else:
                    _settle(pg)
                problems += [f"{path}: {text}" for text in english_on(pg)]
                pg.close()
        finally:
            context.close()
            browser.close()
    assert not problems, f"{len(problems)} English strings on Arabic pages:\n  " + "\n  ".join(problems)


def test_a_chat_turn_reads_in_arabic(harness: Harness) -> None:
    """A turn, paused at its approval card and then finished, in Arabic.

    The page tour above sees what a page shows on load; a turn's own
    interface -- the approval card and its buttons, the turn header's
    phase, a message's action buttons, its time, the chat list's line --
    only appears while one runs. On 2026-09-28 all of those were English
    for an Arabic reader: "Approve once", "Deny", "Completed", "Copy",
    "11:32 am", "web · 2 msgs · just now". The harness's scripted turn asks
    for four approvals; every one is approved here with a real click.
    """
    from playwright.sync_api import sync_playwright

    from tests.e2e.test_unified_turn_browser import PROMPT, _send, _wait_for_pending_row

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            pg.evaluate("() => window.KazmaChat.newSession()")
            pg.wait_for_timeout(800)
            _send(pg, PROMPT)
            _wait_for_pending_row(pg)
            # One live Approve: the card is on the page, measured as it stands.
            pg.wait_for_function(
                "() => document.querySelectorAll("
                "'.turn-approvals .hitl-approve:not([disabled])').length === 1",
                timeout=30000,
            )
            problems += [f"paused: {t}" for t in english_on(pg)]
            for _ in range(8):
                live = pg.locator(".turn-approvals .hitl-approve:not([disabled])")
                if live.count() == 0:
                    break
                live.first.click()
                pg.wait_for_timeout(1500)
            pg.wait_for_function(
                "() => { const h = document.querySelector('.turn-header');"
                " return !!h && h.className.indexOf('is-completed') >= 0; }", timeout=120000)
            pg.wait_for_timeout(800)
            problems += [f"finished: {t}" for t in english_on(pg)]
        finally:
            context.close()
            browser.close()
    assert not problems, f"{len(problems)} English strings in an Arabic chat turn:\n  " + "\n  ".join(problems)


def test_negative_control_the_instrument_tells_interface_from_content() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.set_content(
                '<button title="Search (Ctrl+K)">بحث</button>'
                '<span>web · 3 msgs · 1h ago</span>'
                '<span>خوادم MCP</span><span>Kazma</span><span>JSON</span>'
                '<span>deepseek-flash</span><span>1.2M</span>'
                '<span translate="no">What is my code word?</span>'
                '<code>file_read</code><kbd>Ctrl</kbd>'
                '<span style="display:none">Hidden</span>'
                '<select><option>Choose a model</option><option translate="no">Default model</option></select>'
                '<p>أتمتة المتصفح عبر <span class="bidi-isolate">Playwright</span>.</p>'
                '<p>توليد <span class="bidi-isolate">PDF و<span class="bidi-isolate">Markdown</span></span>.</p>'
                '<p><span class="bidi-isolate">Stays English</span></p>'
                '<div class="message"><div class="message-text">What the model said <b title="A tip">x</b></div></div>'
            )
            found = english_on(pg)
        finally:
            browser.close()
    assert sorted(found) == [
        "Stays English", "option: Choose a model", "title: Search (Ctrl+K)", "web · 3 msgs · 1h ago",
    ], found
