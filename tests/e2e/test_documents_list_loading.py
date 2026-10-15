"""The Documents library says "No documents yet." only after the server said so.

Live 2026-10-15: opening the Documents page showed "No documents yet." for
several seconds while ``/api/documents`` was still answering, then five
documents appeared. The empty state was ``x-show="documents.length === 0"``,
which an empty array before the first answer satisfies exactly like an answer
with nothing in it. The class gate is ``tests/test_empty_states_after_load.py``;
this drives the page: the library read is held, and the empty state must not
appear until the server answers with none -- and not at all when the read
fails.

The page is reached both directly and by the sidebar (a soft navigation,
``modules/nav.js``, which swaps the page and re-runs its scripts; the way a
person arrives, and the path every other test skipped,
``tests/e2e/test_soft_nav_into_chat.py``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

EMPTY = "No documents yet."
LOADING = "Loading…"
LIBRARY_ROUTE = "**/api/documents"
DOCS = [
    {"document_id": "doc-alpha-0001", "title": "Quarterly report", "state": "ready"},
    {"document_id": "doc-beta-00002", "title": "تقرير الربع", "state": "ready"},
]


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def _answer(route, documents=None, *, status: int = 200) -> None:
    body = {"ok": True, "documents": documents or []} if status == 200 else {"ok": False, "error": "unavailable"}
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _wait_for_held(pg, held: list) -> None:
    deadline = time.monotonic() + 30
    while not held and time.monotonic() < deadline:
        pg.wait_for_timeout(100)  # lets Playwright hand us the route
    assert held, "the page never asked for its documents"


def _visible(pg, text: str) -> bool:
    """Whether the Documents page shows an element saying exactly ``text``
    (x-show hides with display:none, so a hidden one has no offsetParent)."""
    return pg.evaluate(
        "(t) => [...document.querySelectorAll('.docs-wrap div')]"
        ".some((el) => el.textContent.trim() === t && el.offsetParent !== null)",
        text,
    )


def _open(pg, harness: Harness, how: str) -> None:
    if how == "direct":
        pg.goto(f"{harness.base}/documents", wait_until="domcontentloaded", timeout=30000)
        return
    # By the sidebar: start on another page, then click the link nav.js
    # handles. (Scheduled: the Memory page asks for the system status, which
    # loads the real embedder.)
    pg.goto(f"{harness.base}/scheduled", wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_function("() => !!window.Alpine", timeout=30000)
    pg.wait_for_timeout(1000)
    pg.evaluate(
        "() => document.querySelector('.sidebar .nav-links a[href=\"/documents\"]').click()")
    pg.wait_for_function("() => location.pathname === '/documents'", timeout=20000)


def _page_ready(pg) -> None:
    """Alpine has initialised the Documents component."""
    pg.wait_for_function(
        "() => { const el = document.querySelector('.docs-wrap');"
        " return !!(el && el._x_dataStack); }",
        timeout=30000,
    )


@pytest.mark.parametrize("how", ["direct", "sidebar"])

def test_the_library_never_claims_to_be_empty_while_it_loads(harness: Harness, how: str) -> None:
    from playwright.sync_api import sync_playwright

    held: list = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.route(LIBRARY_ROUTE, lambda route: held.append(route))
            _open(pg, harness, how)
            if how == "sidebar":
                # A soft navigation is not a page load.
                assert pg.evaluate(
                    "() => performance.getEntriesByType('navigation')[0].name"
                ).endswith("/scheduled")
            _wait_for_held(pg, held)
            _page_ready(pg)
            # Held for a while: the empty state must not appear at any point,
            # the loading line must show throughout.
            _never_seen(pg, EMPTY, 1.5, "the empty state showed while the library was loading")
            assert _visible(pg, LOADING), "nothing said the library was loading"

            # The server answers "none": now the empty state is true.
            _answer(held.pop(0))
            deadline = time.monotonic() + 15
            while not _visible(pg, EMPTY) and time.monotonic() < deadline:
                pg.wait_for_timeout(100)
            assert _visible(pg, EMPTY), "an empty answer did not show the empty state"
            assert not _visible(pg, LOADING)
            assert errors == [], errors
        finally:
            context.close()
            browser.close()



def _never_seen(pg, selector: str, seconds: float, why: str) -> None:
    """Watch *selector* for *seconds*: a check of something that must NOT
    happen, made at every step of the window, never once after a sleep."""
    deadline = time.monotonic() + seconds
    while True:
        assert not _visible(pg, selector), why
        if time.monotonic() >= deadline:
            return
        pg.wait_for_timeout(100)


@pytest.mark.parametrize("how", ["direct", "sidebar"])
def test_documents_show_when_the_answer_has_them(harness: Harness, how: str) -> None:
    from playwright.sync_api import sync_playwright

    held: list = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.route(LIBRARY_ROUTE, lambda route: held.append(route))
            _open(pg, harness, how)
            _wait_for_held(pg, held)
            _page_ready(pg)
            assert not _visible(pg, EMPTY)
            _answer(held.pop(0), DOCS)
            pg.wait_for_function(
                "() => document.querySelectorAll('.docs-wrap .doc-row').length === 2", timeout=15000)
            titles = pg.eval_on_selector_all(
                ".docs-wrap .doc-row .doc-title", "els => els.map((e) => e.innerText.trim())")
            assert titles == ["Quarterly report", "تقرير الربع"]
            assert not _visible(pg, EMPTY)
            assert not _visible(pg, LOADING)
        finally:
            context.close()
            browser.close()


def test_a_failed_read_is_not_an_empty_library(harness: Harness) -> None:
    """The rule's other half: when the read fails, nothing says "none"."""
    from playwright.sync_api import sync_playwright

    held: list = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.route(LIBRARY_ROUTE, lambda route: held.append(route))
            _open(pg, harness, "direct")
            _wait_for_held(pg, held)
            _page_ready(pg)
            _answer(held.pop(0), status=503)
            pg.wait_for_function(
                "() => { const el = document.querySelector('.docs-wrap');"
                " return Alpine.$data(el).documentsLoading === false; }",
                timeout=15000,
            )
            _never_seen(pg, EMPTY, 0.5, "a failed read was shown as an empty library")
        finally:
            context.close()
            browser.close()
