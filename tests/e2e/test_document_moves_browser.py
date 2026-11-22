"""Moving one's own document on the Documents page (Spaces S2-12, piece 5), EN/AR.

Reached by the sidebar. In the person's own space, their document offers
"Share with…" the shared spaces they are in; the page says what sharing
means before it moves anything, and sends the move. In a shared space,
their own document offers "Take out", said first too, and the space's
taken-out lines are listed. The document list and the move's answer come
from the test (the rules and the store are ``tests/test_document_moves.py``);
the spaces are real.
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_people_page_browser import _modal_action, _soft_nav, _t  # noqa: E402

DOC = {"document_id": "doc-mine-000001", "title": "Trip plan", "state": "ready", "mine": True}
THEIRS = {"document_id": "doc-theirs-0001", "title": "Their list", "state": "ready", "mine": False}


def _plain(text: str) -> str:
    return " ".join(text.replace("\u2068", "").replace("\u2069", "").split())


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_share_and_take_out_on_the_documents_page(lang):
    from kazma_core.spaces import people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    moved: list[dict] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "document-moves-test-secret-" + "x" * 24
        scope.set_enforced(True)
        family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = context.new_page()

            def listing(route):
                space = route.request.headers.get("x-kazma-space", "")
                lines = ([{"document_id": "doc-gone-0001", "title": "Old notes", "by": "x", "by_name": "Reem",
                           "at": "2026-11-20T10:00:00+00:00"}] if space == family else [])
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "documents": [DOC, THEIRS], "taken_out": lines}))

            def move(route):
                moved.append({"space": route.request.headers.get("x-kazma-space", ""),
                              **json.loads(route.request.post_data or "{}")})
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"ok": True, "document_id": "doc-new", "taken_out": False}))

            pg.route("**/api/documents", listing)
            pg.route("**/api/documents/*/move", move)

            # In the person's own space: "Share with…" the shared space.
            # The first page a loopback browser opens mints its sign-in
            # cookie; the next is drawn for the signed-in person.
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/documents")
            share = pg.locator(f"[data-doc-share='{DOC['document_id']}']")
            share.wait_for(state="visible", timeout=30000)
            assert pg.locator(f"[data-doc-share='{THEIRS['document_id']}']").count() == 0, "only one's own"
            assert pg.locator(f"[data-doc-take-out='{DOC['document_id']}']").is_hidden()
            share.select_option(family)
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            dialog = pg.locator(".modal", has=pg.locator("#modal-title"))
            assert _plain(_t("documents.share_confirm", lang)) in _plain(dialog.inner_text())
            with pg.expect_response(lambda r: r.url.endswith("/move")):
                _modal_action(pg, _t("documents.share", lang))
            assert moved and moved[-1] == {"space": "default", "to_space": family}, moved

            # In the shared space: "Take out", said first; the lines it keeps.
            pg.goto(f"{h.base}/documents?space={family}", wait_until="domcontentloaded")
            take = pg.locator(f"[data-doc-take-out='{DOC['document_id']}']")
            take.wait_for(state="visible", timeout=30000)
            assert pg.locator(f"[data-doc-take-out='{THEIRS['document_id']}']").is_hidden()
            lines = pg.locator("#documents-taken-out")
            lines.wait_for(state="visible", timeout=20000)
            assert "Old notes" in lines.inner_text() and "Reem" in lines.inner_text()
            take.click()
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            assert _plain(_t("documents.take_out_confirm", lang)) in _plain(dialog.inner_text())
            with pg.expect_response(lambda r: r.url.endswith("/move")):
                _modal_action(pg, _t("documents.take_out", lang))
            assert moved[-1] == {"space": family, "to_space": "default"}, moved
        finally:
            browser.close()
            scope.set_enforced(False)
