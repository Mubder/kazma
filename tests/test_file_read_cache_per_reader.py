"""The "[ALREADY READ THIS TURN]" note is about the reader's own reads.

2026-11 functional audit, FA-111. ``file_read`` keeps a turn's reads so a
repeat read is answered with a note instead of the whole file again. The
cache was one for the process, keyed by the file: a second chat reading a
file the first chat had read got the note -- "do NOT re-read; use the
content from your context" -- about content it had never seen, and the end
of any turn, or any swarm dispatch, emptied every chat's cache. It is keyed
by the reader now: the chat thread, or a swarm task's own scope.

The cache keyed by the file alone (``_reader`` returning one constant) is the
negative control.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

fr = importlib.import_module("kazma_core.tools.file_read")
ALREADY_READ = "ALREADY READ THIS TURN"


class _Allow:
    allowed = True


@pytest.fixture
def notes(tmp_path, monkeypatch):
    from kazma_core.workspace import path_policy

    monkeypatch.setattr(path_policy, "check_path_access", lambda *a, **k: _Allow(), raising=False)
    target = tmp_path / "notes.txt"
    target.write_text("line one\nline two\n", encoding="utf-8")
    fr._turn_read_cache.clear()
    fr._turn_read_cache_order.clear()
    yield str(target)
    fr._turn_read_cache.clear()
    fr._turn_read_cache_order.clear()


def _read_as(thread_id: str, path: str) -> str:
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    async def run() -> str:
        token = set_current_thread_id(thread_id)
        try:
            return await fr.file_read(path)
        finally:
            reset_current_thread_id(token)

    return asyncio.run(run())


def _clear_as(thread_id: str) -> None:
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    token = set_current_thread_id(thread_id)
    try:
        fr.clear_read_cache()
    finally:
        reset_current_thread_id(token)


def test_two_chats_reading_one_file_both_get_it(notes):
    assert ALREADY_READ not in _read_as("chat-a", notes)
    second = _read_as("chat-b", notes)
    assert ALREADY_READ not in second and "line two" in second
    # Each chat's own repeat is still the note.
    assert ALREADY_READ in _read_as("chat-a", notes)


def test_negative_control_one_cache_for_everyone(notes, monkeypatch):
    monkeypatch.setattr(fr, "_reader", lambda: "everyone")
    _read_as("chat-a", notes)
    assert ALREADY_READ in _read_as("chat-b", notes)


def test_a_turn_end_clears_its_own_chat_only(notes):
    _read_as("chat-a", notes)
    _read_as("chat-b", notes)
    _clear_as("chat-a")
    assert ALREADY_READ not in _read_as("chat-a", notes)
    assert ALREADY_READ in _read_as("chat-b", notes)


def test_a_swarm_scope_reads_for_itself_and_leaves_nothing(notes):
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

    _read_as("chat-a", notes)

    async def task() -> tuple[str, str]:
        token = set_current_thread_id("chat-a")  # dispatched from that chat
        scope = fr.enter_read_scope("swarm-task-1")
        try:
            first = await fr.file_read(notes)
            again = await fr.file_read(notes)
        finally:
            fr.leave_read_scope(scope)
            reset_current_thread_id(token)
        return first, again

    first, again = asyncio.run(task())
    assert ALREADY_READ not in first and ALREADY_READ in again
    assert not any(key[0] == "scope:swarm-task-1" for key in fr._turn_read_cache)
    # The chat that dispatched it still holds its own read.
    assert ALREADY_READ in _read_as("chat-a", notes)
