"""A reload with work running finishes inside one stop budget (FA-261).

The 2026-11 functional audit, three reload drills with a streamed turn, a
pending approval, a crawl, a swarm task and an OCR stage running: the stop was
the sum of fixed per-step bounds -- uvicorn's graceful wait 15 s (spent on a
stream left open for the approval), the background drain 10 s, the document
workers up to 45 s, the memory worker up to 30 s, under a 120 s ceiling -- and
took 58-60 s of the guard's 60 s grace. One drill was killed at 60 s and the
owner paged for a reload they had asked for.

One budget now: the guard hands the server ``KAZMA_SHUTDOWN_BUDGET_S`` (its
grace less a margin), the clock starts at the stop signal, every step takes
``step_bound(its own bound)``, and streams waiting on a queue wake at once.
The real app's stop with every slow step stuck ends inside the budget; the
old per-step bounds are the negative control.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from kazma_core import shutdown

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def fresh():
    shutdown.reset_shutdown()
    yield
    shutdown.reset_shutdown()


def test_step_bound_takes_what_is_left(monkeypatch):
    monkeypatch.setenv("KAZMA_SHUTDOWN_BUDGET_S", "20")
    assert shutdown.step_bound(45.0) == 20.0  # before the stop: the whole budget
    shutdown.signal_shutdown()
    monkeypatch.setattr(shutdown, "_shutdown_started", time.monotonic() - 15)
    assert 4.5 < shutdown.step_bound(45.0) <= 5.0
    assert shutdown.step_bound(2.0) == 2.0
    monkeypatch.setattr(shutdown, "_shutdown_started", time.monotonic() - 30)
    assert shutdown.step_bound(10.0) == 0.0


@pytest.mark.asyncio
async def test_waiting_streams_wake_at_once():
    """The token queue and an attached stream's queue get the wake on the
    loop, as the stop begins (no 10 s keepalive tick)."""
    from kazma_core.llm_stream import register_delta_queue, unregister_delta_queue
    from kazma_ui.delivery import get_turn_broker

    tokens: asyncio.Queue = asyncio.Queue()
    register_delta_queue("fa261-th", tokens)
    attached = get_turn_broker().subscribe("fa261-th")
    try:
        shutdown.signal_shutdown()
        got = await asyncio.wait_for(tokens.get(), 1.0)
        assert got is shutdown.SHUTDOWN_WAKE
        assert await asyncio.wait_for(attached.get(), 1.0) is shutdown.SHUTDOWN_WAKE
    finally:
        unregister_delta_queue("fa261-th")
        get_turn_broker().unsubscribe("fa261-th", attached)


def test_the_real_app_stop_ends_inside_the_budget(monkeypatch):
    """Every slow step stuck: the background drain, the memory worker, the
    agent's MCP shutdown. The stop ends inside the budget."""
    from fastapi.testclient import TestClient

    from kazma_core import background
    from kazma_core.memory import worker_bootstrap
    from kazma_ui.app import create_app

    monkeypatch.setenv("KAZMA_SHUTDOWN_BUDGET_S", "6")

    async def stuck_drain(timeout: float = 10.0) -> None:
        await asyncio.sleep(timeout)  # a drain that uses all it is given

    async def stuck(*args, **kwargs) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(background, "drain_background", stuck_drain)
    monkeypatch.setattr(worker_bootstrap, "stop_memory_worker", stuck)
    app = create_app()
    client = TestClient(app)
    client.__enter__()
    agent = getattr(app.state, "agent", None)
    if agent is not None:
        monkeypatch.setattr(agent, "shutdown", stuck)
    started = time.monotonic()
    client.__exit__(None, None, None)
    took = time.monotonic() - started
    assert took < 6 + 3, f"the stop took {took:.1f}s of a 6 s budget"


def test_the_guard_hands_the_budget_down():
    src = (REPO / "scripts/service/kazma_guard.py").read_text(encoding="utf-8")
    spawn = src[src.index("def spawn("):]
    assert 'env["KAZMA_SHUTDOWN_BUDGET_S"]' in spawn[:2000]
    assert "SHUTDOWN_MARGIN_S" in src


def test_the_old_bounds_did_not_fit_the_guards_grace():
    """Negative control: the per-step bounds before FA-261 (uvicorn 15, drain
    10, documents 45, memory 30, swarm and agent unbounded) summed past the
    guard's 60 s grace with a stream open and a document stage running."""
    old_steps = {"uvicorn graceful": 15, "background drain": 10, "documents": 45, "memory": 30}
    assert sum(old_steps.values()) > 60
    # And each one is now cut to what is left.
    assert all(shutdown.step_bound(v) <= shutdown._shutdown_budget_s() for v in old_steps.values())
