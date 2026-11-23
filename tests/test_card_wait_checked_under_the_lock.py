"""Stage 3 review, M3: another member's message never drops a waiting card,
even when it arrived while the turn was still running.

``_waits_on_someone_else`` was asked before the thread lock: Rami's message
passed it while Dana's turn ran (no card yet), waited on the lock, and once
Dana's turn paused on a card it started a new turn -- which discards her
pending interrupt. The check is asked again under the lock, in every chat
(a direct chat switched to another member's chat too).

Through the real gateway handler (the S3-04 group world): Dana's turn runs
and will pause; Rami writes meanwhile; Dana's turn pauses on a card; Rami's
message is then refused with whom the card waits for, and the card stays.

Negative control: the handler of before, which asked only before the lock --
Rami's message runs a new turn over Dana's card.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from kazma_core.spaces import scope

from tests.test_chat_approvals_asker import GROUP, world  # noqa: F401  (the fixture)


def _message(w: Any, who: str, text: str):
    from kazma_gateway.gateway import IncomingMessage

    uid = w.uids[who]
    meta = {"chat_id": GROUP, "user_id": int(uid), "chat_type": "supergroup", "username": who}
    return IncomingMessage("telegram", f"telegram:{uid}", text, context_metadata=meta)


def _race(w: Any) -> list[tuple[Any, str]]:
    """Dana's turn holds the thread; Rami writes before her card exists; her
    turn then pauses on its card. Returns the turns that ran."""
    graph = w.graph
    release = None
    original = graph.ainvoke

    async def run() -> None:
        nonlocal release
        release = asyncio.Event()
        running = asyncio.Event()

        async def slow(state, config):
            if scope.current_scope() is not None and scope.current_scope().asker == w.who["dana"] \
                    and not running.is_set():
                running.set()
                await release.wait()
            return await original(state, config)

        graph.ainvoke = slow
        dana = asyncio.create_task(w.handler(_message(w, "dana", "write the plan for the beds")))
        await asyncio.wait_for(running.wait(), 10)
        rami = asyncio.create_task(w.handler(_message(w, "rami", "what is the weather?")))
        for _ in range(20):
            await asyncio.sleep(0.01)
        release.set()
        await asyncio.wait_for(asyncio.gather(dana, rami), 20)

    asyncio.run(run())
    return graph.runs


def test_a_message_queued_behind_the_turn_never_drops_its_card(world) -> None:
    runs = _race(world)
    askers = [bound.asker for bound, _tid in runs if bound is not None]
    assert askers == [world.who["dana"]], f"Rami's message ran a turn over Dana's card: {askers}"
    thread = runs[0][1]
    assert thread in world.graph.paused, "the card still waits"
    assert any("waits for" in text and "dana" in text.lower() for _t, text, _m in world.sent), world.sent


def test_negative_control_the_check_before_the_lock_only(world, monkeypatch) -> None:
    """Planted: the check asked only before the lock -- there it found no
    card yet, so it is as if never asked."""
    from kazma_ui import hitl_answerers

    monkeypatch.setattr(hitl_answerers, "waiting_for_someone_else", lambda *a, **k: None)
    with pytest.raises(AssertionError, match="ran a turn over Dana's card"):
        test_a_message_queued_behind_the_turn_never_drops_its_card(world)
