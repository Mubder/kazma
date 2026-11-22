"""The S2-12 review, finding 7: when a person leaves a shared space (or is
taken out of it), what is still open for them there ends.

* The identity store, the one writer of memberships, says who left
  (``spaces.departures``); a person added back is no longer departed.
* Their WebSockets in that space are closed (4403); another person's, and
  their own in another space, stay open.
* Their attached chat stream ends at once, not at its next idle check.
* A turn starts only for a member of its space: a request that slipped in
  before the removal, or a resume of their turn, gets none.

Control: the identity store that does not say (``departures.note`` a
no-op) leaves the socket open and the stream running.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import departures, identity, people, scope, threads
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bind_scope


@pytest.fixture
def team(monkeypatch):
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)
    reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
    omar = people.create_person("Omar", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
    space = people.create_shared_space("Team", by=OWNER_ACCOUNT_ID)["id"]
    other = people.create_shared_space("Club", by=OWNER_ACCOUNT_ID)["id"]
    for room in (space, other):
        for person in (reem, omar):
            people.add_member(room, person, by=OWNER_ACCOUNT_ID)
    try:
        yield {"space": space, "other": other, "reem": reem, "omar": omar}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


class _Socket:
    def __init__(self) -> None:
        self.closed: int | None = None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = code


def test_the_store_says_who_left_and_who_came_back(team) -> None:
    heard: list[tuple[str, str]] = []
    stop = departures.on_departure(lambda account, space: heard.append((account, space)))
    try:
        people.remove_member(team["space"], team["reem"], by=OWNER_ACCOUNT_ID)
        assert heard == [(team["reem"], team["space"])]
        assert departures.departed(team["reem"], team["space"])
        assert not departures.departed(team["reem"], team["other"])
        people.add_member(team["space"], team["reem"], by=OWNER_ACCOUNT_ID)
        assert not departures.departed(team["reem"], team["space"])
    finally:
        stop()


async def _sockets_after_removal(team) -> dict[str, _Socket]:
    from kazma_ui.live_connections import socket_watched

    sockets = {name: _Socket() for name in ("reem_here", "reem_there", "omar_here")}
    with socket_watched(team["reem"], team["space"], sockets["reem_here"]), \
            socket_watched(team["reem"], team["other"], sockets["reem_there"]), \
            socket_watched(team["omar"], team["space"], sockets["omar_here"]):
        await asyncio.to_thread(people.remove_member, team["space"], team["reem"], by=OWNER_ACCOUNT_ID)
        for _ in range(20):
            await asyncio.sleep(0.01)
    return sockets


def test_their_sockets_in_that_space_are_closed(team) -> None:
    sockets = asyncio.run(_sockets_after_removal(team))
    assert sockets["reem_here"].closed == 4403
    assert sockets["reem_there"].closed is None and sockets["omar_here"].closed is None


def test_control_a_store_that_does_not_say_leaves_the_socket_open(team, monkeypatch) -> None:
    monkeypatch.setattr(departures, "note", lambda *_a, **_k: None)
    sockets = asyncio.run(_sockets_after_removal(team))
    assert sockets["reem_here"].closed is None


async def _stream_after_removal(team, monkeypatch) -> tuple[list[str], float]:
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(_streaming, "is_turn_running", lambda _thread: True)
    monkeypatch.setattr(_streaming, "is_thread_paused", lambda _thread: False)
    with bind_scope(team["space"], team["reem"]):
        stream = _streaming._sse_attach_stream("thread-departed", "session-1", 0, replay_is_history=True)
        frames = [await stream.__anext__()]  # "resumed"
        await asyncio.to_thread(people.remove_member, team["space"], team["reem"], by=OWNER_ACCOUNT_ID)
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(4):
                async for chunk in stream:
                    frames.append(chunk)
        except TimeoutError:
            frames.append("still running")
        finally:
            await stream.aclose()
        return frames, asyncio.get_running_loop().time() - started


def test_their_attached_stream_ends_at_once(team, monkeypatch) -> None:
    frames, took = asyncio.run(_stream_after_removal(team, monkeypatch))
    assert "still running" not in frames and took < 2, (frames, took)


def test_control_without_the_store_saying_the_stream_runs_on(team, monkeypatch) -> None:
    monkeypatch.setattr(departures, "note", lambda *_a, **_k: None)
    frames, _took = asyncio.run(_stream_after_removal(team, monkeypatch))
    assert frames[-1] == "still running"


def test_a_turn_starts_only_for_a_member(team) -> None:
    with bind_scope(team["space"], team["reem"]):
        assert threads.scope_for_turn("thread-r").scope.asker == team["reem"]
    people.remove_member(team["space"], team["reem"], by=OWNER_ACCOUNT_ID)
    with bind_scope(team["space"], team["reem"]), pytest.raises(threads._AskerNotInSpace):
        threads.scope_for_turn("thread-r")  # a request that slipped in before the removal
    with pytest.raises(threads._AskerNotInSpace):
        threads.scope_for_turn("thread-r")  # a resume with nothing bound: the record's opener
    with bind_scope(team["space"], team["omar"]):
        assert threads.scope_for_turn("thread-o").scope.asker == team["omar"]


def test_nothing_changes_without_spaces(team, monkeypatch) -> None:
    from kazma_ui.live_connections import socket_watched

    monkeypatch.setattr(scope, "_enforced", False)

    async def run() -> Any:
        sock = _Socket()
        with socket_watched(None, None, sock):
            await asyncio.sleep(0)
        return sock

    assert asyncio.run(run()).closed is None
