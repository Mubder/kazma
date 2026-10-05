"""The attended Studio retains drafts and fits narrow English/Arabic layouts."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402
from tests.e2e.test_pages_load_clean import _settle, page_problems  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def test_composer_survives_reload_and_mobile_bilingual_layout():
    from playwright.sync_api import sync_playwright

    with unified_turn_server() as harness, sync_playwright() as playwright:
        from kazma_core.config_store import get_config_store

        get_config_store().set("connectors.x.reply.subjects", [{"schema_version": 2,
            "id": "coffee", "target": "Coffee sourcing", "match": ["coffee"],
            "side": "support", "mood": "supportive", "allow_draft": True,
            "scope": "Commercial sourcing", "exceptions": ["Concede verified harm"],
            "allowed_moods": ["dry", "supportive"]}, {"schema_version": 2,
            "id": "tea", "target": "Tea sourcing", "match": ["tea"],
            "side": "against", "mood": "dry", "allow_draft": True}])
        browser = playwright.chromium.launch(headless=True)
        host = harness.base.split("//", 1)[1].split(":", 1)[0]
        try:
            for language, text in (("en", "A saved opinion about coffee."), ("ar", "رأي محفوظ عن القهوة العربية.")):
                context = browser.new_context(viewport={"width": 375, "height": 850})
                context.add_cookies([{"name": "kazma-lang", "value": language, "domain": host, "path": "/"}])
                try:
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(harness.base + "/x", wait_until="domcontentloaded")
                    _settle(page)
                    page.wait_for_function("() => Alpine.$data(document.querySelector('.xs-wrap'))._composerLoaded")
                    for theme in ("dark", "light"):
                        if page.evaluate("() => document.documentElement.dataset.theme") != theme:
                            page.locator(".theme-toggle:not(.lang-toggle)").click()
                        page.wait_for_function("theme => document.documentElement.dataset.theme === theme", arg=theme)
                        for selector in ("#xs-brief", "#xs-draft-count", "#xs-text", "#xs-reply", "#xs-when", "#xs-draft-search", "#xs-operation-search"):
                            assert page.locator(selector).evaluate("e => {const s=getComputedStyle(e); return s.borderTopStyle === 'solid' && parseFloat(s.paddingLeft) >= 10 && parseFloat(s.minHeight) >= 42;}")
                        finished = page.locator("#xs-show-finished")
                        finished.focus()
                        page.keyboard.press("Space")
                        assert finished.is_checked()
                        assert finished.evaluate("e => {const s=getComputedStyle(e); return s.appearance === 'none' && s.backgroundColor === s.borderTopColor && s.outlineStyle === 'solid';}")
                        page.keyboard.press("Space")
                        assert not finished.is_checked()
                        page.locator("#xs-tab-conversations").click()
                        page.locator("#xs-conversation-state").select_option("posted")
                        page.wait_for_function("() => !Alpine.$data(document.querySelector('.xs-wrap')).convLoading")
                        for selector in ("#xs-conversation-search", "#xs-conversation-state", "#xs-conversation-side", "#xs-conversation-mood"):
                            assert page.locator(selector).evaluate("e => {const s=getComputedStyle(e); return s.borderTopStyle === 'solid' && parseFloat(s.paddingLeft) >= 10;}")
                        assert page.locator("#xs-conversation-state").input_value() == "posted"
                        page.locator("#xs-conversation-side").select_option("support")
                        page.locator("#xs-conversation-mood").select_option("professional")
                        page.wait_for_function("() => !Alpine.$data(document.querySelector('.xs-wrap')).convLoading")
                        assert page.locator("#xs-conversation-side").input_value() == "support"
                        assert page.locator("#xs-conversation-mood").input_value() == "professional"
                        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                        page.locator("#xs-conversation-state").select_option("")
                        page.locator("#xs-conversation-side").select_option("")
                        page.locator("#xs-conversation-mood").select_option("")
                        page.locator("#xs-tab-studio").click()
                    page.locator("#xs-text").fill(text)
                    page.wait_for_function("() => {const s = Alpine.$data(document.querySelector('.xs-wrap')); return s.text && !s._composerSaving && s._composerSignature() === s._composerSaved;}")
                    page.reload(wait_until="domcontentloaded")
                    _settle(page)
                    page.wait_for_function("() => Alpine.$data(document.querySelector('.xs-wrap'))._composerLoaded")
                    assert page.locator("#xs-text").input_value() == text
                    assert page.evaluate("() => document.documentElement.dir") == ("rtl" if language == "ar" else "ltr")
                    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                    assert not page_problems(page, "/x", errors)
                    page.locator("#xs-draft-search").fill("coffee")
                    page.wait_for_function("() => !Alpine.$data(document.querySelector('.xs-wrap')).loadStates.drafts.loading")
                    page.locator("#xs-tab-studio").focus()
                    page.keyboard.press("End")
                    assert page.locator("#xs-tab-datasets").evaluate("element => element === document.activeElement")
                    page.keyboard.press("ArrowRight" if language == "ar" else "ArrowLeft")
                    assert page.locator("#xs-tab-threads").evaluate("element => element === document.activeElement")
                    assert page.locator("#xs-tab-threads").get_attribute("aria-selected") == "true"
                    page.locator("#xs-thread-0").fill(text)
                    page.locator("#xs-thread-1").fill(text + " 2")
                    down = "Move down" if language == "en" else "نقل للأسفل"
                    page.get_by_role("button", name=down, exact=True).first.focus()
                    page.keyboard.press("Space")
                    page.wait_for_function("() => {const s = Alpine.$data(document.querySelector('.xs-wrap')); return !s._composerSaving && s._composerSignature() === s._composerSaved;}")
                    page.reload(wait_until="domcontentloaded")
                    _settle(page)
                    page.wait_for_function("() => Alpine.$data(document.querySelector('.xs-wrap'))._composerLoaded")
                    assert "{count}" not in page.locator(".xs-meta").first.inner_text()
                    page.locator("#xs-tab-studio").focus()
                    page.keyboard.press("End")
                    page.keyboard.press("ArrowRight" if language == "ar" else "ArrowLeft")
                    assert page.locator("#xs-thread-0").input_value() == text + " 2"
                    assert page.locator("#xs-thread-1").input_value() == text
                    page.keyboard.press("Home")
                    assert page.locator("#xs-tab-studio").evaluate("element => element === document.activeElement")
                    health = "Operations health" if language == "en" else "صحة التشغيل"
                    page.locator("summary").filter(has_text=health).click()
                    page.wait_for_function("() => Alpine.$data(document.querySelector('.xs-wrap')).health !== null")
                    assert not page.evaluate("() => Alpine.$data(document.querySelector('.xs-wrap')).healthError")
                    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                    assert not page_problems(page, "/x", errors)
                    page.locator("#xs-tab-datasets").click()
                    page.locator("#xd-name").fill("Browser annotation " + language)
                    page.get_by_role("button", name="Create Collection" if language == "en" else "إنشاء مجموعة", exact=True).click()
                    page.locator("#xd-search").wait_for()
                    page.get_by_role("button", name="Add Real Case" if language == "en" else "إضافة حالة حقيقية", exact=True).click()
                    colors = []
                    for theme in ("dark", "light"):
                        if page.evaluate("() => document.documentElement.dataset.theme") != theme:
                            page.locator(".theme-toggle:not(.lang-toggle)").click()
                        page.wait_for_function("theme => document.documentElement.dataset.theme === theme", arg=theme)
                        check = page.locator(".xd-categories input").first
                        check.focus()
                        page.keyboard.press("Space")
                        assert check.is_checked()
                        checked = check.evaluate("e => {const s = getComputedStyle(e); return {appearance:s.appearance, background:s.backgroundColor, border:s.borderTopColor, outline:s.outlineStyle, tick:getComputedStyle(e, '::before').opacity};}")
                        assert checked["appearance"] == "none"
                        assert checked["background"] == checked["border"]
                        assert checked["outline"] == "solid"
                        assert checked["tick"] == "1"
                        page.keyboard.press("Space")
                        assert not check.is_checked()
                        assert check.evaluate("e => getComputedStyle(e).backgroundColor") != checked["background"]
                        colors.append(page.locator("#xd-source-text").evaluate("e => getComputedStyle(e).backgroundColor"))
                        assert page.locator("#xd-import").evaluate("e => getComputedStyle(e, '::file-selector-button').borderTopColor") == checked["border"]
                        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                    assert colors[0] != colors[1]
                    page.locator("#xd-source-text").fill(text)
                    page.locator("#xd-source-id").fill("browser-source-" + language)
                    page.locator("#xd-language").select_option(language)
                    page.locator("#xd-rationale").fill("Original context saved; human labels still pending.")
                    assert page.locator(".xd-editor").evaluate("form => form.checkValidity()"), page.locator(".xd-editor").evaluate("form => Array.from(form.elements).filter(e => !e.validity.valid).map(e => [e.id, e.value, e.validationMessage])")
                    assert not errors, errors
                    with page.expect_response(lambda r: r.url.endswith("/case") and r.request.method == "PUT") as saved:
                        page.get_by_role("button", name="Save Case" if language == "en" else "حفظ الحالة", exact=True).click()
                    assert saved.value.status == 200, saved.value.text()
                    page.locator(".xd-case").first.wait_for()
                    page.reload(wait_until="domcontentloaded")
                    _settle(page)
                    assert page.locator("#xs-tab-datasets").get_attribute("aria-selected") == "true"
                    page.locator(".xd-collection").filter(has_text="Browser annotation " + language).click()
                    page.locator(".xd-case").first.click()
                    assert page.locator("#xd-source-text").input_value() == text
                    assert not page.locator("#xd-reviewed").is_checked()
                    assert page.locator("#xd-auto").input_value() == ""
                    page.locator("#xd-notes").fill("Reviewed context only; labels still pending.")
                    page.get_by_role("button", name="Save Case" if language == "en" else "حفظ الحالة", exact=True).click()
                    page.locator(".xd-case").first.wait_for()
                    assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
                    assert not page_problems(page, "/x", errors)
                    page.goto(harness.base + "/settings?tab=x", wait_until="domcontentloaded")
                    _settle(page)
                    page.get_by_role("button", name="Coffee sourcing", exact=True).click()
                    page.locator("#xr-mood-0").wait_for()
                    assert page.locator("#xr-mood-0").input_value() == "supportive"
                    assert page.locator("#xr-side-0").input_value() == "support"
                    assert not page.locator("#xr-id-0").is_visible()
                    page.get_by_role("button", name="Advanced subject settings" if language == "en" else "إعدادات الموضوع المتقدمة", exact=True).click()
                    page.locator("#xr-scope-0").wait_for(state="visible")
                    assert page.locator("#xr-scope-0").is_visible()
                    assert page.locator("#xr-scope-0").input_value() == "Commercial sourcing"
                    assert page.locator("#xr-tones-0").evaluate("e => Array.from(e.selectedOptions).map(o => o.value)") == ["dry", "supportive"]
                    page.locator(".x-policy-card").first.locator("details summary").click()
                    draft_permission = page.locator(".x-policy-card input[x-model='s.allow_draft']").first
                    assert draft_permission.is_checked()
                    assert draft_permission.evaluate("e => {const s=getComputedStyle(e); return s.appearance === 'none' && s.backgroundColor === s.borderTopColor;}")
                    assert page.locator(".x-controls .toggle input").first.evaluate("e => getComputedStyle(e).width") == "0px"
                    page.get_by_role("button", name="Tea sourcing", exact=True).click()
                    page.locator("#xr-mood-1").wait_for()
                    assert page.locator("#xr-mood-1").input_value() == "dry"
                    assert page.locator("#xr-side-1").input_value() == "against"
                    language_freedom = page.locator("#xr-style-global-uncensored")
                    assert not language_freedom.is_checked()
                    language_freedom.focus()
                    page.keyboard.press("Space")
                    assert language_freedom.is_checked()
                    page.locator("#xr-style-global-profanity").select_option("strong")
                    language_freedom.uncheck()
                    assert page.locator("#xr-style-global-profanity").input_value() == "none"
                    target = "Browser policy " + language
                    page.get_by_role("button", name="+ Add subject" if language == "en" else "+ إضافة موضوع", exact=True).click()
                    # The new card asks for a human target; its stable ID stays advanced.
                    card = page.locator(".x-policy-card").last
                    card.locator("input[id^='xr-target-']").fill(target)
                    card.locator("select[id^='xr-side-']").select_option("support")
                    card.locator("select[id^='xr-mood-']").select_option("professional")
                    assert not card.locator("input[id^='xr-id-']").is_visible()
                    card.get_by_text("Customize language for this subject" if language == "en" else "تخصيص اللغة لهذا الموضوع", exact=True).click()
                    card.locator("select[id$='-language']").select_option(language)
                    with page.expect_response(lambda response: response.url.endswith("/api/x/reply") and response.request.method == "PUT") as policy_saved:
                        page.get_by_role("button", name="Save auto-reply" if language == "en" else "حفظ الرد التلقائي", exact=True).click()
                    assert policy_saved.value.status == 200, policy_saved.value.text()
                    saved_card = next(s for s in policy_saved.value.json()["subjects"] if s["target"] == target)
                    assert saved_card["match"] == [target]
                    assert saved_card["reply_style"]["language"] == language
                    assert not saved_card["allow_auto"]
                    page.reload(wait_until="domcontentloaded")
                    _settle(page)
                    page.get_by_role("button", name=target, exact=True).click()
                    card = page.locator(".x-policy-card").filter(has=page.get_by_role("button", name=target, exact=True))
                    assert card.locator("select[id$='-language']").input_value() == language
                    assert card.locator("input[id^='xr-target-']").input_value() == target
                    preserved = get_config_store().get("connectors.x.reply.subjects")[0]
                    assert preserved["scope"] == "Commercial sourcing"
                    assert preserved["allowed_moods"] == ["dry", "supportive"]
                    assert not page_problems(page, "/settings?tab=x", errors)
                finally:
                    context.close()
        finally:
            browser.close()
