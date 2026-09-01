"""Cron send backends for discord/slack (audit H-2)."""

from __future__ import annotations

from kazma_gateway.agent_handler.graph import make_gateway_send_handler


class _Store:
    async def get(self, _tid: str):
        return None


class _Manager:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, outbound) -> bool:
        self.sent.append(outbound)
        return True


async def test_discord_and_slack_prefixes_route_through_gateway() -> None:
    mgr = _Manager()
    handler = make_gateway_send_handler(mgr, _Store())
    assert await handler("discord:123456789012345678", "hello") == (
        "sent:discord:123456789012345678"
    )
    assert await handler("slack:C0123ABC", "hi") == "sent:slack:C0123ABC"
    assert [o.target_id for o in mgr.sent] == [
        "discord:123456789012345678",
        "slack:C0123ABC",
    ]
    assert mgr.sent[0].text == "hello"
    assert mgr.sent[1].text == "hi"


async def test_telegram_still_converts_markdown() -> None:
    mgr = _Manager()
    handler = make_gateway_send_handler(mgr, _Store())
    await handler("telegram:1", "**bold**")
    assert mgr.sent[0].context_metadata.get("parse_mode") == "HTML"
    assert "<b>" in mgr.sent[0].text or "bold" in mgr.sent[0].text
