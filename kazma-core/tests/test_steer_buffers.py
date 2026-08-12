"""Unit tests for the steer/abort buffers (kazma_core.agent.steer).

Covers the in-progress-turn injection primitives used by /steer (soft),
/steer! (hard), and /abort — the process-wide, thread_id-keyed buffers
that the supervisor drains each iteration and the Web/gateway layers push to.
"""

from __future__ import annotations

import types

import pytest

from kazma_core.agent import steer


@pytest.fixture(autouse=True)
def _isolated_buffers():
    """Each test starts with empty buffers (module dicts are process-wide)."""
    steer._soft_steer_buffers.clear()
    steer._hard_steer_pending.clear()
    yield
    steer._soft_steer_buffers.clear()
    steer._hard_steer_pending.clear()


# ── Soft steer ───────────────────────────────────────────────────────


def test_push_then_drain_soft_returns_fifo_and_clears():
    steer.push_soft_steer("t1", "first")
    steer.push_soft_steer("t1", "second")
    drained = steer.drain_soft_steers("t1")
    assert [d["text"] for d in drained] == ["first", "second"]
    # Drain pops — a second drain is empty.
    assert steer.drain_soft_steers("t1") == []


def test_push_soft_ignores_empty_text():
    steer.push_soft_steer("t1", "   ")
    assert steer.drain_soft_steers("t1") == []


def test_soft_buffers_are_isolated_per_thread():
    steer.push_soft_steer("t1", "for t1")
    steer.push_soft_steer("t2", "for t2")
    assert [d["text"] for d in steer.drain_soft_steers("t1")] == ["for t1"]
    assert [d["text"] for d in steer.drain_soft_steers("t2")] == ["for t2"]


def test_drain_unknown_thread_returns_empty():
    assert steer.drain_soft_steers("never") == []


# ── Hard steer ───────────────────────────────────────────────────────


def test_hard_peek_does_not_pop():
    steer.push_hard_steer("t1", "urgent")
    assert steer.peek_hard_steer("t1") == "urgent"
    # Still there after peek.
    assert steer.has_hard_steer("t1") is True
    assert steer.peek_hard_steer("t1") == "urgent"


def test_hard_pop_removes_head_fifo():
    steer.push_hard_steer("t1", "a")
    steer.push_hard_steer("t1", "b")
    assert steer.pop_hard_steer("t1") == "a"
    assert steer.pop_hard_steer("t1") == "b"
    assert steer.pop_hard_steer("t1") is None
    assert steer.has_hard_steer("t1") is False


def test_hard_pop_cleans_up_empty_deque():
    steer.push_hard_steer("t1", "only")
    assert steer.pop_hard_steer("t1") == "only"
    # Internal deque should be removed, not left empty.
    assert "t1" not in steer._hard_steer_pending


def test_hard_peek_unknown_thread_is_none():
    assert steer.peek_hard_steer("never") is None
    assert steer.has_hard_steer("never") is False


# ── Clear (abort) ────────────────────────────────────────────────────


def test_clear_all_drops_both_soft_and_hard():
    steer.push_soft_steer("t1", "soft note")
    steer.push_hard_steer("t1", "hard note")
    steer.clear_all_steers("t1")
    assert steer.drain_soft_steers("t1") == []
    assert steer.peek_hard_steer("t1") is None


# ── Framing helpers ──────────────────────────────────────────────────


def test_framing_prefixes_are_present():
    assert steer.soft_steer_note("X").startswith("[KAZMA STEER]")
    assert "X" in steer.soft_steer_note("X")
    assert steer.hard_steer_note("Y").startswith("[KAZMA STEER!]")
    assert "Y" in steer.hard_steer_note("Y")
    assert steer.abort_marker().startswith("[KAZMA ABORT]")
    assert "abandoned" in steer.abort_marker().lower()


def test_hard_steer_payload_shape():
    p = steer.hard_steer_payload("the text")
    assert p["type"] == "hard_steer"
    assert p["text"] == "the text"
    assert "message" in p


# ── is_hard_steer_interrupt (snapshot discriminator) ─────────────────


def _fake_snapshot(*, next_nodes=(), interrupts=None):
    """Build a minimal fake LangGraph StateSnapshot."""
    tasks = []
    for value in interrupts or []:
        intr = types.SimpleNamespace(value=value)
        tasks.append(types.SimpleNamespace(interrupts=[intr]))
    return types.SimpleNamespace(next=tuple(next_nodes), tasks=tasks)


def test_interrupt_detected_when_paused_on_hard_steer():
    snap = _fake_snapshot(next_nodes=("supervisor",), interrupts=[steer.hard_steer_payload("hello")])
    assert steer.is_hard_steer_interrupt(snap) == "hello"


def test_interrupt_ignored_for_unrelated_pause():
    other = {"type": "hitl_approval", "kind": "security", "tool": "x"}
    snap = _fake_snapshot(next_nodes=("tool_worker",), interrupts=[other])
    assert steer.is_hard_steer_interrupt(snap) is None


def test_interrupt_ignored_when_graph_finished():
    snap = _fake_snapshot(next_nodes=(), interrupts=[steer.hard_steer_payload("hello")])
    assert steer.is_hard_steer_interrupt(snap) is None


def test_interrupt_none_snapshot():
    assert steer.is_hard_steer_interrupt(None) is None
