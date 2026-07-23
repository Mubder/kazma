"""Tests for /replay slash command — real ReplayEngine/SnapshotRecorder API.

Tests:
    1. test_replay_list_when_available     — shows snapshot list
    2. test_replay_list_when_unavailable   — shows friendly fallback
    3. test_replay_numeric_falls_through   — /replay <n> returns None (graph handler)
    4. test_replay_invalid_subcommand      — error on unknown sub-command
    5. test_replay_compare                 — compares two snapshots via real API
    6. test_replay_clear                   — clears snapshots for current thread
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import kazma_gateway.slash_commands as sc

# ── Helpers ──────────────────────────────────────────────────────────


def _make_snapshot(iteration: int, model: str = "gpt-4o-mini") -> "object":
    """Build a real SnapshotRecord-like object with the correct attributes."""
    from kazma_core.time_travel import SnapshotRecord

    return SnapshotRecord(
        thread_id="t1",
        iteration=iteration,
        state_json=json.dumps({"messages": [{"role": "user", "content": "hi"}], "last_model": model}),
        timestamp=f"2026-07-23T10:0{iteration}:00",
        model_used=model,
    )


def _make_state(iteration: int, model: str = "gpt-4o-mini", cost: float = 0.001) -> dict:
    """Build a state dict matching what ReplayEngine.replay_from returns."""
    return {
        "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "iteration": iteration,
        "last_model": model,
        "last_cost_usd": cost,
        "next_node": "respond",
        "tool_calls_pending": [],
        "tool_calls_done": [],
    }


def _make_components(snapshots=None, state=None):
    """Return (recorder_mock, engine_mock) matching the real API surface."""
    recorder = MagicMock()
    recorder.list_snapshots.return_value = snapshots or []
    recorder.clear_snapshots.return_value = 2

    engine = MagicMock()
    engine.replay_from.return_value = state
    # compare_replays is a staticmethod — just set it as a callable.
    engine.compare_replays = lambda a, b: {
        "original_iteration": a.get("iteration", 0),
        "replayed_iteration": b.get("iteration", 0),
        "iteration_delta": b.get("iteration", 0) - a.get("iteration", 0),
        "original_message_count": len(a.get("messages", [])),
        "replayed_message_count": len(b.get("messages", [])),
        "message_count_delta": len(b.get("messages", [])) - len(a.get("messages", [])),
        "original_model": a.get("last_model", ""),
        "replayed_model": b.get("last_model", ""),
        "model_changed": a.get("last_model", "") != b.get("last_model", ""),
        "original_cost_usd": a.get("last_cost_usd", 0.0),
        "replayed_cost_usd": b.get("last_cost_usd", 0.0),
        "cost_delta_usd": b.get("last_cost_usd", 0.0) - a.get("last_cost_usd", 0.0),
        "original_tool_calls": len(a.get("tool_calls_pending", [])) + len(a.get("tool_calls_done", [])),
        "replayed_tool_calls": len(b.get("tool_calls_pending", [])) + len(b.get("tool_calls_done", [])),
        "tool_calls_delta": 0,
        "original_next_node": a.get("next_node", ""),
        "replayed_next_node": b.get("next_node", ""),
        "routing_changed": False,
        "identical": a == b,
    }
    return recorder, engine


def _reset_cache():
    """Reset the lazy-import cache so each test starts fresh."""
    sc._replay_recorder = None
    sc._replay_engine = None
    sc._replay_import_attempted = False


# ── Tests ────────────────────────────────────────────────────────────


class TestReplayCommand:
    """Tests for /replay slash command against the real engine API."""

    def test_replay_list_when_available(self):
        """Test 1: /replay list shows snapshot list when components are available."""
        _reset_cache()
        snaps = [_make_snapshot(1, "gpt-4o-mini"), _make_snapshot(2, "groq/compound-mini")]
        recorder, engine = _make_components(snapshots=snaps)

        with patch.object(sc, "_get_replay_components", return_value=(recorder, engine)):
            result = sc.resolve_slash_command("/replay list", {"thread_id": "t1"})

        assert result is not None
        assert "Iteration `1`" in result
        assert "Iteration `2`" in result
        assert "gpt-4o-mini" in result
        recorder.list_snapshots.assert_called_once_with("t1")

    def test_replay_list_when_unavailable(self):
        """Test 2: /replay shows friendly fallback when components not available."""
        _reset_cache()

        with patch.object(sc, "_get_replay_components", return_value=(None, None)):
            result = sc.resolve_slash_command("/replay list")

        assert result is not None
        assert "Time travel not yet available" in result

    def test_replay_numeric_falls_through(self):
        """Test 3: /replay <n> returns None so the graph handler can restore."""
        _reset_cache()
        recorder, engine = _make_components(state=_make_state(3))

        with patch.object(sc, "_get_replay_components", return_value=(recorder, engine)):
            result = sc.resolve_slash_command("/replay 3", {"thread_id": "t1"})

        # Must return None — the graph handler (_handle_replay) does the restore.
        assert result is None

    def test_replay_invalid_subcommand(self):
        """Test 4: /replay <non-numeric> shows error for unknown sub-command."""
        _reset_cache()
        recorder, engine = _make_components()

        with patch.object(sc, "_get_replay_components", return_value=(recorder, engine)):
            result = sc.resolve_slash_command("/replay frobnicate", {"thread_id": "t1"})

        assert result is not None
        assert "Unknown" in result or "unknown" in result
        assert "frobnicate" in result

    def test_replay_compare(self):
        """Test 5: /replay compare <a> <b> diffs two snapshots via real API."""
        _reset_cache()
        state_a = _make_state(1, model="gpt-4o-mini", cost=0.001)
        state_b = _make_state(3, model="groq/compound-mini", cost=0.002)
        recorder, engine = _make_components(state=state_a)
        # replay_from is called twice — return a then b
        engine.replay_from.side_effect = [state_a, state_b]

        with patch.object(sc, "_get_replay_components", return_value=(recorder, engine)):
            result = sc.resolve_slash_command("/replay compare 1 3", {"thread_id": "t1"})

        assert result is not None
        assert "Comparison: iteration 1 vs 3" in result
        assert "Messages" in result
        assert "Model" in result
        # replay_from called for both iterations
        assert engine.replay_from.call_count == 2

    def test_replay_clear(self):
        """Test 6: /replay clear clears snapshots for current thread."""
        _reset_cache()
        recorder, engine = _make_components()
        recorder.clear_snapshots.return_value = 5

        with patch.object(sc, "_get_replay_components", return_value=(recorder, engine)):
            result = sc.resolve_slash_command("/replay clear", {"thread_id": "t1"})

        assert result is not None
        assert "Cleared 5 snapshot(s)" in result
        recorder.clear_snapshots.assert_called_once_with("t1")
