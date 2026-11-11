"""The GitHub sign-in runs in this tab and comes back, in a browser (2026-11-11).

Reached by the sidebar like a person: Workspace, then OAuth. It opened
``/api/github/oauth/start`` in a popup whose callback page answered
``window.opener.postMessage``; the pages now send
``Cross-Origin-Opener-Policy: same-origin``, which cuts that link once the
popup visits github.com. Now the page POSTs the start, goes to GitHub in the
same tab, and the callback sends the browser back to the Workspace, which says
the sign-in worked -- in English and in Arabic. GitHub is a stand-in: its
authorize page answers a redirect to the callback (as GitHub does once the
owner approves), and the code exchange is a stand-in function.

No window is opened; the page's own response carries the opener policy; the
callback is refused from a browser that did not start the sign-in.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("playwright")
httpx = pytest.importorskip("httpx")

from tests.e2e.test_provider_settings_browser import kazma  # noqa: E402,F401 -- the server fixture

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

SAID = {"en": "GitHub connected.", "ar": "تم ربط GitHub."}


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_github_sign_in_comes_back_to_the_workspace(kazma, monkeypatch, lang) -> None:  # noqa: F811
    from kazma_gateway.routers import github_client
    from playwright.sync_api import sync_playwright

    base, _fake_url = kazma
    host = base.split("//", 1)[1].split(":", 1)[0]
    monkeypatch.setenv("KAZMA_PUBLIC_URL", base)
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "Iv1.standin")
    monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "standin-secret")
    for name in ("GITHUB_TOKEN", "GITHUB_PAT", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    exchanged: list[str] = []
    stored: list[dict] = []

    async def exchange(code: str, redirect_uri: str) -> dict:
        exchanged.append(code)
        return {"access_token": "gho_standin", "scope": "repo"}

    monkeypatch.setattr(github_client, "exchange_code_for_token", exchange)
    monkeypatch.setattr(github_client, "store_oauth_token", lambda data: stored.append(data))

    def github(route) -> None:
        """GitHub's authorize page, once the owner approved: back to the callback."""
        query = parse_qs(urlsplit(route.request.url).query)
        assert query["redirect_uri"] == [f"{base}/api/github/oauth/callback"]
        back = f"{base}/api/github/oauth/callback?code=standin-code&state={query['state'][0]}"
        route.fulfill(status=302, headers={"location": back})

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            if lang == "ar":
                ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            windows: list = []
            ctx.on("page", lambda page: windows.append(page))
            ctx.route("https://github.com/login/oauth/authorize**", github)
            pg = ctx.new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.goto(base + "/", wait_until="domcontentloaded")
            pg.locator("a.nav-link[href='/workspace']").first.click()
            pg.wait_for_function("location.pathname === '/workspace'", timeout=15000)
            pg.wait_for_function("() => !!document.querySelector('.workspace-container')", timeout=15000)
            oauth = pg.locator(".ws-card-actions button", has_text="OAuth").first
            oauth.wait_for(state="visible", timeout=15000)

            with pg.expect_navigation(url=f"{base}/workspace?github_oauth=ok", timeout=20000) as came_back:
                oauth.click()
            response = came_back.value
            assert response is not None and response.ok
            # Every Kazma page says it opens no window to another site's.
            assert response.headers.get("cross-origin-opener-policy") == "same-origin"
            pg.wait_for_function(
                "(said) => document.body.innerText.includes(said)", arg=SAID[lang], timeout=15000)
            # The page took the outcome out of its address.
            pg.wait_for_function("() => !location.search.includes('github_oauth')", timeout=5000)
            assert windows == [pg], "the sign-in opened a window"
            assert exchanged == ["standin-code"] and stored and stored[0]["access_token"] == "gho_standin"

            # A state brought by a client that did not start it: refused,
            # nothing exchanged.
            refused = httpx.get(f"{base}/api/github/oauth/callback",
                                params={"code": "c", "state": "x" * 32}, timeout=10)
            assert refused.status_code == 302
            assert refused.headers["location"].endswith("github_oauth_reason=other_browser")
            assert exchanged == ["standin-code"]
        finally:
            browser.close()
    assert not errors, errors


GMAIL_SAID = {"en": "Gmail connected", "ar": "تم ربط Gmail"}


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_gmail_sign_in_starts_with_a_post_and_comes_back_to_settings(kazma, monkeypatch, lang) -> None:  # noqa: F811
    """Settings' Connect with Google: the page POSTs the start (it was a GET
    of start.json), goes to Google in the same tab, and the Gmail callback
    takes the attempt with this browser's cookie."""
    from kazma_skills.native.email_manager import oauth_gmail
    from playwright.sync_api import sync_playwright

    base, _fake_url = kazma
    host = base.split("//", 1)[1].split(":", 1)[0]
    monkeypatch.setenv("KAZMA_PUBLIC_URL", base)
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_ID", "standin.apps.googleusercontent.com")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_SECRET", "GOCSPX-standin")
    finished: list[dict] = []

    async def finish(code: str, attempt: dict) -> dict:
        finished.append(dict(attempt))
        return {"ok": True, "email": "me@example.com", "calendar_ok": True}

    monkeypatch.setattr(oauth_gmail, "finish_gmail_oauth", finish)

    def google(route) -> None:
        query = parse_qs(urlsplit(route.request.url).query)
        back = f"{base}/api/email/oauth/gmail/callback?code=standin&state={query['state'][0]}"
        route.fulfill(status=302, headers={"location": back})

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            if lang == "ar":
                ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ctx.route("https://accounts.google.com/o/oauth2/v2/auth**", google)
            starts: list[str] = []
            ctx.on("request", lambda req: starts.append(req.method)
                   if req.url.endswith("/api/email/oauth/gmail/start") else None)
            pg = ctx.new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.goto(base + "/", wait_until="domcontentloaded")
            pg.locator("a.nav-link[href='/settings']").first.click()
            pg.wait_for_function("location.pathname === '/settings'", timeout=15000)
            tab = pg.locator("button.settings-tab", has_text={"en": "Email", "ar": "البريد"}[lang]).first
            tab.wait_for(state="visible", timeout=15000)
            tab.click()
            # The card has read the mail status: the Google client is ready.
            pg.wait_for_function(
                "() => { const d = Alpine.$data(document.getElementById('settings-root'));"
                " return !!(d && d.emailStatus && d.emailStatus.gmail_oauth_client_set); }",
                timeout=15000)
            # Its card's Connect with Google, whichever sign-in mode the card shows.
            with pg.expect_navigation(url=f"{base}/settings?tab=email**", timeout=20000):
                pg.evaluate("() => Alpine.$data(document.getElementById('settings-root')).connectGmailOAuth()")
            pg.wait_for_function(
                "(said) => document.body.innerText.includes(said)", arg=GMAIL_SAID[lang], timeout=15000)
            assert starts == ["POST"], starts
            assert len(finished) == 1 and finished[0]["provider"] == "gmail"
        finally:
            browser.close()
    assert not errors, errors
