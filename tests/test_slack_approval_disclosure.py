"""Slack renders blocks instead of fallback text: the decision must be in them."""

from __future__ import annotations

from kazma_gateway.agent_handler.hitl import _build_approval_prompt
from kazma_gateway.adapters.platform_keyboards import slack_approval_blocks


def _visible(prompt: dict) -> str:
    return "".join(b["text"]["text"] for b in prompt["markup"] if b["type"] == "section")


def test_slack_write_card_discloses_path_content_and_redacts_secrets():
    prompt = _build_approval_prompt(
        {"tool": "file_write", "args": {
            "path": "fixture/approval.txt", "content": "SYNTHETIC-CONTENT", "api_key": "private-secret",
        }}, "thread~gate", platform="slack",
    )
    visible = _visible(prompt)
    assert visible == prompt["text"]
    assert "fixture/approval.txt" in visible and "SYNTHETIC-CONTENT" in visible
    assert "private-secret" not in visible
    assert prompt["markup"][-1]["elements"][1]["value"] == "hitl:deny:thread~gate"


def test_grouped_slack_card_shows_every_action_in_blocks():
    prompt = _build_approval_prompt(
        {"tools": [
            {"name": "file_write", "args": {"path": f"fixture/{i}.txt", "content": "test"}}
            for i in range(12)
        ]}, "thread~batch", platform="slack",
    )
    visible = _visible(prompt)
    assert visible == prompt["text"]
    for i in range(12):
        assert f"fixture/{i}.txt" in visible


def test_long_exec_preview_keeps_hidden_suffix_warning_and_literal_markup():
    prompt = _build_approval_prompt(
        {"tool": "shell_exec", "args": {"command": "<@U123> <!channel> & " + "x" * 5000}},
        "thread~exec", platform="slack",
    )
    assert _visible(prompt) == prompt["text"]
    assert "Do NOT approve this from chat" in _visible(prompt)
    sections = [b for b in prompt["markup"] if b["type"] == "section"]
    assert len(sections) > 1
    assert all(b["text"]["type"] == "plain_text" and len(b["text"]["text"]) <= 3000 for b in sections)


def test_extreme_preview_is_bounded_with_explicit_warning():
    blocks = slack_approval_blocks("gate", details="x" * 150000)
    assert len(blocks) == 50
    assert "Do NOT approve" in blocks[-2]["text"]["text"]
    assert blocks[-1]["type"] == "actions"


def test_legacy_keyboard_call_keeps_default_heading_and_callbacks():
    blocks = slack_approval_blocks("gate")
    assert "Approval required" in blocks[0]["text"]["text"]
    assert [b["value"] for b in blocks[-1]["elements"]] == [
        "hitl:approve:gate", "hitl:deny:gate", "hitl:approve_task:gate",
    ]
