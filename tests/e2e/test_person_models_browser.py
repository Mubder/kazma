"""Models per person on People, in a real browser (Spaces S2-03), English
and Arabic.

The admin reaches Settings by the sidebar, opens People, opens Y's models,
turns "Every model" off, leaves Y one model and makes it Y's default. Then
Y's own browser asks the chat's model list: only that model, as Y's
default; G's still lists every model.

The server half (the picker, /model, a job, the call) is
``tests/test_person_models.py``.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import (  # noqa: E402
    _settings_loaded,
    _soft_nav,
    _t,
    _their_page,
)

EVERY = ["deepseek:deepseek-flash", "openrouter:model-b", "openrouter:model-c"]


def _listed(_registry: Any = None, details: bool = True) -> dict[str, Any]:
    return {"default": EVERY[0], "options": [
        {"ref": ref, "provider": ref.split(":")[0], "provider_label": ref.split(":")[0],
         "model": ref.split(":", 1)[1], "local": False} for ref in EVERY]}


def _their_models(pg) -> dict[str, Any]:
    return pg.evaluate("() => fetch('/api/models/options').then(r => r.json())")


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_leaves_one_person_one_model(lang, monkeypatch):
    import kazma_core.models.options as options
    from kazma_core.spaces import people, person_models, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    monkeypatch.setattr(options, "model_options", _listed)
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "person-models-test-secret-" + "x" * 24
        scope.set_enforced(True)
        y = people.create_person(f"yara-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        g = people.create_person(f"ghada-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
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
            group = pg.locator("#people-models")
            group.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_models_title", lang) in group.inner_text()
            assert pg.locator(f"[data-person-models='{OWNER_ACCOUNT_ID}']").count() == 0

            row = f"[data-person-models='{y['id']}']"
            pg.locator(f"{row} summary").click()
            every = pg.locator(f"{row} input[data-models-every]")
            assert every.is_checked()
            assert every.get_attribute("aria-label") == _t("settings.people_models_every_of", lang, name=y["name"])
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/models")) as saved:
                every.uncheck()
            assert saved.value.ok, saved.value.text()
            for ref in ("deepseek:deepseek-flash", "openrouter:model-c"):
                box = pg.locator(f"{row} input[data-model='{ref}']")
                box.wait_for(state="visible", timeout=20000)
                with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/models")) as saved:
                    box.uncheck()
                assert saved.value.ok, saved.value.text()
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/models")) as saved:
                pg.locator(f"{row} select[data-models-default]").select_option("openrouter:model-b")
            assert saved.value.ok, saved.value.text()
            pg.wait_for_function(
                "(id) => { const s = document.querySelector(\"[data-person-models='\" + id + \"'] select[data-models-default]\");"
                " return s && s.value === 'openrouter:model-b'; }", arg=y["id"], timeout=20000)

            from kazma_core.spaces import identity

            kept = person_models._models_of(identity.read_registry(), y["id"])
            assert kept.allowed == {"openrouter:model-b"} and kept.default == "openrouter:model-b"

            # Y's own picker: that model, as Y's default. G's: every model.
            yours = _their_models(_their_page(browser, h.base, lang, y))
            assert [o["ref"] for o in yours["options"]] == ["openrouter:model-b"], yours
            assert yours["default"] == "openrouter:model-b"
            theirs = _their_models(_their_page(browser, h.base, lang, g))
            assert [o["ref"] for o in theirs["options"]] == EVERY, theirs
        finally:
            browser.close()
            scope.set_enforced(False)
