"""Deleting a chat keeps what Kazma learned unless the user ticks the box
(plan S3, the owner's rule 2026-09-27).

Through the real page: the sidebar's delete opens the confirm dialog with a
checkbox, unticked. Ticked, the chat is kept out of memory and what it left is
forgotten FIRST -- the forget finds the chat's other key through the chat
store, which the delete empties -- and only then is the chat deleted. Left
unticked (the negative control), no forget request is made at all.
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


def _seed_chat(sid: str) -> None:
    """One chat in the store the harness's server reads (same process).
    The dialog needs a chat, not a turn: a first turn pays for the
    embedder's warm-up, minutes on a cold machine."""
    from kazma_ui.session_manager import get_session_manager

    sm = get_session_manager()
    sess = sm.get_or_create(sid)
    sess.thread_id = sid
    sess.title = "S3 " + sid
    sess.messages = [{"role": "user", "content": "hello there"},
                     {"role": "assistant", "content": "Done."}]
    sm.put(sess)


_HAS_JS = """async (sid) => {
  const r = await fetch('/api/chat/sessions');
  const j = await r.json();
  return (j.sessions || j).some((s) => s.session_id === sid);
}"""


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def _delete(pg, sid: str, *, tick: bool) -> list[tuple[str, str]]:
    """Open the real delete dialog, tick or not, press Delete; the PUT/DELETE
    requests the page made, in order."""
    sent: list[tuple[str, str]] = []

    def note(req) -> None:
        if "/api/" in req.url and req.method in ("PUT", "DELETE"):
            sent.append((req.method, req.url.split("/api/", 1)[1]))

    pg.on("request", note)
    try:
        pg.evaluate("(sid) => { window.KazmaChat.deleteSession(sid); }", sid)
        box = pg.wait_for_selector(".modal-checkbox input", state="visible", timeout=10000)
        assert not box.is_checked(), "the box must start unticked: keeping memory is the default"
        if tick:
            box.check()
        with pg.expect_response(lambda r: r.request.method == "DELETE" and sid in r.url, timeout=15000):
            pg.click(".modal-footer .btn-danger")
        pg.wait_for_function(f"async () => !(await ({_HAS_JS})({sid!r}))", timeout=15000)
    finally:
        pg.remove_listener("request", note)
    return sent


def test_deleting_with_the_box_ticked_forgets_first(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function("() => !!window.KazmaChat && !!window.Alpine && !!Alpine.store('modal')",
                                 timeout=30000)
            for sid in ("s3-forget", "s3-keep"):
                _seed_chat(sid)
                assert pg.evaluate(_HAS_JS, sid), f"the page does not see {sid}"

            forgot = _delete(pg, "s3-forget", tick=True)
            assert forgot == [("PUT", "memory/v2/chats/s3-forget/memory"),
                              ("DELETE", "chat/sessions/s3-forget")], forgot

            # Negative control: unticked, memory is kept -- no forget at all.
            kept = _delete(pg, "s3-keep", tick=False)
            assert kept == [("DELETE", "chat/sessions/s3-keep")], kept
        finally:
            browser.close()
