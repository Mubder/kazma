"""A reply given without the model closes its turn at once (2026-09-28).

``/replay`` typed in the live web chat painted its answer, and the turn's
header kept saying "Kazma is thinking..." for about ten seconds, until the
reconciler read the server: the instant replies' ``done`` frame carried no
``content``, and the client closes a turn from it. The static gate is
``tests/test_instant_reply_frames.py``; this is the behaviour in a browser.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: Well under the reconciler's cadence (6 s), which closed the turn before.
PROMPT_CLOSE_MS = 3000

_LAST_HEADER = (
    "() => { const hs = document.querySelectorAll('.turn-header');"
    " return hs.length ? hs[hs.length - 1].innerText : ''; }"
)


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


@pytest.mark.parametrize("command", ["/replay", "/research"])
def test_an_instant_reply_closes_its_turn_promptly(harness: Harness, command: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function("() => !!window.KazmaChat", timeout=30000)
            box = pg.locator("#chat-input, textarea").first
            box.fill(command)
            box.press("Enter")
            # The answer arrives...
            pg.wait_for_function(
                "() => /saved|Usage/.test((document.querySelector('#chat-messages, .chat-messages')"
                " || document.body).innerText)",
                timeout=15000,
            )
            # ...and its turn is closed promptly, not by the reconciler.
            pg.wait_for_function(
                f"() => !/thinking/i.test(({_LAST_HEADER})())"
                f" && /completed/i.test(({_LAST_HEADER})())",
                timeout=PROMPT_CLOSE_MS,
            )
        finally:
            context.close()
            browser.close()
