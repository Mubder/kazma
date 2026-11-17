"""Settings -> X -> The whole X API in a browser, reached by the sidebar, in
English and Arabic (2026-11-17).

- The card shows how many of X's operations Kazma can call and which of the
  three sign-ins it has; a Bearer token saved there is kept, never shown, and
  marks the app sign-in as set.
- Sign in with X: the button goes to X's consent page in the same tab (a
  stand-in page here) with a PKCE challenge, and X's answer back lands on
  Settings -> X with "Signed in with X (@...)"; the card then names the
  account.
- Arabic: the card reads in Arabic.

Set ``KAZMA_TEST_SHOTS`` to a folder to keep screenshots.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Harness, unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BEARER = "AAAAAAAAAAAAAAAAAAAAAbearer%2Btoken%3Dvalue"


@pytest.fixture
def server(monkeypatch):
    from kazma_core.x_api import client as xc
    from kazma_core.x_api import oauth2_sign_in

    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)

    def exchange(code, state, attempt):
        assert code == "code-from-x"
        return {"access_token": "user-access", "refresh_token": "user-refresh", "expires_in": 7200,
                "scope": "tweet.read users.read offline.access"}

    async def account(self, *, auth="user"):
        return {"id": "42", "username": "kazmaai", "name": "Kazma"}

    monkeypatch.setattr(oauth2_sign_in, "exchange", exchange)
    monkeypatch.setattr(xc.XClient, "get_account", account)
    with unified_turn_server() as h:
        # X sends the browser back to the install's own address; the PKCE
        # verifier is made from Kazma's own secret (a real install makes one
        # at its first start; the harness clears it).
        monkeypatch.setenv("KAZMA_PUBLIC_URL", h.base)
        monkeypatch.setenv("KAZMA_SECRET", "s" * 64)
        yield h


def _shot(page, name: str) -> None:
    folder = os.environ.get("KAZMA_TEST_SHOTS")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.locator("#xr-api-section").screenshot(path=str(Path(folder) / f"{name}.png"))


def _open_x(page, base: str, tab_label: str) -> None:
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("a.nav-link[href='/settings']").first.click()
    page.wait_for_function("location.pathname === '/settings'", timeout=20000)
    page.locator("nav.settings-nav").get_by_role("tab", name=tab_label, exact=True).click()
    page.locator("#xr-api-section").wait_for(state="visible", timeout=20000)
    page.wait_for_function(
        "(document.querySelector('#xr-api-section strong') || {}).innerText !== '0 / 0'", timeout=20000)


def test_the_whole_x_api_card(server) -> None:
    from playwright.sync_api import sync_playwright

    h: Harness = server
    errors: list[str] = []
    host = h.base.split("//", 1)[1].split(":", 1)[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_context(viewport={"width": 1366, "height": 900}, locale="en-US").new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            _open_x(pg, h.base, "X")
            assert pg.locator("#xr-api-title").inner_text() == "The whole X API"

            # The Bearer token: kept, never shown back.
            pg.locator("#x-api-bearer").fill(BEARER)
            pg.locator("#xr-api-section button", has_text="Save").first.click()
            pg.wait_for_function("document.querySelector('#x-api-bearer').value === ''", timeout=15000)
            pg.wait_for_function(
                "fetch('/api/x/api-access').then(r => r.json()).then(d => d.available_auth.app === true)",
                timeout=15000)
            assert BEARER not in pg.content()
            assert pg.locator("#x-api-bearer").get_attribute("placeholder") == "********"

            # Sign in with X: to X's page in this tab, and back.
            pg.locator("#x-api-client-id").fill("client-id-12345")
            pg.locator("#xr-api-section button", has_text="Save client").click()
            pg.wait_for_function(
                "fetch('/api/x/api-access').then(r => r.json()).then(d => d.client_id === 'client-id-12345')",
                timeout=15000)
            consent: dict = {}

            def x_page(route):
                consent["url"] = route.request.url
                route.fulfill(status=200, content_type="text/html", body="<p>X consent</p>")

            pg.route("https://x.com/**", x_page)
            with pg.expect_response("**/api/x/oauth2/start") as started:
                pg.locator("#xr-api-section button", has_text="Sign in with X").click()
            assert started.value.status == 200, started.value.text()
            pg.wait_for_function("location.host === 'x.com'", timeout=15000)
            query = parse_qs(urlparse(consent["url"]).query)
            assert query["code_challenge_method"] == ["S256"] and query["client_id"] == ["client-id-12345"]
            assert query["redirect_uri"][0].endswith("/api/x/oauth2/callback")
            state = query["state"][0]
            # X sends the browser back with its code (the harness's own address).
            pg.goto(f"{h.base}/api/x/oauth2/callback?code=code-from-x&state={state}", wait_until="domcontentloaded")
            pg.wait_for_function("location.pathname === '/settings'", timeout=20000)
            pg.locator("#xr-api-section").wait_for(state="visible", timeout=20000)
            pg.wait_for_function("document.body.innerText.includes('Signed in with X (@kazmaai)')", timeout=20000)
            pg.wait_for_function("(document.querySelector('#xr-api-section') || {}).innerText.includes('@kazmaai')",
                                 timeout=20000)
            assert "x_oauth" not in pg.url
            _shot(pg, "x-api-en")

            # Arabic, by the sidebar too.
            ctx = browser.new_context(viewport={"width": 1366, "height": 900})
            ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            _open_x(ar, h.base, "إكس")
            assert ar.evaluate("document.documentElement.dir") == "rtl"
            assert ar.locator("#xr-api-title").inner_text() == "واجهة X كاملة"
            assert "مسجّل الدخول بحساب X باسم" in ar.locator("#xr-api-section").inner_text()
            _shot(ar, "x-api-ar")
        finally:
            browser.close()
    assert errors == [], errors
