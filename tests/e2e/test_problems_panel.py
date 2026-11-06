"""The Dashboard's Open problems card in a browser, in English and Arabic.

A real problem (a banner the model-fallback notice posts) is shown on the
Dashboard reached by the sidebar, worded in the page's language;
Acknowledge hides it, and a resolved problem leaves by itself. No page error.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_problems_card_shows_a_problem_and_acknowledges_it(language: str) -> None:
    from kazma_core.observability.alerts import AlertDispatcher
    from kazma_ui.i18n import TRANSLATIONS
    from playwright.sync_api import sync_playwright

    with unified_turn_server() as h:
        AlertDispatcher.post_banner(subsystem="llm-failover:e2e", title="Model failover: e2e-model failed",
                                    reason="e2e-model kept failing", severity="WARNING")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(h.base + "/memory", wait_until="domcontentloaded")
                    page.wait_for_function("() => !!window.Alpine")
                    page.evaluate("() => { window.__notReloaded = true; }")
                    page.click('nav a[href="/dashboard"], aside a[href="/dashboard"]', timeout=15000)
                    page.wait_for_function("() => location.pathname === '/dashboard'", timeout=20000)
                    page.wait_for_selector("#problems-list li", timeout=20000)
                    assert page.evaluate("() => window.__notReloaded === true"), "reached by the sidebar"
                    assert TRANSLATIONS["problems.title"][language] in page.text_content("#problems-title")
                    text = page.inner_text("#problems-list")
                    assert "Model failover: e2e-model failed" in text
                    assert TRANSLATIONS["problems.resolved.alert"][language] in text
                    page.click("#problems-list li button:nth-of-type(1)")  # Acknowledge
                    page.wait_for_function("() => !document.querySelector('#problems-list li')", timeout=20000)
                    assert not page.is_hidden("#problems-empty")
                    assert errors == [], errors
                finally:
                    browser.close()
        finally:
            AlertDispatcher.resolve_alerts_for_subsystem("llm-failover:e2e")
