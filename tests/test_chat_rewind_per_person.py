"""Stage 3 review, M4: in a chat app a member never undoes, edits or
restores another member's chat.

``/undo``, ``/edit`` and ``/replay <step>`` rewrite a chat's turns, and the
S3-06 follow-up held only ``/steer``, ``/abort``, ``/stop``, ``/reset`` and
``/compact`` to the web chat's rule. They now ask it too
(``hitl_answerers.turn_control_refusal``, action ``rewind``: the chat's
opener or an owner of the space, never while another member's turn runs or
waits), in a group and in a direct chat alike.

Through the real gateway handler, Spaces enforced, a group of each chat app
bound to Garden (the S3-06 follow-up's world):

* while Dana's turn runs, Rami's ``/undo``, ``/edit`` and ``/replay`` are
  refused, told whose turn it is; her turn runs on, nothing is rewritten;
* Dana opened the chat: once her turn ends, Rami's ``/undo`` and ``/edit``
  are refused (the chat is hers), hers and the space owner's are taken;
* Rami's ``/switch`` to Dana's chat from his own direct chat never lets his
  ``/undo`` reach it.

Negative control: the handler without the rule -- Rami's ``/undo`` takes
back Dana's answer.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from kazma_core.spaces import scope

from tests.test_chat_turn_control_per_person import (  # noqa: F401  (the fixture)
    GROUP_PLATFORMS,
    _dana_s_turn_running,
    _end,
    _message,
    _said,
    world,
)


def _answers(w: Any, thread: str) -> list[str]:
    return [str(m.get("content")) for m in (w.graph.values.get(thread) or {}).get("messages") or []
            if isinstance(m, dict) and m.get("role") == "assistant"]


async def _dana_s_chat(w: Any, platform: str) -> str:
    """Dana opens the group's chat and gets an answer. Returns its thread."""
    await _said(w, platform, "dana", "/space Garden")
    await _said(w, platform, "dana", "which beds need water?")
    return next(t for b, t in w.graph.runs if b is not None and b.asker == w.who["dana"])


@pytest.mark.parametrize("platform", GROUP_PLATFORMS)
def test_another_member_s_rewind_is_refused_while_her_turn_runs(world, platform) -> None:
    async def run() -> None:
        thread, turn = await _dana_s_turn_running(world, platform)
        try:
            for command in ("/undo", "/edit use roses instead", "/replay abcd1234"):
                said = await _said(world, platform, "rami", command)
                assert "nothing was undone, edited or restored" in said, (command, said)
            assert not turn.done(), "Dana's turn runs on"
        finally:
            await _end(world, turn)

    asyncio.run(run())


@pytest.mark.parametrize("platform", GROUP_PLATFORMS)
def test_only_the_chat_s_opener_or_an_owner_rewinds_it(world, platform) -> None:
    async def run() -> None:
        thread = await _dana_s_chat(world, platform)
        before = _answers(world, thread)
        assert before, "Dana's chat has an answer"
        for command in ("/undo", "/edit use roses instead"):
            said = await _said(world, platform, "rami", command)
            assert "started this chat" in said and "dana" in said.lower(), (command, said)
        assert _answers(world, thread) == before, "Rami rewrote Dana's chat"
        said = await _said(world, platform, "dana", "/undo")
        assert "started this chat" not in said, said
        assert _answers(world, thread) != before, "Dana's own /undo was taken"

    asyncio.run(run())


def test_the_space_s_owner_rewinds_it(world) -> None:
    async def run() -> None:
        thread = await _dana_s_chat(world, "telegram")
        before = _answers(world, thread)
        said = await _said(world, "telegram", "owner", "/undo")
        assert "started this chat" not in said, said
        assert _answers(world, thread) != before, "the owner's /undo was taken"

    asyncio.run(run())


def test_a_switch_from_a_direct_chat_never_rewinds_another_s_chat(world) -> None:
    from kazma_gateway.gateway import IncomingMessage

    async def run() -> None:
        thread = await _dana_s_chat(world, "telegram")
        before = _answers(world, thread)
        uid = world.uids["telegram"]["rami"]
        meta = {"chat_id": int(uid), "user_id": int(uid), "chat_type": "private", "username": "rami"}

        async def say(text: str) -> str:
            n = len(world.sent)
            await world.handler(IncomingMessage("telegram", f"telegram:{uid}", text,
                                                context_metadata=dict(meta)))
            return " | ".join(t for _, t in world.sent[n:])

        await say("/space Garden")
        assert "Taken over" in await say(f"/switch {thread}"), "Rami's chat continues Dana's season"
        said = await say("/undo")
        assert "started this chat" in said and "dana" in said.lower(), said
        assert _answers(world, thread) == before, "Rami's /undo from his own chat rewrote Dana's"

    asyncio.run(run())


def test_negative_control_the_handler_without_the_rule(world, monkeypatch) -> None:
    """Planted: the handler of before -- nobody is refused a rewind. Rami's
    /undo takes back Dana's answer: the check above fails on its own
    assertion."""
    from kazma_ui import hitl_answerers

    real = hitl_answerers.turn_control_refusal
    monkeypatch.setattr(hitl_answerers, "turn_control_refusal",
                        lambda tid, action, **kw: None if action == "rewind" else real(tid, action, **kw))
    with pytest.raises(AssertionError, match="Rami rewrote Dana's chat|started this chat"):
        test_only_the_chat_s_opener_or_an_owner_rewinds_it(world, "telegram")


def test_before_spaces_anyone_rewinds_as_today(world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    asyncio.run(world.handler(_message(world, "telegram", "dana", "which beds need water?")))
    said = asyncio.run(_said(world, "telegram", "rami", "/undo"))
    assert "started this chat" not in said and "nothing was undone" not in said, said
