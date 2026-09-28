"""The Dashboard's session table names each chat (2026-09-28).

Live, every row read "unknown / anonymous / 0 messages / created -": the
rows came from the checkpoints and a five-minute gateway cache, never from
the chat itself. This sends one real turn through the chat page, then reads
the Dashboard in English and in Arabic: the row carries the chat's title
(linked to it), its platform, its message count, its saved steps and when
it was last active -- in the page's language.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

QUESTION = "Plan the harbour walk"

_ROW = """() => {
  const tr = document.querySelector('#sessions-tbody tr');
  if (!tr) return null;
  const cells = Array.from(tr.querySelectorAll('td')).map((td) => td.innerText.trim());
  const link = tr.querySelector('td a');
  return {cells, href: link ? link.getAttribute('href') : '',
          titleTranslate: link ? link.getAttribute('translate') : ''};
}"""


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="Start at the lighthouse.")) as h:
        yield h


def test_the_session_table_names_the_chat(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function("() => !!window.KazmaChat", timeout=30000)
            box = pg.locator("#chat-input")
            box.fill(QUESTION)
            box.press("Enter")
            pg.wait_for_function(
                "() => { const h = document.querySelectorAll('.turn-header');"
                " return h.length && /completed/i.test(h[h.length - 1].innerText); }",
                timeout=30000,
            )

            dash = context.new_page()
            dash.goto(f"{harness.base}/dashboard", wait_until="domcontentloaded", timeout=30000)
            dash.wait_for_function(f"() => ({_ROW})() !== null", timeout=20000)
            row = dash.evaluate(_ROW)
            chat, platform, messages, steps, when, _actions = row["cells"]
            assert QUESTION in chat, row
            assert row["href"].startswith("/chat?session="), row
            assert row["titleTranslate"] == "no", "the chat's title is the user's words"
            assert platform == "Web", row
            assert messages == "2", row
            assert int(steps) >= 1, row
            assert when not in ("", "—") and "unknown" not in chat.lower(), row

            context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = context.new_page()
            ar.goto(f"{harness.base}/dashboard", wait_until="domcontentloaded", timeout=30000)
            ar.wait_for_function(f"() => ({_ROW})() !== null", timeout=20000)
            _chat, platform_ar, _m, _s, when_ar, actions_ar = ar.evaluate(_ROW)["cells"]
            arabic = "[؀-ۿ]"
            assert platform_ar == "الويب", platform_ar  # "الويب"
            assert ar.evaluate(f"(t) => /{arabic}/.test(t)", when_ar), when_ar
            assert ar.evaluate(f"(t) => /{arabic}/.test(t)", actions_ar), actions_ar
        finally:
            context.close()
            browser.close()
