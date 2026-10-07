"""A turn sent after reaching Chat from another page paints live.

Live 2026-10-03 13:19-13:22 UTC: the owner opened the Memory page, clicked
Chat in the sidebar, and sent a task. The page showed nothing -- no thinking
header, no steps, no approval card -- while the server ran the turn and
paused it for approval; a refresh showed all of it. The sidebar click is a
soft navigation (``modules/nav.js``): it swaps the page and re-runs the
incoming page's scripts, and it skipped every script under
``/static/js/modules/``. Chat keeps its turn machinery there
(``turn_document.js``, ``turn_view.js``, ``turn_presentation.js`` ...), and
``chat.js`` declines quietly when one is missing, so nothing painted and
nothing logged an error. Every other browser test loads ``/chat`` directly.

A shared script loads once per version: arriving at Chat again does not run
it again (``turn_visibility.js`` binds a document listener).
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, Step, unified_turn_server  # noqa: E402

#: What Chat's turn machinery defines on window (chat.html's modules/ scripts).
CHAT_MODULE_GLOBALS = (
    "KazmaDeliveryCursor", "KazmaTurnDocument", "KazmaTurnView",
    "KazmaTurnPreferences", "KazmaTurnPresentation", "KazmaTurnVisibility",
    "KazmaPushClient",
)

CARD_JS = (
    "() => document.querySelectorAll("
    "'.turn-approvals-rows .hitl-approval-card button:not([disabled])').length"
)


def _soft_nav(pg, href: str) -> None:
    """Follow a sidebar link the way a person does: a click nav.js handles."""
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def test_a_turn_sent_after_reaching_chat_from_another_page_paints_live(tmp_path):
    from playwright.sync_api import sync_playwright

    script = Script(steps=[Step(tool="file_write",
                                args={"path": str(tmp_path / "out.txt"), "content": "x"},
                                narration="Writing the file.")])
    with unified_turn_server(script) as h, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            pg = browser.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda message: errors.append(message.text)
                  if "[soft-nav] i18n refresh failed" in message.text else None)
            pg.goto(f"{h.base}/memory", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.Alpine", timeout=30000)
            pg.wait_for_timeout(1500)
            _soft_nav(pg, "/chat")
            pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
            pg.wait_for_function("() => !!window.KazmaChat", timeout=20000)
            # Arrived by soft navigation, not a page load.
            assert pg.evaluate("() => performance.getEntriesByType('navigation').length") == 1
            assert pg.evaluate(
                "() => performance.getEntriesByType('navigation')[0].name"
            ).endswith("/memory")

            missing = pg.evaluate(
                "(names) => names.filter((n) => !window[n])", list(CHAT_MODULE_GLOBALS))
            assert missing == [], f"Chat's turn modules not loaded: {missing}"

            pg.fill("#chat-input", "please write the file")
            pg.evaluate("() => window.KazmaChat.sendMessage()")
            # The live block, then the approval card, with no reload.
            pg.wait_for_function(
                "() => !!document.querySelector('.message-assistant .turn-header')",
                timeout=20000)
            pg.wait_for_function(CARD_JS, timeout=60000)

            # Away and back: each shared script ran exactly once.
            _soft_nav(pg, "/memory")
            pg.wait_for_timeout(1000)
            _soft_nav(pg, "/chat")
            pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
            runs = pg.evaluate(
                "() => performance.getEntriesByType('resource')"
                ".filter((r) => /\\/static\\/js\\/modules\\/turn_view\\.js/.test(r.name)).length")
            assert runs == 1, f"turn_view.js fetched {runs} times"
            assert errors == [], errors
        finally:
            browser.close()
