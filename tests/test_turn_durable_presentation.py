"""Durable presentation state — ``docs/plans/UNIFIED_TURN_BLOCK.md`` §8.

The Phase 0 report (§5.2) found no write inside the token loop: between the
first token and the pause or terminal write, the turn's presentation state
lived only in the in-process journal (``delivery.py``: "process-local memory
only") and the LangGraph checkpoint, which holds messages — not reasoning,
not tool activity, not partial answer text. Tool activity was worse than
un-durable: it had no producer at all, because Kazma's tool worker calls the
registry directly and LangGraph emits no ``on_tool_*`` for it.

Two guarantees, deliberately different, and this module is where the
difference is written down as a test rather than as a claim:

* **Activity is persist-then-publish.** Committed before its frame is
  emitted, so anything the client was told is recoverable.
* **Answer text is checkpointed.** Bounded by
  ``DURABLE_TEXT_CHARS`` / ``DURABLE_TEXT_INTERVAL_S``. Tokens published
  since the last checkpoint are NOT durable. The plan forbids claiming the
  strong guarantee where persistence is periodic; it is not claimed, and
  the bound is measured below rather than asserted in prose.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def session(tmp_path, monkeypatch) -> tuple[str, str]:
    """An isolated session with one open reply turn. Returns (sid, turn_id)."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_ui.reply_sink import open_reply_turn
    from kazma_ui.session_manager import get_session_manager, reset_session_manager

    reset_session_manager()
    sm = get_session_manager()
    sid = "durable-test"
    sess = sm.get_or_create(sid)
    sess.thread_id = sid
    sess.messages = [{"role": "user", "content": "go"}]
    sm.put(sess)
    turn = open_reply_turn(sid)
    yield sid, turn
    reset_session_manager()


def _row(sid: str, turn: str) -> dict[str, Any]:
    from kazma_ui.session_manager import get_session_manager

    rows = get_session_manager().get_or_create(sid).messages
    return next((m for m in rows if m.get("turn_id") == turn), {})


def test_activity_is_durable_immediately(session) -> None:
    """A noted tool part commits on the next call, with no throttle.

    Persist-then-publish: the pump calls ``commit`` BEFORE emitting the
    tool frame, so a row the client was told about is recoverable. Tool
    events are a handful per turn, so one transaction each is affordable —
    which is exactly why activity and text get different rules.
    """
    from kazma_ui.sse_chat._streaming import DurablePresentation

    sid, turn = session
    dp = DurablePresentation(sid, turn, sid)
    dp.note({"type": "tool", "name": "file_read", "call_id": "r1",
             "result": "", "state": "running"})

    assert asyncio.run(dp.commit("")) is True
    parts = _row(sid, turn).get("parts") or []
    tools = [p for p in parts if p.get("type") == "tool"]
    assert len(tools) == 1
    assert tools[0]["call_id"] == "r1"
    assert tools[0]["state"] == "running"


def test_a_tool_row_advances_rather_than_duplicating(session) -> None:
    """running -> done is one row, because the call id is the key."""
    from kazma_ui.sse_chat._streaming import DurablePresentation

    sid, turn = session
    dp = DurablePresentation(sid, turn, sid)
    dp.note({"type": "tool", "name": "file_read", "call_id": "r1",
             "result": "", "state": "running"})
    asyncio.run(dp.commit(""))
    dp.note({"type": "tool", "name": "file_read", "call_id": "r1",
             "result": "alpha", "state": "done"})
    asyncio.run(dp.commit(""))

    tools = [p for p in (_row(sid, turn).get("parts") or [])
             if p.get("type") == "tool"]
    assert len(tools) == 1, "one call persisted as two rows"
    assert tools[0]["state"] == "done"
    assert tools[0]["result"] == "alpha"


def test_text_is_throttled_not_written_per_token(session, monkeypatch) -> None:
    """Text waits for a char or time threshold; activity never does."""
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(_streaming, "DURABLE_TEXT_CHARS", 100)
    monkeypatch.setattr(_streaming, "DURABLE_TEXT_INTERVAL_S", 999.0)

    sid, turn = session
    dp = _streaming.DurablePresentation(sid, turn, sid)

    assert asyncio.run(dp.commit("x" * 40)) is False, (
        "text under the threshold was written anyway"
    )
    assert dp.writes == 0
    assert asyncio.run(dp.commit("x" * 140)) is True
    assert dp.writes == 1
    assert (_row(sid, turn).get("content") or "").startswith("x")


def test_force_commits_whatever_is_pending(session, monkeypatch) -> None:
    """The terminal write must not leave a checkpoint behind.

    The terminal persist builds parts from text and the HITL payload only,
    so a tool row still queued at that moment would be dropped on the last
    step of the turn it survived all of.
    """
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(_streaming, "DURABLE_TEXT_CHARS", 10_000)
    monkeypatch.setattr(_streaming, "DURABLE_TEXT_INTERVAL_S", 999.0)

    sid, turn = session
    dp = _streaming.DurablePresentation(sid, turn, sid)
    dp.note({"type": "tool", "name": "shell_exec", "call_id": "r9",
             "result": "ok", "state": "done"})
    assert asyncio.run(dp.commit("the answer", force=True)) is True

    row = _row(sid, turn)
    assert "the answer" in (row.get("content") or "")
    assert any(p.get("call_id") == "r9" for p in (row.get("parts") or []))


def test_a_failed_checkpoint_keeps_its_parts(session, monkeypatch) -> None:
    """A write that fails must not swallow the activity it was carrying.

    Plan §8: a persistence failure surfaces and enters bounded recovery; it
    does not silently drop content. Dropping the queued parts here would
    lose exactly what this class exists to keep, and the next checkpoint
    would have no idea anything was missing.
    """
    from kazma_ui.sse_chat import _streaming

    sid, turn = session
    dp = _streaming.DurablePresentation(sid, turn, sid)
    dp.note({"type": "tool", "name": "file_read", "call_id": "r1",
             "result": "", "state": "running"})

    def _boom(*a: Any, **kw: Any) -> bool:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(_streaming, "persist_reply", _boom)
    assert asyncio.run(dp.commit("", force=True)) is False
    assert dp.failures == 1

    # Recovered: the same part is still queued and the next commit lands it.
    monkeypatch.undo()
    assert asyncio.run(dp.commit("", force=True)) is True
    tools = [p for p in (_row(sid, turn).get("parts") or [])
             if p.get("type") == "tool"]
    assert len(tools) == 1 and tools[0]["call_id"] == "r1"


def test_write_amplification_is_bounded_and_measured(session, monkeypatch) -> None:
    """Plan §11: measure write amplification, do not assert it in prose.

    Streams 12,000 characters in 40-character chunks — 300 token events —
    with the shipped 600-character threshold and no time trigger, and
    records how many transactions that costs. One write per token would be
    300; the bound here is 12000/600 = 20.
    """
    from kazma_ui.sse_chat import _streaming

    monkeypatch.setattr(_streaming, "DURABLE_TEXT_INTERVAL_S", 999.0)
    chars_per_write = _streaming.DURABLE_TEXT_CHARS

    sid, turn = session
    dp = _streaming.DurablePresentation(sid, turn, sid)

    async def _stream() -> None:
        text = ""
        for _ in range(300):
            text += "x" * 40
            if dp.text_is_due(text, 0.0):
                await dp.commit(text)

    asyncio.run(_stream())

    total = 300 * 40
    expected = total // chars_per_write
    assert dp.writes == expected, (
        f"{dp.writes} writes for {total} chars at a {chars_per_write}-char "
        f"threshold (expected {expected})"
    )
    # The bound, stated as the test rather than as a comment: at most one
    # write per threshold, and never one per token.
    assert dp.writes <= total / chars_per_write
    assert dp.writes < 300 / 10


def test_the_pump_commits_before_it_publishes() -> None:
    """Persist-then-publish for activity, checkpointed for text.

    A boundary check on the shipped pump: the ordering is the guarantee, so
    an edit that moves the emit above the commit turns a durable row into a
    best-effort one without changing a single test elsewhere.
    """
    src = (
        ROOT / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py"
    ).read_text(encoding="utf-8")

    for marker in ('"tool_call",', '"tool_result",'):
        before = src.rsplit(marker, 1)[0]
        tail = before[-1200:]
        assert "_durable.commit(content_acc)" in tail, (
            f"the {marker.strip(chr(34) + chr(44))} frame is emitted without "
            f"a durable commit in front of it"
        )

    assert "await _durable.commit(content_acc, force=True)" in src, (
        "the terminal write no longer flushes queued activity"
    )
    token_block = src.split("content_acc += token_text", 1)[1][:600]
    assert "_durable.text_is_due(" in token_block, (
        "the token loop no longer checkpoints text at all"
    )


def test_tool_activity_has_a_producer() -> None:
    """LangGraph emits no on_tool_* for this worker — measured, not assumed.

    ``graph.astream_events`` over a turn that really writes a file produces
    only ``on_chain_*``: the tool worker calls ``tool_registry.execute()``
    directly rather than invoking a LangChain tool runnable. Both transports
    derive their tool rows from ``on_tool_start`` / ``on_tool_end``, so
    without an injected event there is no tool activity anywhere — live,
    stored, or restored.
    """
    worker = (
        ROOT / "kazma-core" / "kazma_core" / "agent" / "graph_tool_worker.py"
    ).read_text(encoding="utf-8")
    assert "emit_tool_activity" in worker, (
        "the tool worker no longer announces its own activity"
    )

    from kazma_core.llm_stream import emit_tool_activity

    assert callable(emit_tool_activity)

    stream = (
        ROOT / "kazma-core" / "kazma_core" / "llm_stream.py"
    ).read_text(encoding="utf-8")
    body = stream.split("def emit_tool_activity(", 1)[1].split("\ndef ", 1)[0]
    assert '"on_tool_error"' not in body, (
        "an errored call must still end as on_tool_end — the SSE pump maps "
        "start and end only, so on_tool_error leaves the row at 'running' "
        "forever on the primary transport"
    )


def test_resume_leg_binds_a_delta_queue() -> None:
    """The approved work runs on the resume leg, which had no queue.

    Measured on the four-gate scenario: all four tools ran on resume legs,
    all eight activity events were emitted, and every one was dropped
    because ``ainvoke`` never registered a delta queue for the thread. The
    operator saw one backfilled token per leg and no tool rows.
    """
    src = (
        ROOT / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py"
    ).read_text(encoding="utf-8")
    resume = src.split("_is_resume:", 1)[1].split("            else:", 1)[0]
    assert "_reg_q(thread_id, _resume_q)" in resume, (
        "the resume leg no longer binds a delta queue, so tokens and tool "
        "activity produced by approved work go nowhere"
    )
    assert "_unreg_q(thread_id)" in resume, "the resume queue is never released"
    for kind in ("on_chat_model_stream", "on_tool_start", "on_tool_end"):
        assert kind in resume, f"the resume leg stopped mapping {kind}"
