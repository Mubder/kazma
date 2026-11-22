"""A member's Settings page asks nothing the server refuses (Spaces, 2026-11-23).

The page shell read the install's settings on every page -- the appearance
(`GET /api/settings/appearance`, and a `PUT` when the theme was switched),
the active model (`/api/provider/active`), the providers (`/api/providers`)
and the voice settings (`/api/settings/voice`) -- all the admin's, so a
member's page was refused four times on every load. The server now tells the
page (`<meta name="kazma-install-settings">`, ``auth.install_settings_here``)
and the shell asks them only where it may; the sidebar's default-model
picker (it sets the install's default) is the admin's too. This space's tabs
of a feature the person lacks (email and calendar, X Studio) are neither
drawn nor opened.

A member with the features a new person starts with (`real_features`: no
email, no X) opens Kazma, reaches Settings by the sidebar, opens each tab
shown, and switches the theme: no request is refused, in English and Arabic.

Negative control: today's shell (the page told it may read the install's
settings) is refused the install's routes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_settings_areas_browser import (  # noqa: E402
    OWN_SPACE,
    _member,
    _open_settings_by_sidebar,
    _sign_in_on,
)

pytestmark = pytest.mark.real_features

#: The install's routes today's shell asked on every page.
INSTALL_ROUTES = ("/api/settings/appearance", "/api/provider/active", "/api/providers", "/api/settings/voice")


def _walk(lang: str) -> list[str]:
    """A member's walk through Settings by the sidebar: every refused
    request (``status method path``), from the first page load on."""
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
            pg.on("response", lambda r: refused.append(f"{r.status} {r.request.method} {r.url.split('?')[0]}")
                  if r.status in (401, 403) and "/api/" in r.url else None)
            _open_settings_by_sidebar(pg, h.base, OWN_SPACE)
            assert pg.evaluate("() => document.documentElement.lang") == lang
            # The tabs of features this member lacks are not on the page.
            drawn = pg.evaluate(
                "() => [...document.querySelectorAll('.settings-tab')].map(b => b.getAttribute('@click'))")
            assert "onTabChange('email')" not in drawn and "onTabChange('x')" not in drawn, drawn
            assert "onTabChange('account')" in drawn, drawn
            root = "document.querySelector('#settings-root')._x_dataStack[0]"
            shown = pg.evaluate(
                f"() => ['account', 'email', 'x', 'space_data'].filter(t => {root}._tabShown(t))")
            assert shown == ["account", "space_data"], shown
            for tab in shown:
                pg.evaluate(f"(tab) => {root}.onTabChange(tab)", tab)
                pg.wait_for_function(f"(tab) => {root}.tab === tab", arg=tab, timeout=20000)
            # An address naming a tab of a feature they lack opens My account.
            pg.evaluate(f"() => {root}.onTabChange('x')")
            assert pg.evaluate(f"() => {root}.tab") == "account"
            # Their theme switch stays in this browser.
            pg.locator(".theme-toggle").first.click()
            pg.wait_for_timeout(1500)
        finally:
            browser.close()
            scope.set_enforced(False)
    return refused


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_members_settings_page_asks_nothing_refused(lang):
    refused = _walk(lang)
    assert refused == [], refused


def test_control_todays_shell_is_refused_the_installs_routes(monkeypatch):
    from kazma_ui import auth

    monkeypatch.setattr(auth, "install_settings_here", lambda: True)
    refused = _walk("en")
    asked = {line.split(" ", 2)[2].replace("http://127.0.0.1", "").split(":", 1)[-1] for line in refused}
    hit = [route for route in INSTALL_ROUTES if any(path.endswith(route) for path in asked)]
    assert hit, refused


def test_control_tabs_drawn_whatever_the_features_ask_x_studio(monkeypatch):
    """The X tab drawn for a person without X Studio (its panel loads itself
    with x-init): the page asks X's routes and is refused."""
    import kazma_core.spaces.features as features

    real = features.feature_here
    monkeypatch.setattr(features, "feature_here",
                        lambda feature: True if feature in ("x_studio", "email_calendar") else real(feature))
    refused = _walk_tabs_on("en")
    assert any("/api/x/" in line for line in refused), refused


def _walk_tabs_on(lang: str) -> list[str]:
    """:func:`_walk` without its own checks of the tabs shown: what the page
    asks when every tab is drawn."""
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
            pg.on("response", lambda r: refused.append(f"{r.status} {r.request.method} {r.url.split('?')[0]}")
                  if r.status in (401, 403) and "/api/" in r.url else None)
            _open_settings_by_sidebar(pg, h.base, OWN_SPACE)
            pg.wait_for_timeout(1500)
        finally:
            browser.close()
            scope.set_enforced(False)
    return refused
