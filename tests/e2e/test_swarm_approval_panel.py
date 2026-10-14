"""The Swarm page's approval panel in a browser (Swarm audit SW-S03).

A chat approval card links to ``/swarm?approval=<id>``. Reached by a link
inside Kazma (a soft navigation, which runs the page script before the
address bar changes) in English and Arabic, the panel must show the whole
call with the card's hash, and its Approve must decide the shared request,
recorded as the web, with the buttons gone afterwards.
"""

from __future__ import annotations

import json
import time
import uuid

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

CALL = json.dumps({"content": "Meeting notes: " + "a" * 2500, "path": "/home/op/.ssh/authorized_keys"})


def _open_request(tool: str, args: str) -> str:
    """A request as SafetyMiddleware opens one: the gate record, then the row."""
    from kazma_core.safety.hitl_gates import GateRow, register_gate
    from kazma_core.swarm import shared_approvals

    rid = "swarm-gate-" + uuid.uuid4().hex[:12]
    register_gate(GateRow(gate_id=rid, thread_id="swarm", tool=tool, mechanism="swarm_bus",
                          args_json=args, message=args[:500]), ttl_seconds=300)
    shared_approvals.open_request(rid, tool=tool, args=args, deadline=time.time() + 300)
    return rid


def test_a_request_not_on_record_is_asked_once() -> None:
    """Live 2026-10-14: the panel polled an unknown id every 5 s for as long
    as the page stayed open. A request not on record never appears later."""
    from playwright.sync_api import sync_playwright

    from kazma_ui.i18n import TRANSLATIONS

    with unified_turn_server() as h, sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            asked: list[str] = []
            page.on("request", lambda r: asked.append(r.url) if "/api/swarm/approvals/" in r.url else None)
            page.goto(h.base + "/swarm?approval=no-such-request", wait_until="domcontentloaded")
            page.wait_for_function(
                "(want) => (document.getElementById('swarm-approval-body') || {}).textContent === want",
                arg=TRANSLATIONS["swarm.approval.not_found"]["en"], timeout=15000)
            page.wait_for_timeout(11000)  # two poll periods
            assert len(asked) == 1, asked
            assert page.is_hidden("#swarm-approval-actions")
        finally:
            browser.close()


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_panel_shows_the_whole_call_and_takes_the_answer(language: str) -> None:
    from playwright.sync_api import sync_playwright

    from kazma_core.swarm import shared_approvals
    from kazma_core.swarm.approval_card import render_arguments
    from kazma_ui.i18n import TRANSLATIONS

    with unified_turn_server() as h:
        rid = _open_request("file_write", CALL)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(h.base + "/memory", wait_until="domcontentloaded")
                page.wait_for_function("() => !!window.Alpine")
                page.evaluate("() => { window.__notReloaded = true; }")
                # A link inside Kazma, followed the way nav.js follows one.
                page.evaluate(
                    "(href) => { const a = document.createElement('a'); a.href = href;"
                    " a.textContent = 'request'; document.querySelector('main, body').appendChild(a);"
                    " a.click(); }",
                    f"/swarm?approval={rid}",
                )
                page.wait_for_function("() => location.pathname === '/swarm'", timeout=20000)
                page.wait_for_selector("#swarm-approval .swarm-approval-call", timeout=20000)
                assert page.evaluate("() => window.__notReloaded === true"), "a soft navigation"

                panel = page.inner_text("#swarm-approval")
                assert "/home/op/.ssh/authorized_keys" in panel
                assert "a" * 2500 in panel, "the whole call, not the card's cut"
                assert render_arguments(CALL).digest in panel
                assert TRANSLATIONS["swarm.approval.st_pending"][language] in panel
                assert TRANSLATIONS["swarm.approval.title"][language] in panel
                assert page.get_attribute("#swarm-approval .swarm-approval-call", "dir") == "ltr"

                page.click('[data-action="approval-approve"]')
                page.wait_for_function(
                    "() => (document.querySelector('[data-approval-state]') || {}).dataset"
                    "?.approvalState === 'approved'",
                    timeout=15000,
                )
                assert TRANSLATIONS["swarm.approval.st_approved"][language] in page.inner_text("#swarm-approval")
                assert page.is_hidden("#swarm-approval-actions")
                row = shared_approvals.get(rid) or {}
                assert (row.get("status"), row.get("platform")) == ("approved", "web")
                assert not errors, errors
            finally:
                browser.close()
