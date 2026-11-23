"""Linking a Telegram account from My account, in a real browser (Spaces
S3-01), English and Arabic.

A person signs in with their own name and password, reaches Settings by the
sidebar and opens My account. "Link Telegram" shows a code once, what to send
the bot and where to find it. The code is sent to the real Telegram adapter's
update path in a direct chat; the page, which reads the links again while the
code is shown, says the account is linked, and the bot's answer is in the
page's language.

The server half is ``tests/test_chat_link_flow.py``.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

PASSWORD = "a-long-enough-password"
TG_USER = 555901


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


def _send_to_the_bot(adapter: Any, text: str) -> list[Any]:
    """The code, as Telegram delivers a direct message to the adapter."""
    sent: list[Any] = []

    async def record(outbound: Any) -> bool:
        sent.append(outbound)
        return True

    adapter.send = record
    update = {"update_id": 1, "message": {
        "message_id": 1, "chat": {"id": TG_USER, "type": "private"},
        "from": {"id": TG_USER, "is_bot": False, "first_name": "D"}, "date": 1, "text": text,
    }}

    async def run() -> None:
        await adapter._process_update(update, asyncio.Queue())
        for _ in range(400):
            await asyncio.sleep(0.02)
            if not any(t.get_name().endswith("-chat-link") for t in asyncio.all_tasks() if not t.done()):
                return
        raise AssertionError("the link flow never finished")

    # Playwright's sync API runs a loop in this thread: the bot gets its own.
    failed: list[BaseException] = []

    def in_its_own_thread() -> None:
        try:
            asyncio.run(run())
        except BaseException as exc:  # handed back to the test
            failed.append(exc)

    worker = threading.Thread(target=in_its_own_thread)
    worker.start()
    worker.join(timeout=60)
    if failed:
        raise failed[0]
    return sent


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_person_links_telegram_from_my_account(lang):
    from kazma_core.service_container import get_container
    from kazma_core.spaces import chat_links, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    from kazma_gateway.adapters.telegram import TelegramAdapter
    from kazma_gateway.gateway import GatewayManager

    name = f"dana-link-{lang}"
    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "chat-link-test-secret-" + "x" * 24
        scope.set_enforced(True)
        chat_links._forget_attempts_for_tests()
        container = get_container()
        gateway = container.get(GatewayManager) if container.has(GatewayManager) else None
        token = None
        if gateway is None:
            gateway = GatewayManager()
            token = container.register(GatewayManager, gateway)
        adapter = TelegramAdapter(token="123:abc")
        adapter._bot_username = "kazma_link_test_bot"

        async def nothing(*args: Any, **kwargs: Any) -> None:
            return None

        adapter._announce_if_added_to_group = nothing
        adapter._set_reaction = nothing
        adapter._running = True
        gateway.add_adapter(adapter)
        person = people.create_person(name, PASSWORD, by=OWNER_ACCOUNT_ID)
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            signed_in = ctx.request.post(f"{h.base}/api/auth/login",
                                         data={"username": name, "password": PASSWORD})
            assert signed_in.ok, signed_in.text()
            pg = ctx.new_page()
            # This item's routes only: a member's Settings page asks some of
            # the install's own routes too, refused as before (not this item's).
            pg.on("response", lambda r: refused.append(f"{r.status} {r.url}")
                  if r.status >= 400 and "/api/account/chat-links" in r.url else None)
            pg.goto(f"{h.base}/chat", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_account", lang)).click()

            panel = pg.locator("#chat-links")
            panel.wait_for(state="visible", timeout=20000)
            assert _t("chat_links.title", lang) in panel.inner_text()
            pg.locator("#chat-links .empty-state", has_text=_t("chat_links.none", lang)).wait_for(
                state="visible", timeout=20000)
            link = pg.locator("[data-link-platform='telegram']")
            link.wait_for(state="visible", timeout=20000)
            assert link.inner_text().strip() == _t("chat_links.link", lang, platform="Telegram")
            with pg.expect_response(lambda r: r.url.endswith("/api/account/chat-links/code")) as issued:
                link.click()
            assert issued.value.ok, issued.value.text()
            shown = pg.locator("[data-chat-link-code]")
            shown.wait_for(state="visible", timeout=20000)
            send = shown.inner_text().strip()
            assert send.startswith("/link ") and len(send) == len("/link ABCD-2345"), send
            box = pg.locator("[data-chat-link-code-box]").inner_text()
            assert _t("chat_links.send_hint", lang, platform="Telegram") in box
            assert pg.locator("[data-chat-link-code-box] a[href='https://t.me/kazma_link_test_bot']").is_visible()

            answer = _send_to_the_bot(adapter, send)
            assert len(answer) == 1
            assert ("Linked" if lang == "en" else "تمّ الربط") in answer[0].text
            assert name in answer[0].text

            pg.locator(f"[data-chat-link='telegram:{TG_USER}']").wait_for(state="visible", timeout=20000)
            done = pg.locator("[data-chat-link-done]")
            done.wait_for(state="visible", timeout=20000)
            assert done.inner_text().strip() == _t(
                "chat_links.done", lang, platform="Telegram", id=str(TG_USER))
            assert not pg.locator("[data-chat-link-code-box]").is_visible(), "the code is gone once used"
            assert chat_links._link_of("telegram", TG_USER) == person["id"]
        finally:
            browser.close()
            if adapter in gateway.adapters:
                gateway.adapters.remove(adapter)
            if token is not None:
                container.release(GatewayManager, token)
            scope.set_enforced(False)
    assert refused == [], refused
