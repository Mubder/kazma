"""Stronger sign-in in a real browser (Spaces S4-02), English and Arabic.

The admin reaches Settings by the sidebar, opens People and makes an
invitation link. A guest opens the link in their own browser, chooses a name
and a password, and signs in on the sign-in page. They reach Settings by the
sidebar, open My account and turn on a second factor: the key is shown once,
a code from it turns it on, and the recovery codes are shown once. In a new
browser the sign-in page then asks for a code after the password, and the
code signs them in. Back on People, the admin opens the person's sessions
(two: one per sign-in) and ends one.

Negative control: ``tests/test_sign_in_hardening.py`` (a password alone
signing in when a second factor is required).
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

pytestmark = pytest.mark.real_sign_in

PASSWORD = "a-long-enough-password"


def _soft_nav(pg, href: str) -> None:
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _settings_loaded(pg) -> None:
    pg.locator("#settings-root").wait_for(state="attached", timeout=30000)
    pg.wait_for_function(
        "() => { const el = document.querySelector('#settings-root');"
        " return el && el._x_dataStack && !el._x_dataStack[0].loading; }", timeout=30000)


def _t(key: str, lang: str, **values: str) -> str:
    from kazma_ui.i18n import t

    return t(key, lang, **values)


def _code(secret: str, step_offset: int = 0) -> str:
    from kazma_core.security import totp

    return totp._code_at(secret, int(time.time() // totp.PERIOD) + step_offset)


def _open_tab(pg, key: str, lang: str) -> None:
    pg.locator(".settings-tab", has_text=_t(key, lang)).first.click()


def _sign_in(page, base: str, name: str) -> None:
    page.goto(f"{base}/login", wait_until="domcontentloaded")
    page.fill("#username", name)
    page.fill("#password", PASSWORD)
    page.click("#btnUser")


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_an_invitation_a_second_factor_and_a_session_ended(lang):
    from kazma_core.security.vault import reset_vault
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    name = f"huda-{lang}"
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "sign-in-hardening-test-secret-" + "x" * 24
        # A second factor is kept in the vault: this server has one, in its
        # own data folder.
        had_key = os.environ.get("KAZMA_VAULT_KEY")
        os.environ["KAZMA_VAULT_KEY"] = "1e" * 32
        reset_vault()
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            # ── The admin makes an invitation on People ──────────────────
            admin = browser.new_context()
            admin.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = admin.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            _open_tab(pg, "settings.tab_people", lang)
            pg.locator("#people-invitations").wait_for(state="visible", timeout=20000)
            assert _t("sign_in.invitations_title", lang) in pg.locator("#people-invitations h3").inner_text()
            pg.fill("#invite-name", "Huda")
            pg.fill("#invite-hours", "2")
            pg.click("#invite-make")
            link = pg.locator("[data-invite-link]")
            link.wait_for(state="visible", timeout=20000)
            url = link.inner_text().strip()
            assert "/invite/" in url
            pg.locator("[data-invitation-status='open']").first.wait_for(state="visible", timeout=10000)

            # ── The guest opens it and makes an account ──────────────────
            guest = browser.new_context()
            guest.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            gp = guest.new_page()
            gp.goto(url, wait_until="domcontentloaded")
            assert gp.locator("h1").inner_text().strip() == _t("invite.title", lang)
            gp.fill("#name", name)
            gp.fill("#password", PASSWORD)
            gp.fill("#confirm", PASSWORD)
            gp.click("#btnInvite")
            gp.locator("#invite-done").wait_for(state="visible", timeout=20000)
            assert _t("invite.done", lang) in gp.locator("#invite-done").inner_text()

            # ── This machine: no auto-login now that a second person has an
            # account; the sign-in page says why ────────────────────────
            local = browser.new_context()
            local.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            lp = local.new_page()
            lp.goto(f"{h.base}/chat", wait_until="domcontentloaded")
            lp.wait_for_function("() => location.pathname === '/login'", timeout=20000)
            note = lp.locator("#everyone-signs-in")
            note.wait_for(state="visible", timeout=20000)
            assert note.inner_text().strip() == _t("login.everyone_signs_in", lang)

            # ── They sign in, and turn on a second factor ────────────────
            gp.click("#invite-sign-in")
            gp.wait_for_function("() => location.pathname === '/login'", timeout=20000)
            gp.fill("#username", name)
            gp.fill("#password", PASSWORD)
            gp.click("#btnUser")
            gp.wait_for_function("() => location.pathname !== '/login'", timeout=20000)
            gp.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(gp, "/settings")
            _settings_loaded(gp)
            _open_tab(gp, "settings.tab_account", lang)
            panel = gp.locator("#second-factor")
            panel.wait_for(state="visible", timeout=20000)
            assert _t("sign_in.title", lang) in panel.locator("h3").inner_text()
            gp.click("#second-factor-begin")
            key = gp.locator("[data-second-factor-secret]")
            key.wait_for(state="visible", timeout=20000)
            secret = key.inner_text().strip()
            gp.fill("#second-factor-code", _code(secret))
            gp.click("#second-factor-confirm")
            gp.locator("[data-recovery-codes]").wait_for(state="visible", timeout=20000)
            assert gp.locator("[data-recovery-code]").count() == 10
            gp.click("#second-factor-codes-kept")
            gp.locator("[data-second-factor-on]").wait_for(state="visible", timeout=20000)

            # ── A new browser: the password, then the code ───────────────
            fresh = browser.new_context()
            fresh.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            fp = fresh.new_page()
            _sign_in(fp, h.base, name)
            fp.locator("#mode-code").wait_for(state="visible", timeout=20000)
            assert _t("login.code_help", lang) in fp.locator("#mode-code").inner_text()
            assert fp.evaluate("() => location.pathname") == "/login", "a password alone signed in"
            fp.fill("#code", _code(secret, 1))
            fp.click("#btnCode")
            fp.wait_for_function("() => location.pathname !== '/login'", timeout=20000)

            # ── The admin sees their two sessions and ends one ───────────
            pg.reload(wait_until="domcontentloaded")
            _settings_loaded(pg)
            _open_tab(pg, "settings.tab_people", lang)
            person = pg.evaluate("(n) => fetch('/api/people').then(r => r.json())"
                                 ".then(d => d.people.find(p => p.name === n))", name)
            assert person["second_factor"] is True
            pg.locator(f"[data-person-sessions-toggle='{person['id']}']").click()
            listed = pg.locator(f"[data-person-session-list='{person['id']}'] [data-person-session]")
            listed.first.wait_for(state="visible", timeout=20000)
            assert listed.count() == 2
            listed.first.locator("button", has_text=_t("sign_in.end", lang)).click()
            pg.locator(".modal-footer button", has_text=_t("sign_in.end", lang)).first.click()
            pg.wait_for_function(
                "(id) => document.querySelectorAll("
                "'[data-person-session-list=\"' + id + '\"] [data-person-session]').length === 1",
                arg=person["id"], timeout=20000)
            assert pg.locator(f"[data-person-second-factor='{person['id']}'] .badge-success").is_visible()
        finally:
            browser.close()
            scope.set_enforced(False)
            if had_key is None:
                os.environ.pop("KAZMA_VAULT_KEY", None)
            else:
                os.environ["KAZMA_VAULT_KEY"] = had_key
            reset_vault()
