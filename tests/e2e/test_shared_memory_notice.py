"""The shared-memory notice, in a real browser (Spaces S1-11, decision N35).

In a shared space every member's facts and turns are recalled for every
member, each under its owner's name, so the space says so before anyone types
something private: "Memory here is shared with ..." near the chat's composer
and on the Memory page, with the other members' names, in English and Arabic.
The page is reached by the sidebar (soft navigation runs the page scripts
again). A personal space shows no notice.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

TEAM = "team-shared-notice"
NOTICE = "[data-shared-memory-notice]"


def _soft_nav(pg, href: str) -> None:
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _a_shared_space() -> None:
    """The owner's shared space with Omar in it, Spaces enforced (sign-in on:
    the loopback auto-login is the owner's account)."""
    import os

    from kazma_core.spaces import identity, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    os.environ["KAZMA_SECRET"] = "shared-notice-test-secret-" + "x" * 24

    def omar(reg):
        reg["accounts"]["omar"] = {"id": "omar", "name": "Omar", "role": "member", "enabled": True}

    identity.update_registry(omar)
    identity.create_space("Shared notice team", created_by=OWNER_ACCOUNT_ID, space_id=TEAM)
    identity.add_member(TEAM, "omar")
    scope.set_enforced(True)


def _notice(pg) -> str:
    pg.wait_for_function(f"() => !document.querySelector('{NOTICE}').hidden", timeout=20000)
    return pg.evaluate(f"() => document.querySelector('{NOTICE}').textContent")


@pytest.mark.parametrize("lang,shared_words", [("en", "Memory here is shared with"),
                                               ("ar", "الذاكرة هنا مشتركة مع")])
def test_a_shared_space_says_whose_memory_it_is(lang, shared_words):
    from kazma_core.spaces import scope
    from kazma_core.spaces.scope import OWNER_SPACE_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        _a_shared_space()
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = context.new_page()
            pg.goto(f"{h.base}/chat?space={TEAM}", wait_until="domcontentloaded")
            pg.goto(f"{h.base}/settings?space={TEAM}", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            # Chat, by the sidebar: the notice near the composer.
            _soft_nav(pg, "/chat")
            text = _notice(pg)
            assert shared_words in text and "Omar" in text, text
            assert pg.evaluate(f"() => !!document.querySelector('{NOTICE} bdi[translate=no]')")
            # The Memory page, by the sidebar.
            _soft_nav(pg, "/memory")
            text = _notice(pg)
            assert shared_words in text and "Omar" in text, text
            # The owner's personal space: no notice.
            own = context.new_page()
            own.goto(f"{h.base}/chat?space={OWNER_SPACE_ID}", wait_until="domcontentloaded")
            own.goto(f"{h.base}/settings?space={OWNER_SPACE_ID}", wait_until="domcontentloaded")
            own.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(own, "/chat")
            own.wait_for_function(
                "() => document.documentElement.getAttribute('data-shared-memory-state') === 'none'",
                timeout=20000)
            assert own.evaluate("() => document.querySelector('.chat-input-area [data-shared-memory-notice]').hidden")
        finally:
            browser.close()
            scope.set_enforced(False)
