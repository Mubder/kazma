"""Aborting a turn pauses the chat's long task (2026-11, FA-148).

AGENTS 25B said ``/abort`` clears an active ``/long``. It did not: the web
chat's abort and the chat apps' ``/abort`` cancelled the turn and left the
long task -- a mission, or ``/unrestricted`` -- active, so the next message
ran with no time limit under mission framing. Both paths now pause it, as a
Partial result does (``pause_long_task``); ``/long`` turns it on again.

Driven through the real ``abort_turn`` with a stand-in graph; the abort
without the pause is the negative control. The chat apps' path is held by
the source check below (the same call, beside its ``close_aborted_turn``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _Graph:
    def __init__(self) -> None:
        self.values = {"messages": [{"role": "user", "content": "keep researching"}]}

    async def aget_state(self, config):
        from types import SimpleNamespace

        return SimpleNamespace(values=self.values, next=())

    async def aupdate_state(self, config, values, **kw):
        self.values = {**self.values, **values}


@pytest.fixture
def mission(monkeypatch):
    from kazma_core.agent import long_task

    thread = "t-abort-long"
    long_task.enable_long_task(thread, actor="test", mode="mission")
    assert long_task.is_long_task_active(thread)
    monkeypatch.setattr("kazma_ui.turn_runtime.close_aborted_turn", _noop)
    monkeypatch.setattr("kazma_core.memory.forget.rewind_memory", _noop)
    yield thread
    long_task.disable_long_task(thread, actor="test")


async def _noop(*args, **kwargs):
    return None


async def test_an_abort_pauses_the_long_task(mission):
    from kazma_core.agent import long_task
    from kazma_ui.turn_abort import abort_turn

    result = await abort_turn(mission, _Graph())
    assert result["ok"] is True
    assert not long_task.is_long_task_active(mission)
    assert long_task.long_task_status(mission).get("paused_reason", "aborted") == "aborted"


async def test_negative_control_without_the_pause_it_stays_active(mission, monkeypatch):
    from kazma_core.agent import long_task
    from kazma_ui.turn_abort import abort_turn

    monkeypatch.setattr(long_task, "pause_long_task_for_abort", _noop)
    await abort_turn(mission, _Graph())
    assert long_task.is_long_task_active(mission)


def test_the_chat_apps_abort_pauses_it_too():
    source = (ROOT / "kazma-gateway/kazma_gateway/agent_handler/graph.py").read_text(encoding="utf-8")
    block = source.split('in ("/abort", "/stop")', 1)[1].split("⛔ Task aborted", 1)[0]
    assert "await pause_long_task_for_abort(thread_id)" in block
