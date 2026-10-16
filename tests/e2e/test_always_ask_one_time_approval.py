"""An always-ask tool's card offers Approve once / Deny only, and an
"allow for this chat" answer approves nothing.

Live 2026-10-16 12:25 UTC: "Allow tool (session)" on an x_post card published
a post the owner read as "it will still ask me before posting". A scripted
model here does what the live one did -- save_proposal, then x_post with the
stored draft -- so the real commitment gate and the real interrupt run. Fast
lock on the rules: ``tests/test_always_ask_one_time_only.py``.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Script,
    api_client,
    new_session_id,
    sse_frames,
    start_turn,
    submit_decision,
    unified_turn_server,
)

DRAFT = "Kazma one-time approval check"


class _DraftThenPost(Script):
    """save_proposal, then x_post with the draft id save_proposal returned."""

    def respond(self, messages):
        from kazma_core.llm_provider import LLMResponse, ToolCall

        results = [m for m in messages or []
                   if isinstance(m, dict) and str(m.get("role") or m.get("type") or "").lower() == "tool"]
        if not results:
            self.calls.append("save_proposal")
            return LLMResponse(content="Saving the draft.", tool_calls=[ToolCall(
                id="call_1_save_proposal", name="save_proposal",
                arguments={"kind": "tweets", "items": [DRAFT]})],
                finish_reason="tool_calls", model="harness")
        if len(results) == 1:
            found = re.search(r"prop_[0-9a-f]+:\d+", str(results[0].get("content") or ""))
            assert found, results[0]
            self.calls.append("x_post")
            return LLMResponse(content="Posting it.", tool_calls=[ToolCall(
                id="call_2_x_post", name="x_post",
                arguments={"text": DRAFT, "proposal_id": found.group(0)})],
                finish_reason="tool_calls", model="harness")
        self.calls.append("final")
        return LLMResponse(content="Done.", finish_reason="stop", model="harness")


def _wait_for_approval(h, client, session_id: str) -> dict:
    with start_turn(h.base, session_id, "post the metrics", client) as resp:
        for frame in sse_frames(resp, limit_seconds=90):
            if frame["event"] == "approval_required":
                return frame["data"]
    raise AssertionError("the turn never asked for approval")


def test_allow_for_this_chat_and_yolo_approve_nothing_for_an_x_post():
    script = _DraftThenPost(steps=[])
    with unified_turn_server(script) as h, api_client(h.base) as client:
        approval = _wait_for_approval(h, client, new_session_id())
        assert approval.get("tool") == "x_post", approval
        assert approval.get("always_ask") is True, approval
        thread, iid = approval["thread_id"], str(approval.get("interrupt_id") or "")

        for scope in ("tool", "yolo"):
            answer = submit_decision(h.base, thread, approve=True, interrupt_id=iid,
                                     scope=scope, client=client)
            assert answer["_status"] == 409, answer
            assert answer.get("reason") == "one_time_only", answer

        from kazma_core.safety.hitl_grants import has_tool_grant
        from kazma_core.safety.yolo import is_yolo_active

        assert has_tool_grant(thread, "x_post") is False
        assert is_yolo_active(thread) is False
        # Nothing was approved: the question is still open, and still says so.
        pending = client.get(f"{h.base}/api/pending-approvals").json().get("pending") or []
        mine = [p for p in pending if p.get("thread_id") == thread]
        assert mine and mine[0].get("always_ask") is True, pending

        denied = submit_decision(h.base, thread, approve=False, interrupt_id=iid, client=client)
        assert denied["_status"] == 200, denied


def test_the_card_offers_approve_once_and_deny_only():
    from playwright.sync_api import sync_playwright

    script = _DraftThenPost(steps=[])
    with unified_turn_server(script) as h, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            pg = browser.new_page()
            pg.goto(f"{h.base}/chat", wait_until="domcontentloaded")
            pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
            pg.wait_for_function("() => !!window.KazmaChat", timeout=20000)
            pg.fill("#chat-input", "post the metrics")
            pg.evaluate("() => window.KazmaChat.sendMessage()")
            card = pg.locator(".hitl-approval-card").last
            card.locator(".hitl-approve").wait_for(state="visible", timeout=60000)
            assert card.locator(".hitl-approve-tool").count() == 0
            assert card.locator(".hitl-approve-yolo").count() == 0
            assert card.locator(".hitl-deny").count() == 1
            card.locator(".hitl-deny").click()
            pg.wait_for_function("() => /Done\\./.test(document.body.textContent)", timeout=60000)
        finally:
            browser.close()
