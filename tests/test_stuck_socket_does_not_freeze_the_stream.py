"""A watching socket that stops reading freezes nobody's stream (FA-136).

The 2026-11 functional audit: ``TurnBroker.emit`` held the thread's emit lock
while it awaited ``socket.send_json`` with no bound, ahead of the SSE queue.
One tab whose socket stopped reading (a sleeping laptop, a dead phone
connection) stopped every frame of that thread: in the repro, after 2 s no
emit had finished and neither the healthy socket nor the SSE queue had a
frame. The SSE pump awaits ``emit`` per token, so the answer stalled for every
viewer.

The SSE queue is fed first, sends go out at once and are bounded; a socket
that takes no frame in the bound is dropped (it resumes from its cursor). The
negative control is the old sequential, unbounded fan-out.
"""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

import pytest

from kazma_ui import delivery
from kazma_ui.delivery import TurnBroker

REPO = Path(__file__).resolve().parents[1]


class _Healthy:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_json(self, frame: dict) -> None:
        self.frames.append(frame)

    async def close(self, code: int = 1000) -> None:
        return None


class _Stuck:
    """A socket whose reader stopped: its send never returns."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = False

    async def send_json(self, frame: dict) -> None:
        await self.release.wait()

    async def close(self, code: int = 1000) -> None:
        self.closed = True


async def _emit_three(broker: TurnBroker) -> tuple[_Healthy, _Stuck, asyncio.Queue, float]:
    healthy, stuck = _Healthy(), _Stuck()
    broker.register_socket("t1", stuck)
    broker.register_socket("t1", healthy)
    queue = broker.subscribe("t1")
    start = time.monotonic()

    async def emits() -> None:
        for i in range(3):
            await broker.emit("t1", {"type": "token", "data": {"content": str(i)}})

    task = asyncio.create_task(emits())
    await asyncio.wait({task}, timeout=1.0)
    took = time.monotonic() - start
    stuck.release.set()
    await asyncio.wait({task}, timeout=5.0)
    return healthy, stuck, queue, took


@pytest.mark.asyncio
async def test_the_healthy_socket_and_the_sse_queue_get_every_frame_within_a_second():
    broker = TurnBroker()
    healthy, stuck, queue, took = await _emit_three(broker)
    assert [f["data"]["content"] for f in healthy.frames] == ["0", "1", "2"]
    assert queue.qsize() == 3
    assert took < 1.5
    assert broker.socket_count("t1") == 1, "the stuck socket is dropped"
    await asyncio.sleep(0)
    assert stuck.closed


@pytest.mark.asyncio
async def test_the_old_fan_out_froze_the_thread(monkeypatch):
    """Negative control: the sequential, unbounded fan-out before FA-136."""
    async def old_fan_out(self, thread_id, sockets, frame):
        delivered = 0
        for _conn_id, socket in sockets:
            await socket.send_json(frame)
            delivered += 1
        return delivered

    monkeypatch.setattr(TurnBroker, "_fan_out_sockets", old_fan_out)
    broker = TurnBroker()
    healthy, stuck = _Healthy(), _Stuck()
    broker.register_socket("t1", stuck)
    broker.register_socket("t1", healthy)
    task = asyncio.create_task(broker.emit("t1", {"type": "token"}))
    done, _ = await asyncio.wait({task}, timeout=1.0)
    assert not done and healthy.frames == []
    stuck.release.set()
    await task


def test_no_send_is_awaited_inside_the_emit_lock():
    """The class: inside ``async with`` in delivery.py, no ``await x.send*(``."""
    tree = ast.parse((REPO / "kazma-ui/kazma_ui/delivery.py").read_text(encoding="utf-8"))
    found = []
    for block in [n for n in ast.walk(tree) if isinstance(n, ast.AsyncWith)]:
        for node in ast.walk(block):
            if (isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr.startswith("send")):
                found.append(node.lineno)
    assert not found, found
    assert delivery._SOCKET_SEND_BOUND_S <= 1.0
