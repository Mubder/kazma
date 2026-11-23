"""People's "Code" section, in a real browser (Spaces S4-05 and its review),
English and Arabic.

The admin reaches Settings by the sidebar and opens People. A person's code
runs only in a container with no network: Y's "Code may use the network"
box is off, cannot be switched on and says "Not yet" -- network for members'
code comes once Kazma can limit it to the public internet (S4-05b). A
stored allowance from the first build of S4-05 shows as off too. The admin
has no row: their code keeps its own rules.

The server half (the refusal, a stored allowance read as not allowed,
``--network none`` for every container, E2B never, a real container) is
``tests/test_member_code_in_containers.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import _settings_loaded, _soft_nav, _t  # noqa: E402

pytestmark = pytest.mark.real_features


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_persons_code_network_is_off_and_not_offered(lang):
    from kazma_core.spaces import identity, member_code, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "person-code-test-secret-" + "x" * 24
        scope.set_enforced(True)
        y = people.create_person(f"yara-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)

        def first_build_allowed(reg):
            reg["policies"].setdefault(y["id"], {})[member_code.CODE_NETWORK_POLICY] = True

        identity.update_registry(first_build_allowed)
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
            group = pg.locator("#people-code")
            group.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_code_title", lang) in group.inner_text()
            assert _t("settings.people_code_help", lang) in group.inner_text()
            # The admin's code keeps its own rules: no row of theirs.
            assert pg.locator(f"[data-person-code='{OWNER_ACCOUNT_ID}']").count() == 0

            row = pg.locator(f"[data-person-code='{y['id']}']")
            row.wait_for(state="visible", timeout=20000)
            box = row.locator("input[data-network-switch]")
            assert box.get_attribute("aria-label") == _t("settings.people_code_network_of", lang, name=y["name"])
            # A stored allowance from the first build reads as off, and the
            # box cannot be switched on.
            assert not box.is_checked() and box.is_disabled()
            assert _t("settings.people_code_network_not_yet", lang) in row.inner_text()
            assert member_code.allowed_network(identity.read_registry(), y["id"]) is False
        finally:
            browser.close()
            scope.set_enforced(False)
