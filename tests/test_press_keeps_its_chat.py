"""Stage 3 review, M5: a button press keeps the chat it was pressed in.

A Discord press built its message with no ``guild_id``, so a press in a
server channel read as a direct chat (``chat_apps._is_direct``): it bound the
presser's personal space, the card's thread was refused as another space's,
and the asker was told the card was stale -- a card in a server channel
could not be answered. The press now carries its server.

Through the real Discord adapter and the real gateway handler, with Spaces
enforced: a Discord server channel bound to Garden, Dana's turn there paused
on a card; Dana presses Approve; the press reaches the handler as a message
of that channel and resumes her turn, as her, in Garden. And each chat app's
press carries what tells its group from a direct chat (a gate over the
adapters' press messages).

Negative control: the press of before, with no server -- the card cannot be
answered.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_core.spaces import chat_links, identity, scope

from tests.test_chat_approvals_asker import IID, world  # noqa: F401  (the fixture)

CHANNEL = "900771"
GUILD = "424242"
ROOT = Path(__file__).resolve().parents[1]


def _link_discord(w: Any) -> None:
    """Dana and Rami linked on Discord too; the stand-in graph reads a
    thread only in its own space, as the real checkpoint store does once
    Spaces is enforced (S1-08: another space's thread is "no checkpoints")."""
    from kazma_core.spaces import threads

    answer = w.graph.aget_state

    async def in_its_space(config):
        tid = config["configurable"]["thread_id"]
        bound = scope.current_scope()
        if bound is not None and bound.space_id != threads.thread_home(tid):
            from types import SimpleNamespace

            return SimpleNamespace(values={}, next=(), tasks=())
        return await answer(config)

    w.graph.aget_state = in_its_space

    def link(reg: dict) -> None:
        for name in ("dana", "rami"):
            reg["links"].append({"platform": "discord", "user_id": w.uids[name], "account": w.who[name]})

    identity.update_registry(link)
    chat_links.refresh_links()


def _channel_message(w: Any, who: str, text: str):
    from kazma_gateway.gateway import IncomingMessage

    uid = w.uids[who]
    meta = {"channel_id": CHANNEL, "guild_id": GUILD, "user_id": uid, "username": who}
    return IncomingMessage("discord", f"discord:{uid}:{CHANNEL}", text, context_metadata=meta)


def _press(w: Any, who: str, custom_id: str, *, strip_server: bool = False) -> None:
    """*who* presses a button of the channel's card: the real adapter makes
    the message, the real handler runs it."""
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake")
    adapter._http = MagicMock()
    adapter._http.post = AsyncMock(return_value=MagicMock(status_code=200))
    queue: asyncio.Queue = asyncio.Queue()
    adapter._queue = queue

    async def run() -> None:
        await adapter._handle_interaction({
            "id": "ia_1", "token": "tok_1", "channel_id": CHANNEL, "guild_id": GUILD,
            "data": {"custom_id": custom_id}, "message": {"content": "Approve?"},
            "member": {"user": {"id": w.uids[who], "username": who}},
        })
        for _ in range(20):
            await asyncio.sleep(0.01)
        msg = queue.get_nowait()
        if strip_server:
            msg.context_metadata.pop("guild_id", None)
        await w.handler(msg)

    asyncio.run(run())


def test_a_press_in_a_server_channel_answers_its_card(world, monkeypatch) -> None:
    """The press alone answers it: L7's decision in the card's own space is
    taken out, so the server the press carries is what finds the card."""
    from kazma_gateway.agent_handler import hitl

    monkeypatch.setattr(hitl, "_card_space_for_presser", lambda thread: "")
    _link_discord(world)
    asyncio.run(world.handler(_channel_message(world, "dana", "/space Garden")))
    asyncio.run(world.handler(_channel_message(world, "dana", "write the plan for the beds")))
    (bound, thread), = world.graph.runs
    assert bound == scope.Scope(world.garden["id"], world.who["dana"])
    _press(world, "dana", f"hitl:approve:{thread}~{IID}")
    assert world.resumes, "the press in a server channel did not answer its card"
    (resumed_as, _cmd), = world.resumes
    assert resumed_as == scope.Scope(world.garden["id"], world.who["dana"])


#: Per chat app, the key of a press's message that tells a group from a
#: direct chat (``chat_apps._is_direct``); WhatsApp has only direct chats.
GROUP_KEYS = {"discord.py": "guild_id", "slack.py": "channel_id", "telegram.py": "chat_type"}


def _press_messages(path: Path) -> list[ast.Call]:
    """Every ``IncomingMessage(...)`` a press makes in *path*: one whose
    metadata says it is an interaction or a callback."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "IncomingMessage":
            text = ast.unparse(node)
            if "'interaction': True" in text or "callback_query_id" in text:
                out.append(node)
    return out


@pytest.mark.parametrize("name", sorted(GROUP_KEYS))
def test_every_press_message_carries_its_chat_kind(name) -> None:
    path = ROOT / "kazma-gateway" / "kazma_gateway" / "adapters" / name
    calls = _press_messages(path)
    assert calls, f"no press message found in {name}"
    for call in calls:
        assert repr(GROUP_KEYS[name]) in ast.unparse(call), (
            f"a press in {name} does not say which kind of chat it is in ({GROUP_KEYS[name]})")


def test_negative_control_a_press_with_no_server(world, monkeypatch) -> None:
    """Planted: the press of before, with no server, reaching the handler of
    before (L7's decision in the card's own space taken out too: since L7 a
    member's press read as a direct chat still finds its card). The card in
    the server channel cannot be answered."""
    from kazma_gateway.agent_handler import hitl

    monkeypatch.setattr(hitl, "_card_space_for_presser", lambda thread: "")
    _link_discord(world)
    asyncio.run(world.handler(_channel_message(world, "dana", "/space Garden")))
    asyncio.run(world.handler(_channel_message(world, "dana", "write the plan for the beds")))
    (_bound, thread), = world.graph.runs
    _press(world, "dana", f"hitl:approve:{thread}~{IID}", strip_server=True)
    assert not world.resumes, "the control must reproduce the stale card"
