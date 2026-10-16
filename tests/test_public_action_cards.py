"""An always-ask card names the public action it approves, on every surface.

Live 2026-10-16 an ``x_post`` card read "Approval Required ... Approve once",
like any other card, and the owner approved it as part of "check the
metrics". Each always-ask card (``ALWAYS_HITL_TOOLS``: X writes) now says
what it does in its header ("Publish to X — public, can't be undone"), shows
the post first and whole, and its approving button says the action ("Post
now"). It offers no task/session approval: the web route and the chat apps
both refuse one. The web card in a browser, EN/AR:
``tests/e2e/test_always_ask_one_time_approval.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS
from kazma_core.safety.public_actions import PUBLIC_ACTIONS, public_action

ROOT = Path(__file__).resolve().parents[1]
CHAT_JS = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
LONG_POST = "Kazma metrics — " + "x" * 260 + " END"


def _js_table() -> dict[str, tuple[tuple[str, str], tuple[str, str]]]:
    src = CHAT_JS.read_text(encoding="utf-8")
    block = re.search(r"var _PUBLIC_ACTION_LABELS = \{(.*?)\};", src, re.S).group(1)
    out = {}
    for tool, k1, e1, k2, e2 in re.findall(
        r"(\w+): \[\['(\w+)', '([^']*)'\], \['(\w+)', '([^']*)'\]\]", block
    ):
        out[tool] = ((k1, e1), (k2, e2))
    return out


def test_every_always_ask_tool_has_its_words_everywhere():
    from kazma_ui.i18n.catalog import merged

    CATALOG = merged()

    assert set(PUBLIC_ACTIONS) == set(ALWAYS_HITL_TOOLS)
    table = _js_table()
    assert set(table) == set(ALWAYS_HITL_TOOLS)
    for tool, (title, button) in PUBLIC_ACTIONS.items():
        (tkey, tfall), (bkey, bfall) = table[tool]
        assert (tkey, bkey) == (f"hitl_public_{tool}", f"hitl_public_{tool}_btn")
        assert tfall.replace("’", "'") == title and bfall == button, tool
        for key, english in ((tkey, title), (bkey, button)):
            entry = CATALOG[f"chat.{key}"]
            assert entry["en"].replace("’", "'") == english and entry["ar"].strip(), key


def test_the_chat_page_bridges_the_table():
    from tests.test_chat_i18n_bridge import bridge, used_keys

    template = (ROOT / "kazma-ui" / "kazma_ui" / "templates" / "chat.html").read_text(encoding="utf-8")
    plain_used, _ = used_keys([CHAT_JS.read_text(encoding="utf-8")])
    plain, _ = bridge(template)
    for tool in ALWAYS_HITL_TOOLS:
        assert f"hitl_public_{tool}" in plain_used and f"hitl_public_{tool}" in plain
        assert f"hitl_public_{tool}_btn" in plain_used and f"hitl_public_{tool}_btn" in plain
    assert "hitl_public_note" in plain


def _prompt(platform: str, tool: str, *, long_text: str = LONG_POST):
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt

    payload = {
        "tool": tool, "args": {"text": "model text", "proposal_id": "prop_abc:1"},
        "tools": [{"name": tool, "args": {}, "proposal": {
            "proposal_id": "prop_abc", "items": [{"id": "prop_abc:1", "text": long_text}]}}],
        "interrupt_id": "gate-1",
    }
    return _build_approval_prompt(payload, "th-1", platform=platform)


def _buttons(platform: str, markup) -> list[str]:
    if platform == "telegram":
        return [b["text"] for row in markup["inline_keyboard"] for b in row]
    if platform == "discord":
        return [b["label"] for row in markup for b in row["components"]]
    return [e["text"]["text"] for b in markup if b.get("type") == "actions" for e in b["elements"]]


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
def test_a_chat_app_card_names_the_post_and_has_no_task_approval(platform):
    out = _prompt(platform, "x_post")
    assert out["text"].startswith("📢 Publish to X — public, can't be undone")
    assert LONG_POST in out["text"]  # whole, not clipped at 240
    assert out["text"].index(LONG_POST) < out["text"].index("Tool: x_post")
    labels = _buttons(platform, out["markup"])
    assert any("Post now" in label for label in labels), labels
    assert not any("task" in label.lower() for label in labels), labels
    assert any("Deny" in label for label in labels), labels


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
def test_any_other_card_keeps_its_buttons(platform):
    """Control: an ordinary danger tool still reads Approve / Approve for task."""
    out = _prompt(platform, "file_write", long_text="short")
    assert out["text"].startswith("⚠️ Approval required")
    labels = _buttons(platform, out["markup"])
    assert any(label.endswith("Approve") for label in labels), labels
    assert any("task" in label.lower() for label in labels), labels


def test_every_always_ask_tool_gets_a_public_card():
    for tool in ALWAYS_HITL_TOOLS:
        out = _prompt("telegram", tool)
        title, button = public_action(tool)
        assert out["text"].startswith(f"📢 {title}"), tool
        assert any(button in b for b in _buttons("telegram", out["markup"])), tool


async def _decide(action: str, monkeypatch):
    import kazma_ui.hitl_decision as decisions
    from kazma_core.sessions.directory import record_thread_owner
    from kazma_gateway.agent_handler.hitl import _handle_hitl_resume
    from kazma_gateway.gateway import IncomingMessage

    recorder = AsyncMock(side_effect=RuntimeError("stop after recording"))
    monkeypatch.setattr(decisions, "record_gate_decision", recorder)
    interrupt = SimpleNamespace(id="gate-1", value={
        "type": "hitl_approval", "tool": "x_post", "tools": [{"name": "x_post"}],
        "always_ask": True})
    graph = SimpleNamespace(
        aget_state=AsyncMock(return_value=SimpleNamespace(
            next=("tool_worker",), tasks=[SimpleNamespace(interrupts=[interrupt])])),
        ainvoke=AsyncMock(),
    )
    store = SimpleNamespace(get=AsyncMock(return_value={"sender_id": "user"}))
    manager = SimpleNamespace(send=AsyncMock())
    monkeypatch.setenv("KAZMA_GATEWAY_ADMINS", "telegram:user")
    record_thread_owner("thread", "telegram:user")
    message = IncomingMessage("telegram", "user", f"/hitl {action} thread~gate-1",
                              context_metadata={"chat_id": 123, "callback_query_id": "button"})
    assert await _handle_hitl_resume(message, graph, {}, "thread", store, manager)
    return recorder, graph, manager


@pytest.mark.asyncio
async def test_approve_for_task_on_a_post_approves_nothing(monkeypatch):
    recorder, graph, manager = await _decide("approve_task", monkeypatch)
    recorder.assert_not_awaited()
    graph.ainvoke.assert_not_awaited()
    assert "Nothing was approved" in manager.send.call_args.args[0].text


@pytest.mark.asyncio
async def test_approve_on_a_post_reaches_the_decision(monkeypatch):
    """Control: the one-time approval is still taken (recorded first)."""
    recorder, graph, _ = await _decide("approve", monkeypatch)
    recorder.assert_awaited()
