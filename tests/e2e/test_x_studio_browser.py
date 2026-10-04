"""The attended Studio retains drafts and fits narrow English/Arabic layouts."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402
from tests.e2e.test_pages_load_clean import _settle, page_problems  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def test_composer_survives_reload_and_mobile_bilingual_layout():
    from playwright.sync_api import sync_playwright

    with unified_turn_server() as harness, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        host = harness.base.split("//", 1)[1].split(":", 1)[0]
        try:
            for language, text in (("en", "A saved opinion about coffee."), ("ar", "رأي محفوظ عن القهوة العربية.")):
                context = browser.new_context(viewport={"width": 375, "height": 850})
                context.add_cookies([{"name": "kazma-lang", "value": language, "domain": host, "path": "/"}])
                try:
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(harness.base + "/x", wait_until="domcontentloaded")
                    _settle(page)
                    page.wait_for_function("() => Alpine.$data(document.querySelector('.xs-wrap'))._composerLoaded")
                    page.locator("#xs-text").fill(text)
                    page.wait_for_function("() => {const s = Alpine.$data(document.querySelector('.xs-wrap')); return s.text && !s._composerSaving && s._composerSignature() === s._composerSaved;}")
                    page.reload(wait_until="domcontentloaded")
                    _settle(page)
                    page.wait_for_function("() => Alpine.$data(document.querySelector('.xs-wrap'))._composerLoaded")
                    assert page.locator("#xs-text").input_value() == text
                    assert page.evaluate("() => document.documentElement.dir") == ("rtl" if language == "ar" else "ltr")
                    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                    assert not page_problems(page, "/x", errors)
                    page.locator("#xs-draft-search").fill("coffee")
                    page.wait_for_function("() => !Alpine.$data(document.querySelector('.xs-wrap')).loadStates.drafts.loading")
                finally:
                    context.close()
        finally:
            browser.close()
