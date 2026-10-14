"""The About me over-limit message reads in Arabic (Memory audit M32).

The server answered "About me is N characters; the limit is 2000." in
English on an Arabic page. It now answers a code (``about_too_long`` with the
length and the limit) and the page words it in its own language.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _too_long_message(harness: Harness, lang: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        host = harness.base.split("//", 1)[1].split(":", 1)[0]
        context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            # The page loads the saved text (loadAboutMe) and writes it over
            # the box when the answer lands: wait for it, or under load the
            # text set below is replaced and Save stays disabled.
            with pg.expect_response(lambda r: "/api/memory/v2/profile" in r.url and r.request.method == "GET",
                                    timeout=30000):
                pg.goto(f"{harness.base}/settings?tab=memory", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function("() => !!window.kazmaSave && !!window.Alpine", timeout=30000)
            pg.locator("#about-me-text").wait_for(state="visible", timeout=15000)
            # The textarea stops typing at the limit; a longer text arrives by
            # paste into an older page, or through the API -- set it directly.
            # Set until it holds: the saved text, applied a moment after its
            # response, may replace it once.
            pg.wait_for_function("""() => {
                const data = window.Alpine.$data(document.querySelector('#about-me-text'));
                if ((data.aboutMe || '').length !== 2100) {
                    data.aboutMeMax = 5000;
                    data.aboutMe = 'x'.repeat(2100);
                    return false;
                }
                return !document.querySelector('#memory-about-me-section button.btn-primary').disabled;
            }""", timeout=15000, polling=200)
            pg.locator("#memory-about-me-section button.btn-primary").click()
            err = pg.locator("#memory-about-me-section .form-error")
            err.wait_for(state="visible", timeout=15000)
            return err.inner_text()
        finally:
            context.close()
            browser.close()


def test_the_over_limit_message_reads_in_arabic(harness: Harness) -> None:
    text = _too_long_message(harness, "ar")
    assert "أطول من الحد المسموح" in text, text
    assert "characters" not in text and "limit" not in text, text  # no English left
    assert "2100" in text and "2000" in text, text


def test_the_same_message_in_english(harness: Harness) -> None:
    """Negative control of the instrument: the English page shows English."""
    text = _too_long_message(harness, "en")
    assert "longer than the limit" in text, text
