"""A public post is drafted, shown, and only then published -- on a card that
says it publishes.

Live 2026-10-16 12:25 UTC: asked to "check the metrics", the model saved a new
draft and asked to post it two seconds later, in the same turn. The owner
approved the card believing it was part of the reading ("Allow tool
(session)", Approve once), and the post went out. Now:

- a draft is never published in the turn that wrote it: the commitment gate
  refuses, no card appears, and the model shows the draft;
- in a later turn ("post it") the card names the public action ("Publish to
  X", button "Post now") and offers no "allow for this chat" or YOLO, and the
  approve route refuses those answers.

A scripted model does what the live one did -- save_proposal, then x_post with
the stored draft -- so the real commitment gate and the real interrupt run.
Fast locks: ``tests/test_always_ask_one_time_only.py``,
``tests/test_proposal_seen_before_publish.py``, ``tests/test_public_action_cards.py``.
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
NOT_POSTED = "Not posted: this draft was written in this same turn"


def _role(m) -> str:
    return str(m.get("role") or m.get("type") or "").lower() if isinstance(m, dict) else ""


class _DraftThenPost(Script):
    """Turn 1: save_proposal, then x_post of that draft (the live model's
    move) and, refused, a reply showing the draft. Turn 2 ("post it"): x_post
    of the same draft."""

    proposal_id: str = ""
    refusals: list = []

    def respond(self, messages):
        from kazma_core.llm_provider import LLMResponse, ToolCall

        msgs = list(messages or [])
        texts = [str(m.get("content") or "") for m in msgs if isinstance(m, dict)]
        tools = [str(m.get("content") or "") for m in msgs if _role(m) == "tool"]
        second_turn = any(_role(m) == "user" and "post it" in str(m.get("content") or "")
                          for m in msgs)
        refused = [t for t in tools if NOT_POSTED in t]
        if not second_turn:
            if not tools:
                self.calls.append("save_proposal")
                return LLMResponse(content="Saving the draft.", tool_calls=[ToolCall(
                    id="call_1_save_proposal", name="save_proposal",
                    arguments={"kind": "tweets", "items": [DRAFT]})],
                    finish_reason="tool_calls", model="harness")
            if not refused and not type(self).proposal_id:
                found = re.search(r"prop_[0-9a-f]+:\d+", "\n".join(tools))
                assert found, tools
                type(self).proposal_id = found.group(0)
                self.calls.append("x_post same turn")
                return LLMResponse(content="Posting it.", tool_calls=[ToolCall(
                    id="call_2_x_post", name="x_post",
                    arguments={"text": DRAFT, "proposal_id": found.group(0)})],
                    finish_reason="tool_calls", model="harness")
            type(self).refusals.extend(refused or [t for t in texts if NOT_POSTED in t])
            self.calls.append("show draft")
            return LLMResponse(content=f"Here is the draft: {DRAFT}. Shall I post it?",
                               finish_reason="stop", model="harness")
        last_user = max(i for i, m in enumerate(msgs) if _role(m) == "user"
                        and "post it" in str(m.get("content") or ""))
        if not [m for m in msgs[last_user + 1:] if _role(m) == "tool"]:
            self.calls.append("x_post next turn")
            return LLMResponse(content="Posting it.", tool_calls=[ToolCall(
                id="call_3_x_post", name="x_post",
                arguments={"text": DRAFT, "proposal_id": type(self).proposal_id})],
                finish_reason="tool_calls", model="harness")
        self.calls.append("final")
        return LLMResponse(content="Done.", finish_reason="stop", model="harness")


def _fresh_script() -> _DraftThenPost:
    _DraftThenPost.proposal_id = ""
    _DraftThenPost.refusals = []
    return _DraftThenPost(steps=[])


def _first_turn(h, client, session_id: str) -> list[dict]:
    with start_turn(h.base, session_id, "check the metrics", client) as resp:
        return list(sse_frames(resp, limit_seconds=90))


def _wait_for_approval(h, client, session_id: str) -> dict:
    with start_turn(h.base, session_id, "post it", client) as resp:
        for frame in sse_frames(resp, limit_seconds=90):
            if frame["event"] == "approval_required":
                return frame["data"]
    raise AssertionError("the turn never asked for approval")


def test_a_draft_is_not_published_in_the_turn_that_wrote_it():
    script = _fresh_script()
    with unified_turn_server(script) as h, api_client(h.base) as client:
        session = new_session_id()
        frames = _first_turn(h, client, session)
        assert not [f for f in frames if f["event"] == "approval_required"], frames
        assert _DraftThenPost.refusals and NOT_POSTED in _DraftThenPost.refusals[0], (
            _DraftThenPost.refusals, script.calls)
        assert script.calls[:3] == ["save_proposal", "x_post same turn", "show draft"], script.calls

        from kazma_core.agent.artifacts import get_artifact_store

        info = get_artifact_store().resolve_proposal(_DraftThenPost.proposal_id)
        assert info and not info["items"][0].get("used_at"), info

        # The next message asks to post it: the card appears, and it is public.
        approval = _wait_for_approval(h, client, session)
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


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_card_names_the_post_and_offers_post_now_and_deny_only(lang):
    from playwright.sync_api import sync_playwright

    script = _fresh_script()
    with unified_turn_server(script) as h, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = ctx.new_page()
            pg.goto(f"{h.base}/chat", wait_until="domcontentloaded")
            pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
            pg.wait_for_function("() => !!window.KazmaChat", timeout=20000)
            pg.fill("#chat-input", "check the metrics")
            pg.evaluate("() => window.KazmaChat.sendMessage()")
            pg.wait_for_function("() => /Shall I post it\\?/.test(document.body.textContent)", timeout=60000)
            assert pg.locator(".hitl-approval-card").count() == 0

            pg.fill("#chat-input", "post it")
            pg.evaluate("() => window.KazmaChat.sendMessage()")
            card = pg.locator(".hitl-approval-card").last
            card.locator(".hitl-approve").wait_for(state="visible", timeout=60000)
            title = {"en": "Publish to X", "ar": "نشر على X"}[lang]
            button = {"en": "Post now", "ar": "انشر الآن"}[lang]
            assert "hitl-public-action" in (card.get_attribute("class") or "")
            assert title in card.locator(".hitl-header-title").inner_text()
            assert card.locator(".hitl-approve").inner_text().strip() == button
            assert DRAFT in card.inner_text()
            assert card.locator(".hitl-approve-tool").count() == 0
            assert card.locator(".hitl-approve-yolo").count() == 0
            assert card.locator(".hitl-deny").count() == 1
            # A later render of the pending card reads its title from here
            # (_paintHitlSlotCard), so it keeps naming the public action.
            assert title in (card.get_attribute("data-pending-title") or "")
            card.locator(".hitl-deny").click()
            pg.wait_for_function("() => /Done\\./.test(document.body.textContent)", timeout=60000)
        finally:
            browser.close()
