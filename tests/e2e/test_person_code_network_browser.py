"""People's "Code" section, in a real browser (Spaces S4-05, S4-05b), English
and Arabic.

The admin reaches Settings by the sidebar and opens People. A person's code
runs only in a container, and with the network only where Kazma can hold it
to the public internet (its own engine, with the filter):

* where it cannot (here: another engine), the reason is shown, and Y's
  "Code may use the public internet" box is off and cannot be ticked -- a
  stored allowance shows as off too;
* where it can, the box is offered: the stored allowance shows as on; the
  admin unticks it (saved), ticks it again, reads what allowing means, and
  allows it (saved).

The admin has no row: their code keeps its own rules. The server half (the
refusals, the jail's network, a real engine) is
``tests/test_member_code_in_containers.py`` and
``tests/test_member_code_egress_engine.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import _settings_loaded, _soft_nav, _t  # noqa: E402

pytestmark = pytest.mark.real_features


def _open_people(pg, lang: str):
    _soft_nav(pg, "/settings")
    _settings_loaded(pg)
    pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
    group = pg.locator("#people-code")
    group.wait_for(state="visible", timeout=20000)
    return group


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_persons_code_network_is_offered_only_where_kazma_filters_it(lang, monkeypatch):
    from kazma_core.spaces import identity, member_code, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    refusal = {"code": "code_network_engine"}
    monkeypatch.setattr(member_code, "network_refusal", lambda: refusal["code"])
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "person-code-test-secret-" + "x" * 24
        scope.set_enforced(True)
        y = people.create_person(f"yara-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)

        def first_build_allowed(reg):
            reg["policies"].setdefault(y["id"], {})[member_code.CODE_NETWORK_POLICY] = True

        identity.update_registry(first_build_allowed)

        def allowed() -> bool:
            policy = identity.read_registry()["policies"].get(y["id"]) or {}
            return policy.get(member_code.CODE_NETWORK_POLICY) is True

        browser = p.chromium.launch()
        try:
            admin = browser.new_context()
            admin.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = admin.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)

            # Where Kazma cannot filter it: the reason, and the box off.
            group = _open_people(pg, lang)
            assert _t("settings.people_code_title", lang) in group.inner_text()
            assert pg.locator(f"[data-person-code='{OWNER_ACCOUNT_ID}']").count() == 0
            refused = pg.locator("#people-code-network-refused")
            refused.wait_for(state="visible", timeout=20000)
            assert refused.inner_text().strip() == _t("settings.people_err.code_network_engine", lang)
            row = pg.locator(f"[data-person-code='{y['id']}']")
            row.wait_for(state="visible", timeout=20000)
            box = row.locator("input[data-network-switch]")
            assert box.get_attribute("aria-label") == _t("settings.people_code_network_of", lang, name=y["name"])
            assert not box.is_checked() and box.is_disabled()

            # Where it can: the stored allowance is on; off, then on again.
            refusal["code"] = ""
            _soft_nav(pg, "/chat")
            group = _open_people(pg, lang)
            row = pg.locator(f"[data-person-code='{y['id']}']")
            row.wait_for(state="visible", timeout=20000)
            box = row.locator("input[data-network-switch]")
            pg.wait_for_function(
                "(id) => { const b = document.querySelector(`[data-person-code='${id}'] input[data-network-switch]`);"
                " return b && !b.disabled && b.checked; }", arg=y["id"], timeout=20000)
            assert not pg.locator("#people-code-network-refused").is_visible()
            with pg.expect_response(lambda r: r.url.endswith("/code-network")) as off:
                box.click()
            assert off.value.ok, off.value.text()
            pg.wait_for_function(
                "(id) => { const b = document.querySelector(`[data-person-code='${id}'] input[data-network-switch]`);"
                " return b && !b.checked; }", arg=y["id"], timeout=20000)
            assert not allowed()
            box.click()
            title = pg.locator("#modal-title")
            title.wait_for(state="visible", timeout=10000)
            dialog = pg.locator(".modal", has=title)
            assert _t("settings.people_code_network_means", lang) in " ".join(dialog.inner_text().split())
            with pg.expect_response(lambda r: r.url.endswith("/code-network")) as on:
                pg.locator(".modal-footer button", has_text=_t("settings.people_code_network_allow", lang)).first.click()
            assert on.value.ok, on.value.text()
            pg.wait_for_function(
                "(id) => { const b = document.querySelector(`[data-person-code='${id}'] input[data-network-switch]`);"
                " return b && b.checked; }", arg=y["id"], timeout=20000)
            assert allowed()
        finally:
            browser.close()
            scope.set_enforced(False)
