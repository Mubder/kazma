"""Kazma's own share of the time to the first token stays small (FA-277).

The 2026-11 functional audit measured the time to the first visible token of
a web turn at p50 5.3 s and p95 20.1 s against a budget of p50 <= 3 s and
p95 <= 8 s. Most of it was the model (deepseek-flash's hidden reasoning, about
30,200 input tokens of tool definitions a turn); which model and how much
reasoning is the owner's choice (Stage 0). What Kazma itself adds -- the
route, the turn's notes, memory recall, the graph, the stream -- is held here.

Trivial turns ("ping") through the real web route, on the unified-turn
harness: the real app on uvicorn, an isolated data folder, and a model that
streams its answer the moment it is called. The time from the request to the
first ``token`` frame Kazma sends is Kazma's alone. The first turn warms the
process and is not counted.
"""

from __future__ import annotations

import statistics
import time
from typing import Any

import pytest

pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

#: Turns measured after the warm-up.
TURNS = 12
#: Kazma's own share of the first token, a fraction of the whole turn's
#: budget (p50 <= 3 s, p95 <= 8 s) -- what is left is the model's. Measured
#: 2026-11-02 on the development machine: p50 0.054 s, p95 0.072 s.
OWN_P50_S = 0.5
OWN_P95_S = 1.5


class _Pong:
    """A model that answers "pong" at once."""

    calls: list[str]

    def __init__(self) -> None:
        self.calls = []

    def respond(self, messages: list[Any]) -> Any:
        from kazma_core.llm_provider import LLMResponse

        self.calls.append("final")
        return LLMResponse(content="pong", finish_reason="stop", model="harness-model")


def _first_token(client: Any, base: str, text: str) -> float:
    """Seconds from sending *text* to the first token frame, then read to the end."""
    started = time.perf_counter()
    first: float | None = None
    with client.stream("POST", f"{base}/api/chat/stream",
                       json={"message": text, "session_id": "latency-chat"}, timeout=120) as response:
        assert response.status_code == 200, response.status_code
        for line in response.iter_lines():
            if first is None and line.startswith("event: token"):
                first = time.perf_counter() - started
            if line.startswith("event: done"):
                break
    assert first is not None, "the turn sent no token"
    return first


def _measure(turns: int) -> tuple[float, float, list[float]]:
    import httpx

    model = _Pong()
    with unified_turn_server(model) as h:  # type: ignore[arg-type]
        with httpx.Client() as client:
            client.get(f"{h.base}/chat")
            _first_token(client, h.base, "warm up")
            samples = [_first_token(client, h.base, f"ping {n}") for n in range(turns)]
    ordered = sorted(samples)
    p50 = statistics.median(ordered)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return p50, p95, ordered


def test_kazma_adds_little_before_the_first_token() -> None:
    p50, p95, ordered = _measure(TURNS)
    print(f"[first-token] Kazma's share over {TURNS} turns: p50 {p50:.3f}s p95 {p95:.3f}s "
          f"max {ordered[-1]:.3f}s")
    assert p50 <= OWN_P50_S, f"p50 {p50:.2f}s of Kazma's own time before the first token"
    assert p95 <= OWN_P95_S, f"p95 {p95:.2f}s of Kazma's own time before the first token"


def test_a_slow_step_before_the_first_token_is_caught(monkeypatch) -> None:
    """Negative control: building the turn's messages made 0.6 s slower
    (a blocking read on the turn's path) fails the budget."""
    from kazma_core.agent import turn_input

    real = turn_input.build_turn_messages

    async def slow(*args: Any, **kwargs: Any) -> Any:
        import asyncio

        await asyncio.sleep(0.6)
        return await real(*args, **kwargs)

    monkeypatch.setattr(turn_input, "build_turn_messages", slow)
    p50, _p95, _ordered = _measure(4)
    assert p50 > OWN_P50_S
