"""The web chat honours the turn's time limit, through the real route.

Full audit 2026-09-30: the chat apps' turns had a wall-clock budget
(``KAZMA_TURN_TIMEOUT_SECONDS`` in ``run_agent_turn``) and the web chat's did
not. Since 2026-10-15 one rule (``kazma_core.agent.turn_budget``) bounds
every running segment of a turn on every surface. Here the real app boots
(``create_app()`` under uvicorn, ``tests/e2e/_unified_turn_harness.py``), the
model is scripted at the provider boundary and made slow, and a turn is sent
through ``POST /api/chat/stream`` as the page sends it:

* past the limit, the turn ends with the honest reply in the ``done`` frame,
  the graph run is cancelled (the slow model call sees the cancellation), no
  turn is left running, ``close_turn`` closed it, the owner was paged once,
  and a reload reads the same reply;
* a pause for approval longer than the limit is not counted;
* negative control: the web pump without the budget (as it was) runs past
  the same limit.

``tests/test_turn_budget.py`` holds the same rules on the graph directly.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest

pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    Step,
    api_client,
    attach_stream,
    gate_from_frame,
    new_session_id,
    sse_frames,
    start_turn,
    submit_decision,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

ENV = "KAZMA_TURN_TIMEOUT_SECONDS"
LIMIT_S = 3


class Thinking:
    """How long the scripted model takes before each step, and what happened.

    The step is the number of tool results in the conversation, as the
    harness's script counts them, so a resumed graph asks for the same step.
    """

    def __init__(self, seconds_by_step: dict[int, float]) -> None:
        self.seconds_by_step = seconds_by_step
        self.events: list[str] = []

    async def wait(self, messages: list[Any]) -> None:
        done = sum(
            1 for m in messages or []
            if isinstance(m, dict) and str(m.get("role") or "").lower() == "tool"
        )
        seconds = self.seconds_by_step.get(done, 0.0)
        if seconds <= 0:
            return
        self.events.append("thinking")
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            self.events.append("cancelled")
            raise
        self.events.append("finished")


@contextlib.contextmanager
def slow_model(script: Script, thinking: Thinking) -> Iterator[None]:
    """The harness's scripted provider, with thinking time before each answer.

    Patched over the harness's own patch for the test's duration, at the
    same boundary (``LLMProvider.chat`` / ``chat_stream``).
    """
    from kazma_core.llm_provider import LLMProvider
    from kazma_core.llm_stream import StreamDelta

    async def _chat(self: Any, messages: Any, *a: Any, **kw: Any) -> Any:
        await thinking.wait(list(messages or []))
        return script.respond(list(messages or []))

    async def _chat_stream(self: Any, messages: Any, *a: Any, **kw: Any) -> Any:
        await thinking.wait(list(messages or []))
        resp = script.respond(list(messages or []))
        text = str(getattr(resp, "content", "") or "")
        for i in range(0, len(text), 24):
            yield StreamDelta(content=text[i : i + 24])
        yield StreamDelta(response=resp)

    with (
        mock.patch.object(LLMProvider, "chat", _chat),
        mock.patch.object(LLMProvider, "chat_stream", _chat_stream),
    ):
        yield


@pytest.fixture
def alerts(monkeypatch) -> list[str]:
    from kazma_core.observability import ops_alerts

    raised: list[str] = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: raised.append(key) or True)
    return raised


@pytest.fixture
def closes(monkeypatch) -> list[dict[str, Any]]:
    from kazma_ui import turn_runtime

    calls: list[dict[str, Any]] = []
    real = turn_runtime.close_turn

    async def spy(*args: Any, **kwargs: Any) -> bool:
        calls.append(dict(kwargs))
        return await real(*args, **kwargs)

    monkeypatch.setattr(turn_runtime, "close_turn", spy)
    return calls


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return bool(predicate())


def _read_until_terminal(resp: Any, limit_s: float) -> list[dict[str, Any]]:
    frames = []
    for frame in sse_frames(resp, limit_seconds=limit_s):
        frames.append(frame)
        if frame["event"] in ("done", "turn_complete"):
            break
    return frames


def _done(frames: list[dict[str, Any]]) -> dict[str, Any]:
    return next(f["data"] for f in frames if f["event"] == "done")


def _running_turns() -> int:
    from kazma_ui.active_turns import running_turn_count

    return running_turn_count()


def test_a_web_turn_past_the_limit_ends_honestly(monkeypatch, alerts, closes) -> None:
    from kazma_core.agent.turn_budget import TurnBudget, _timed_out_text

    monkeypatch.setenv(ENV, str(LIMIT_S))
    script = Script(steps=[], final="The model's late answer.")
    thinking = Thinking({0: 120.0})
    with unified_turn_server(script) as harness, slow_model(script, thinking):
        session_id = new_session_id()
        started = time.monotonic()
        with api_client(harness.base) as client:
            with start_turn(harness.base, session_id, "Summarise everything.", client) as resp:
                assert resp.status_code == 200
                frames = _read_until_terminal(resp, limit_s=60)
            elapsed = time.monotonic() - started
            text = _timed_out_text(TurnBudget(LIMIT_S, "env"))
            done = _done(frames)
            assert done["content"] == text, done
            assert done["interrupted"] is False
            assert elapsed < LIMIT_S + 20, elapsed
            # The graph run was cancelled, not left behind the reply.
            assert _wait_until(lambda: "cancelled" in thinking.events), thinking.events
            assert "finished" not in thinking.events
            assert _wait_until(lambda: _running_turns() == 0)
            assert _wait_until(lambda: bool(closes)), "close_turn never ran"
            assert alerts.count("turn.timed_out") == 1, alerts
            # A reload reads the same reply.
            assert _wait_until(lambda: _stored_reply(harness, client, session_id) == text)


def _stored_reply(harness: Harness, client: Any, session_id: str) -> str:
    resp = client.get(f"{harness.base}/api/chat/sessions/{session_id}/messages", timeout=30.0)
    if resp.status_code != 200:
        return ""
    rows = [m for m in resp.json() if m.get("role") == "assistant"]
    return str(rows[-1].get("content") or "") if rows else ""


def test_waiting_for_an_approval_longer_than_the_limit_is_not_counted(monkeypatch, alerts) -> None:
    monkeypatch.setenv(ENV, str(LIMIT_S))
    script = Script(steps=[Step(tool="file_write", args={}, narration="Writing the note.")],
                    final="Note written.")
    thinking = Thinking({})
    with unified_turn_server(script) as harness, slow_model(script, thinking):
        script.steps[0].args = {"path": os.path.join(harness.data_dir, "note.md"), "content": "hi"}
        session_id = new_session_id()
        with api_client(harness.base) as client:
            gate, thread_id, last_seq = "", "", 0
            with start_turn(harness.base, session_id, "Write a note.", client) as resp:
                for frame in sse_frames(resp, limit_seconds=60):
                    data = frame["data"] if isinstance(frame["data"], dict) else {}
                    last_seq = max(last_seq, int(data.get("seq") or 0))
                    thread_id = thread_id or str(data.get("thread_id") or "")
                    if frame["event"] in ("approval_required", "hitl") and gate_from_frame(frame):
                        gate = gate_from_frame(frame)
                        thread_id = str(data.get("thread_id") or thread_id)
                        break
            assert gate, "the turn did not pause for the approval"
            time.sleep(LIMIT_S + 2)  # the person reads the card: longer than the limit
            ack = submit_decision(harness.base, thread_id, approve=True, interrupt_id=gate, client=client)
            assert ack.get("_status") == 200 and ack.get("ok") is True, ack
            with attach_stream(harness.base, session_id, last_seq, client) as resp:
                frames = _read_until_terminal(resp, limit_s=60)
            assert _done(frames)["content"] == "Note written."
            assert "turn.timed_out" not in alerts
            assert os.path.exists(os.path.join(harness.data_dir, "note.md"))


def test_without_the_budget_the_web_turn_runs_past_the_limit(monkeypatch, alerts) -> None:
    """Negative control: the web pump as it was -- the limit set, no budget
    around the graph run. The turn is still running past the limit."""
    from kazma_core.agent import turn_budget

    @contextlib.asynccontextmanager
    async def no_budget(*args: Any, **kwargs: Any):
        yield turn_budget.TurnSegment()

    monkeypatch.setattr(turn_budget, "turn_segment_budget", no_budget)
    monkeypatch.setenv(ENV, str(LIMIT_S))
    script = Script(steps=[], final="The model's late answer.")
    thinking = Thinking({0: 120.0})
    with unified_turn_server(script) as harness, slow_model(script, thinking):
        session_id = new_session_id()
        frames: list[dict[str, Any]] = []

        def read() -> None:
            with api_client(harness.base) as client:
                with start_turn(harness.base, session_id, "Summarise everything.", client) as resp:
                    with contextlib.suppress(Exception):
                        frames.extend(_read_until_terminal(resp, limit_s=60))

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        time.sleep(LIMIT_S + 3)
        assert not [f for f in frames if f["event"] == "done"], "the turn ended within the limit"
        assert _running_turns() == 1 and thinking.events == ["thinking"]
        assert "turn.timed_out" not in alerts
        # Stop it (the page's Stop button) so the server can shut down.
        with api_client(harness.base) as client:
            client.post(f"{harness.base}/api/chat/stop", json={"session_id": session_id}, timeout=30.0)
        reader.join(timeout=60)
        assert _wait_until(lambda: "cancelled" in thinking.events)
