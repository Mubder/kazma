"""A markdown table in a reply to a chat app arrives as lines, not pipes
(2026-09-29). Telegram, Discord and Slack show no tables; a live Discord
reply read "| Check | Status |" / "|---|---|" row by row."""

from __future__ import annotations

import asyncio

import pytest

from kazma_gateway.chat_tables import tables_to_lines

LIVE = (
    "**Test received — Kazma is live and responding.** ✅\n"
    "\n"
    "| Check | Status |\n"
    "|---|---|\n"
    "| Message received | ✅ |\n"
    "| Supervisor agent | ✅ Online |\n"
    "| Reply channel | ✅ Working |\n"
    "\n"
    "Nothing was executed, sent, or modified."
)


def test_the_live_reply_reads_as_lines() -> None:
    assert tables_to_lines(LIVE) == (
        "**Test received — Kazma is live and responding.** ✅\n"
        "\n"
        "Check — Status\n"
        "• Message received — ✅\n"
        "• Supervisor agent — ✅ Online\n"
        "• Reply channel — ✅ Working\n"
        "\n"
        "Nothing was executed, sent, or modified."
    )


@pytest.mark.parametrize("table, lines", [
    ("a | b\n--- | ---\n1 | 2", "a — b\n• 1 — 2"),  # no outer pipes
    ("| Name | Age |\n|:---|---:|\n| Pixel | 3 |", "Name — Age\n• Pixel — 3"),  # alignment
    ("| | Value |\n|---|---|\n| x | |", "Value\n• x"),  # empty cells
    ("| الاسم | العمر |\n|---|---|\n| بكسل | ٣ |", "الاسم — العمر\n• بكسل — ٣"),
    ("| a | b |\n|---|---|\n| x \\| y | z |", "a — b\n• x | y — z"),  # escaped pipe
    ("| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n", "a — b\r\n• 1 — 2\r\n"),
])
def test_table_shapes(table: str, lines: str) -> None:
    assert tables_to_lines(table) == lines


@pytest.mark.parametrize("text", [
    "no table here",
    "a | b in prose, with no delimiter row\nnext line",
    "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```",  # inside a code fence
    "| a | b |\n|---|---|---|\n| 1 | 2 |",  # delimiter of another width
    "",
])
def test_what_is_not_a_table_is_left_alone(text: str) -> None:
    assert tables_to_lines(text) == text


def test_every_reply_to_a_chat_app_goes_through_it() -> None:
    """GatewayManager.send is the one way a reply reaches a chat app; the
    adapter receives the rewritten text (negative control below)."""
    from kazma_gateway.gateway import BaseAdapter, GatewayManager, OutboundMessage

    sent: list[str] = []

    class Fake(BaseAdapter):
        name = "discord"

        async def listen(self, queue, shutdown_event) -> None:  # pragma: no cover
            return None

        async def send(self, outbound: OutboundMessage) -> bool:
            sent.append(outbound.text)
            return True

    manager = GatewayManager()
    manager.add_adapter(Fake())
    ok = asyncio.run(manager.send(OutboundMessage(target_id="discord:c1", text=LIVE)))
    assert ok and "|---|" not in sent[0] and "• Reply channel — ✅ Working" in sent[0]

    # The control: text without a table arrives exactly as written.
    asyncio.run(manager.send(OutboundMessage(target_id="discord:c1", text="plain")))
    assert sent[1] == "plain"
