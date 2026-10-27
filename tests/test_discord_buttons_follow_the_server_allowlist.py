"""A Discord button pressed in a server Kazma is limited away from does
nothing (audit 2026-10-26 AUD-066).

Messages and slash commands apply the Guild ID allowlist
(``connectors.discord.guild_id``); component interactions -- the approval
and picker buttons -- checked the user only, so a button in another server
was answered as in an allowed one.
"""

from __future__ import annotations

import asyncio
from typing import Any


class _Http:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def post(self, path: str, json: dict[str, Any]) -> None:
        self.posts.append((path, json))


def _adapter(*, guilds: list[str]) -> Any:
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="demo-discord-token", allowed_guilds=guilds, allowed_users=["7"])
    adapter._http = _Http()
    adapter._queue = asyncio.Queue()
    return adapter


def _press(guild: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "type": 3,
        "id": "i1",
        "token": "demo-interaction-token",
        "channel_id": "55",
        "member": {"user": {"id": "7", "username": "owner"}},
        "data": {"custom_id": "personality:friendly"},
        "message": {"content": "Pick one"},
    }
    if guild is not None:
        data["guild_id"] = guild
    return data


def _run(adapter: Any, data: dict[str, Any]) -> list[str]:
    asyncio.run(adapter._handle_interaction(data))
    out = []
    while not adapter._queue.empty():
        out.append(adapter._queue.get_nowait().text)
    return out


def test_a_button_in_another_server_is_ignored() -> None:
    adapter = _adapter(guilds=["111"])
    assert _run(adapter, _press("222")) == []
    # Acknowledged quietly (type 6), never answered.
    assert [p[1] for p in adapter._http.posts] == [{"type": 6}]


def test_a_button_in_an_allowed_server_or_a_dm_still_works() -> None:
    assert _run(_adapter(guilds=["111"]), _press("111")) == ["/personality friendly"]
    assert _run(_adapter(guilds=["111"]), _press(None)) == ["/personality friendly"]
    assert _run(_adapter(guilds=[]), _press("222")) == ["/personality friendly"]


def test_negative_control_without_the_server_check_the_press_is_taken() -> None:
    # The same press with the allowlist emptied after construction is what
    # the handler did for every server before AUD-066: taken.
    adapter = _adapter(guilds=["111"])
    adapter._allowed_guilds = set()
    assert _run(adapter, _press("222")) == ["/personality friendly"]
