"""The network for one person's code on People, in a real browser (Spaces
S4-05), English and Arabic.

The admin reaches Settings by the sidebar and opens People. Y's code runs
only in a container with no network: Y's box under "Code" is off. Allowing
it asks first -- their code still runs only in a container, and what it
reads could leave the install -- and Cancel changes nothing. Allow saves it
(the identity store holds it, and the rule for Y's work says so); unticking
takes it back with no question. The admin has no row: their code keeps its
own rules.

The server half (no host fallback per space, the jail's network both ways,
E2B, a real container) is ``tests/test_member_code_in_containers.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import _settings_loaded, _soft_nav, _t  # noqa: E402

pytestmark = pytest.mark.real_features


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_allows_one_persons_code_the_network(lang):
    from kazma_core.spaces import identity, member_code, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "person-code-test-secret-" + "x" * 24
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
            group = pg.locator("#people-code")
            group.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_code_title", lang) in group.inner_text()
            # The admin's code keeps its own rules: no row of theirs.
            assert pg.locator(f"[data-person-code='{OWNER_ACCOUNT_ID}']").count() == 0

            row = pg.locator(f"[data-person-code='{y['id']}']")
            row.wait_for(state="visible", timeout=20000)
            box = row.locator("input[data-code-network]")
            assert not box.is_checked()
            assert box.get_attribute("aria-label") == _t("settings.people_code_network_of", lang, name=y["name"])

            def dialog():
                found = pg.locator(".modal-content, [role='dialog']").filter(
                    has_text=_t("settings.people_code_network_means", lang)).first
                found.wait_for(state="visible", timeout=20000)
                return found

            # Asked first; Cancel changes nothing.
            box.check()
            dialog().locator(".modal-footer button").filter(
                has_not_text=_t("settings.people_code_network_allow", lang)).first.click()
            pg.wait_for_function(
                "(id) => { const b = document.querySelector(\"[data-person-code='\" + id + \"'] input\");"
                " return b && !b.checked; }", arg=y["id"], timeout=20000)
            assert member_code.allowed_network(identity.read_registry(), y["id"]) is False

            # Allow: saved, and the rule for Y's work says so.
            box.check()
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/code-network")) as saved:
                dialog().get_by_role("button", name=_t("settings.people_code_network_allow", lang)).click()
            assert saved.value.ok, saved.value.text()
            pg.wait_for_function(
                "(id) => { const b = document.querySelector(\"[data-person-code='\" + id + \"'] input\");"
                " return b && b.checked; }", arg=y["id"], timeout=20000)
            assert member_code.allowed_network(identity.read_registry(), y["id"]) is True
            with scope.bind_scope(y["personal_space"], y["id"]):
                assert member_code.code_rule() == member_code.CodeRule(container_only=True, network=True)

            # Unticking takes it back, with no question.
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/code-network")) as saved:
                row.locator("input[data-code-network]").uncheck()
            assert saved.value.ok, saved.value.text()
            pg.wait_for_function(
                "(id) => { const b = document.querySelector(\"[data-person-code='\" + id + \"'] input\");"
                " return b && !b.checked; }", arg=y["id"], timeout=20000)
            assert member_code.allowed_network(identity.read_registry(), y["id"]) is False
        finally:
            browser.close()
            scope.set_enforced(False)
