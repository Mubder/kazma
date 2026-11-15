"""The Telegram adapter remembers a bounded number of groups it announced
itself in (2026-11-15).

``_announce_if_added_to_group`` posts a group's chat id when the bot joins it,
once per group, and kept every group id it had answered in a set for the life
of the process: one entry per group anyone added the bot to, never forgotten.
Now at most ``GROUP_ANNOUNCED_MAX`` are kept, oldest forgotten first.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kazma_gateway.adapters import telegram
from kazma_gateway.adapters.telegram import GROUP_ANNOUNCED_MAX, TelegramAdapter


class _Http:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    async def post(self, path: str, json: dict[str, Any]) -> None:
        self.sent.append(json["chat_id"])


def _joined(chat_id: int) -> dict[str, Any]:
    return {"message": {"chat": {"id": chat_id, "type": "supergroup"}, "group_chat_created": True}}


def _adapter() -> TelegramAdapter:
    adapter = TelegramAdapter.__new__(TelegramAdapter)
    adapter._http = _Http()
    return adapter


def test_each_group_is_announced_once_and_the_memory_stays_bounded() -> None:
    adapter = _adapter()

    async def run() -> None:
        await adapter._announce_if_added_to_group(_joined(-1001))
        await adapter._announce_if_added_to_group(_joined(-1001))
        assert adapter._http.sent == [-1001], "one announcement per group"
        for n in range(GROUP_ANNOUNCED_MAX * 3):
            await adapter._announce_if_added_to_group(_joined(-2000 - n))

    asyncio.run(run())
    assert len(adapter._group_announced) == GROUP_ANNOUNCED_MAX
    # The newest are remembered; the oldest went first.
    assert f"tg:group-announced:{-2000 - GROUP_ANNOUNCED_MAX * 3 + 1}" in adapter._group_announced
    assert "tg:group-announced:-1001" not in adapter._group_announced


def test_negative_control_without_the_bound_the_memory_grows_with_every_group(monkeypatch) -> None:
    """The old memory, a set that only grew: the first test fails on it."""
    monkeypatch.setattr(telegram, "GROUP_ANNOUNCED_MAX", 10**9)
    adapter = _adapter()

    async def run() -> None:
        for n in range(GROUP_ANNOUNCED_MAX * 3):
            await adapter._announce_if_added_to_group(_joined(-2000 - n))

    asyncio.run(run())
    assert len(adapter._group_announced) == GROUP_ANNOUNCED_MAX * 3 > GROUP_ANNOUNCED_MAX
