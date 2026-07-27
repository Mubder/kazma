"""Telegram-parity UX modules for Discord/Slack."""

from __future__ import annotations

from kazma_gateway.adapters.discord_keyboards import build_approval_components
from kazma_gateway.adapters.discord_parse import parse_message_create
from kazma_gateway.adapters.platform_callbacks import parse_callback_data
from kazma_gateway.adapters.slack_blocks import build_approval_blocks
from kazma_gateway.adapters.slack_parse import parse_message_event
from kazma_gateway.adapters.telegram_callbacks import parse_callback_data as tg_parse


def test_shared_callback_schemes_match_telegram() -> None:
    for raw in (
        "hitl:approve:abc",
        "hitl:deny:abc",
        "personality:default",
        "model_provider:openai",
        "model_select:gpt-4o",
        "swarm_approve_task1",
        "swarm_reject_task1",
        "sys_install:foo",
    ):
        a = parse_callback_data(raw)
        b = tg_parse(raw)
        assert a.kind == b.kind
        assert a.text == b.text
        assert a.swarm_data == b.swarm_data


def test_discord_approval_components_ids() -> None:
    comps = build_approval_components("req-1")
    assert comps[0]["components"][0]["custom_id"] == "hitl:approve:req-1"
    assert comps[0]["components"][1]["custom_id"] == "hitl:deny:req-1"


def test_slack_approval_blocks_ids() -> None:
    blocks = build_approval_blocks("req-2")
    actions = blocks[-1]["elements"]
    assert actions[0]["value"] == "hitl:approve:req-2"
    assert actions[1]["value"] == "hitl:deny:req-2"


def test_discord_parse_message() -> None:
    msg = parse_message_create(
        {
            "id": "99",
            "channel_id": "ch1",
            "content": "hello discord",
            "author": {"id": "u1", "username": "bob", "bot": False},
            "attachments": [],
        }
    )
    assert msg is not None
    assert msg.platform == "discord"
    assert msg.text == "hello discord"
    assert msg.context_metadata["channel_id"] == "ch1"


def test_discord_parse_skips_bots() -> None:
    assert (
        parse_message_create(
            {
                "id": "1",
                "channel_id": "c",
                "content": "x",
                "author": {"id": "b", "bot": True},
            }
        )
        is None
    )


def test_slack_parse_message() -> None:
    msg = parse_message_event(
        {
            "type": "message",
            "channel": "C1",
            "user": "U1",
            "text": "hello slack",
            "ts": "1.0",
            "team": "T1",
        }
    )
    assert msg is not None
    assert msg.platform == "slack"
    assert msg.sender_id == "slack:U1"
    assert msg.text == "hello slack"


def test_adapter_static_keyboards() -> None:
    from kazma_gateway.adapters.discord import DiscordAdapter
    from kazma_gateway.adapters.slack import SlackAdapter
    from kazma_gateway.adapters.telegram import TelegramAdapter

    tg = TelegramAdapter.build_approval_keyboard("x")
    assert "inline_keyboard" in tg
    dc = DiscordAdapter.build_approval_keyboard("x")
    assert isinstance(dc, list) and dc[0]["type"] == 1
    sk = SlackAdapter.build_approval_keyboard("x")
    assert any(b.get("type") == "actions" for b in sk)
