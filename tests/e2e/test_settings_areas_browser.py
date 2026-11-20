"""Settings in three areas, in a real browser (Spaces S1-23).

With Spaces enforced, Settings is My account, This space and Install. A
member signed in with their own password reaches Settings by the sidebar
(soft navigation runs the page scripts again) and is shown My account and
This space, in the page's language; Install, the API tokens and the
platform users are not on the page, and nothing the page asks for is
refused. The admin, on the same install, is shown the three areas.

Negative control: the same member on an install that does not enforce
Spaces is sent to the sign-in page, as before Spaces: Settings is the
admin's there.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

MEMBER = "dana-settings"
OWN_SPACE = "dana-own"


def _soft_nav(pg, href: str) -> None:
    """Follow a sidebar link the way a person does: a click nav.js handles."""
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _sign_in_on() -> None:
    """Sign-in on (the harness runs with it off; it puts the variable back)."""
    import os

    os.environ["KAZMA_SECRET"] = "settings-areas-test-secret-" + "x" * 24


def _member() -> str:
    """A local user with a personal space; the raw session id of their
    sign-in."""
    from kazma_core.security import platform_rbac, web_sessions
    from kazma_core.spaces import identity
    from kazma_ui import space_binding

    user = platform_rbac.create_local_user(MEMBER, "a-long-enough-password-1")
    account = space_binding.account_of({"source": "session", "actor": "login", "user_id": user.user_id,
                                        "username": MEMBER, "role": "operator"})
    assert account, "the member has no account"
    identity.create_space("Dana", created_by=account, personal_of=account, space_id=OWN_SPACE)
    return web_sessions.create_session(role="operator", actor="login", username=MEMBER, user_id=user.user_id)


def _open_settings_by_sidebar(pg, base: str, space: str) -> None:
    pg.goto(f"{base}/chat?space={space}", wait_until="domcontentloaded")
    pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
    _soft_nav(pg, "/settings")
    pg.locator("#settings-root").wait_for(state="attached", timeout=30000)
    pg.wait_for_function(
        "() => { const el = document.querySelector('#settings-root');"
        " return el && el._x_dataStack && !el._x_dataStack[0].loading; }", timeout=30000)


def _label(key: str, lang: str) -> str:
    from kazma_ui.i18n import t

    return t(key, lang)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_member_is_shown_their_account_and_their_space(lang):
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        _sign_in_on()
        session = _member()
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base},
                                 {"name": "kazma-session", "value": session, "url": h.base}])
            pg = context.new_page()
            pg.on("response", lambda r: refused.append(f"{r.status} {r.url}")
                  if r.status in (401, 403) and "/api/" in r.url and "/settings" in pg.url else None)
            _open_settings_by_sidebar(pg, h.base, OWN_SPACE)
            assert pg.evaluate("() => window.kazmaSpace.id") == OWN_SPACE
            assert pg.evaluate("() => document.documentElement.lang") == lang

            # My account and This space, in the page's language; no Install.
            assert pg.locator("#settings-area-account").inner_text().strip() == _label("settings.area_account", lang)
            assert pg.locator("#settings-area-space").inner_text().strip() == _label("settings.area_space", lang)
            assert pg.locator("#settings-area-install").count() == 0
            assert pg.locator("#hub-subtab-providers").count() == 0
            # The account tab is theirs: the password, not the owner's API tokens.
            pg.locator("input[x-model='passwordForm.new_password']").wait_for(state="visible", timeout=20000)
            assert pg.locator("[x-model='tokenName']").count() == 0

            # This space's tabs load, and nothing the page asks for is refused.
            for tab in ("email", "x"):
                pg.evaluate("(tab) => document.querySelector('#settings-root')._x_dataStack[0].onTabChange(tab)",
                            tab)
                pg.wait_for_function(
                    "(tab) => document.querySelector('#settings-root')._x_dataStack[0].tab === tab",
                    arg=tab, timeout=20000)
            pg.wait_for_timeout(800)
            # An install tab asked for by name is never opened.
            pg.evaluate("() => document.querySelector('#settings-root')._x_dataStack[0].onTabChange('system')")
            assert pg.evaluate("() => document.querySelector('#settings-root')._x_dataStack[0].tab") == "account"
        finally:
            browser.close()
            scope.set_enforced(False)
    assert refused == [], refused


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_is_shown_the_three_areas(lang):
    from kazma_core.spaces import scope
    from kazma_core.spaces.scope import OWNER_SPACE_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        _sign_in_on()
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = context.new_page()
            # The first page a loopback browser opens mints the owner's sign-in.
            _open_settings_by_sidebar(pg, h.base, OWNER_SPACE_ID)
            for area in ("account", "space", "install"):
                got = pg.locator(f"#settings-area-{area}").inner_text().strip()
                assert got == _label(f"settings.area_{area}", lang), (area, got)
            assert pg.locator("#hub-subtab-providers").count() == 1
        finally:
            browser.close()
            scope.set_enforced(False)


def test_control_without_spaces_a_member_does_not_reach_settings():
    """Negative control: not enforced, Settings stays the admin's -- the
    member's page is the sign-in page, never the three areas."""
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        _sign_in_on()
        session = _member()
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-session", "value": session, "url": h.base}])
            pg = context.new_page()
            answer = pg.goto(f"{h.base}/settings", wait_until="domcontentloaded")
            assert pg.locator("#settings-area-account").count() == 0, (answer.status if answer else None, pg.url)
        finally:
            browser.close()
