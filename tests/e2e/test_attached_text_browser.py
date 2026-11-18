"""A text file attached in the web chat goes beside the message, never into
it (2026-11, the review of the memory framework audit's F3), in English and
Arabic, reached by the sidebar.

The page put the file's text into the message under "[Attached file: ...]"
in a code block: the model, memory and the chat's title read it as what the
user said. It now sends the file as ``text_attachment``, the server fences it
for the model (``kazma_core/agent/attached_text.py``), and the chat shows and
keeps the user's words and the file's name -- live and after a reload.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

CASES = {
    "en": ("please keep this", "# Notes\n```python\nx = 1\n```\nMy name is Pierre and I live in Lyon.\n", "Pierre"),
    "ar": ("احتفظ بهذا الملف", "# ملاحظات\n```\nx = 1\n```\nاسمي سارة وساكنة في جدة.\n", "سارة"),
}

_LAST_USER_TEXT = """() => {
  const rows = document.querySelectorAll('.message-user .message-text');
  return rows.length ? rows[rows.length - 1].innerText : null;
}"""


@pytest.mark.parametrize("language", ["en", "ar"])
def test_an_attached_text_file_goes_beside_the_message(language: str) -> None:
    from playwright.sync_api import sync_playwright

    words, file_text, secret = CASES[language]
    with unified_turn_server(Script(steps=[], final="Noted.")) as h:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(h.base + "/dashboard", wait_until="domcontentloaded")
                page.wait_for_function("() => !!window.Alpine")
                page.click('nav a[href="/chat"], aside a[href="/chat"]', timeout=15000)
                page.wait_for_function("() => location.pathname === '/chat' && !!window.KazmaChat",
                                       timeout=20000)
                page.set_input_files("#file-input", files=[{
                    "name": "notes.md", "mimeType": "text/markdown", "buffer": file_text.encode("utf-8"),
                }])
                page.wait_for_selector('[data-remove-attach="text"]', timeout=15000)
                box = page.locator("#chat-input")
                box.fill(words)
                with page.expect_request(lambda r: "/api/chat/stream" in r.url and r.method == "POST",
                                         timeout=20000) as sent:
                    box.press("Enter")
                body = sent.value.post_data_json
                assert body["message"] == words, body
                assert body["text_attachment"]["name"] == "notes.md", body
                assert secret in body["text_attachment"]["text"] and secret not in body["message"], body
                page.wait_for_function(
                    "() => [...document.querySelectorAll('.message-assistant .message-text')]"
                    ".some(e => e.innerText.includes('Noted.'))",
                    timeout=30000,
                )
                live = page.evaluate(_LAST_USER_TEXT) or ""
                assert words in live and secret not in live, live

                session = page.evaluate("() => localStorage.getItem('kazma.chatSessionId') || ''")
                assert session
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function(f"() => {{ const t = ({_LAST_USER_TEXT})(); return !!t; }}",
                                       timeout=20000)
                stored = page.evaluate(_LAST_USER_TEXT) or ""
                assert words in stored and "notes.md" in stored and secret not in stored, stored
                assert errors == [], errors
            finally:
                browser.close()
