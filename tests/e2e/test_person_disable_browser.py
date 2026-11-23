"""Disabling and enabling a person on People, in a real browser (Spaces
S2-07), English and Arabic.

The admin reaches Settings by the sidebar, opens People, disables Y (the
confirm says their work waits), and Y's row says "Disabled" with Enable in
place of Disable; the identity store holds it and Y's work is refused by
every door. Enabled again, the row is as it was.

The server half (a paused scheduled job, the reason, the other doors, the
control of the tools frozen at booking) is
``tests/test_background_uses_creator_policy.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import _settings_loaded, _soft_nav, _t  # noqa: E402

pytestmark = pytest.mark.real_features


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_disables_and_enables_a_person(lang):
    from kazma_core.spaces import features, identity, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "person-disable-test-secret-" + "x" * 24
        scope.set_enforced(True)
        y = people.create_person(f"yara-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        browser = p.chromium.launch()
        try:
            admin = browser.new_context()
            admin.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = admin.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            row = pg.locator(f"[data-person='{y['id']}']")
            row.wait_for(state="visible", timeout=20000)
            assert not row.locator("[data-person-disabled]").is_visible()
            assert pg.locator(f"[data-person='{OWNER_ACCOUNT_ID}'] [data-person-disable]").count() == 0

            disable = row.locator("[data-person-disable]")
            assert disable.get_attribute("aria-label") == _t("settings.people_disable_of", lang, name=y["name"])
            disable.click()
            dialog = pg.locator(".modal-content, [role='dialog']").filter(
                has_text=_t("settings.people_disable_confirm", lang)).first
            dialog.wait_for(state="visible", timeout=20000)
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/enabled")) as saved:
                dialog.get_by_role("button", name=_t("settings.people_disable", lang)).click()
            assert saved.value.ok, saved.value.text()
            row.locator("[data-person-disabled]").wait_for(state="visible", timeout=20000)
            assert _t("settings.people_disabled", lang) in row.inner_text()
            assert row.locator("[data-person-enable]").is_visible()
            assert y["id"] in identity.disabled_accounts()
            assert features.work_refusal("research", y["id"])

            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/enabled")) as saved:
                row.locator("[data-person-enable]").click()
            assert saved.value.ok, saved.value.text()
            row.locator("[data-person-disabled]").wait_for(state="hidden", timeout=20000)
            assert y["id"] not in identity.disabled_accounts()
            assert features.work_refusal("research", y["id"]) is None
        finally:
            browser.close()
            scope.set_enforced(False)
