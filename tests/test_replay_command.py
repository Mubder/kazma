"""Tests for /replay slash command.

6 tests:
    1. test_replay_list_when_available     — shows snapshot list
    2. test_replay_list_when_unavailable   — shows friendly fallback
    3. test_replay_iteration_valid         — replays from valid iteration
    4. test_replay_iteration_invalid       — error on invalid iteration
    5. test_replay_compare                 — compares two iterations
    6. test_replay_clear                   — clears snapshots
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import kazma_gateway.slash_commands as sc

# ── Helpers ──────────────────────────────────────────────────────────

def _make_engine_class(snapshots=None, replay_result="replayed", compare_result="diff", clear_count=2):
    """Return a mock ReplayEngine class."""
    mock_cls = MagicMock()
    instance = mock_cls.return_value
    instance.list_snapshots.return_value = snapshots if snapshots is not None else []
    instance.replay.return_value = replay_result
    instance.compare.return_value = compare_result
    instance.clear_snapshots.return_value = clear_count
    return mock_cls


def _reset_cache():
    """Reset the lazy-import cache so each test starts fresh."""
    sc._ReplayEngine = None
    sc._replay_import_attempted = False


# ── Tests ────────────────────────────────────────────────────────────


class TestReplayCommand:
    """Tests for /replay slash command."""

    def test_replay_list_when_available(self):
        """Test 1: /replay list shows snapshot list when engine is available."""
        _reset_cache()
        snapshots = [
            {"iteration": 1, "timestamp": "2026-06-26T10:00:00", "description": "initial"},
            {"iteration": 2, "timestamp": "2026-06-26T10:05:00", "description": "updated"},
        ]
        mock_engine = _make_engine_class(snapshots=snapshots)

        with patch.object(sc, "_get_replay_engine", return_value=mock_engine):
            result = sc.resolve_slash_command("/replay list", {"thread_id": "t1"})

        assert result is not None
        assert "Iteration `1`" in result
        assert "Iteration `2`" in result
        assert "initial" in result
        assert "updated" in result

    def test_replay_list_when_unavailable(self):
        """Test 2: /replay shows friendly fallback when engine is not available."""
        _reset_cache()

        with patch.object(sc, "_get_replay_engine", return_value=None):
            result = sc.resolve_slash_command("/replay list")

        assert result is not None
        assert "Time travel not yet available" in result

    def test_replay_iteration_valid(self):
        """Test 3: /replay <iteration> replays from valid iteration."""
        _reset_cache()
        mock_engine = _make_engine_class(replay_result="state at iteration 3")

        with patch.object(sc, "_get_replay_engine", return_value=mock_engine):
            result = sc.resolve_slash_command("/replay 3", {"thread_id": "t1"})

        assert result is not None
        assert "Replay from iteration 3" in result
        assert "state at iteration 3" in result
        mock_engine.return_value.replay.assert_called_once_with(3)

    def test_replay_iteration_invalid(self):
        """Test 4: /replay <invalid> shows error on invalid iteration."""
        _reset_cache()
        mock_engine = _make_engine_class()

        with patch.object(sc, "_get_replay_engine", return_value=mock_engine):
            result = sc.resolve_slash_command("/replay abc", {"thread_id": "t1"})

        assert result is not None
        assert "Invalid iteration" in result
        assert "abc" in result
        mock_engine.return_value.replay.assert_not_called()

    def test_replay_compare(self):
        """Test 5: /replay compare <a> <b> compares two iterations."""
        _reset_cache()
        mock_engine = _make_engine_class(compare_result="iter 1: A\niter 3: B")

        with patch.object(sc, "_get_replay_engine", return_value=mock_engine):
            result = sc.resolve_slash_command("/replay compare 1 3", {"thread_id": "t1"})

        assert result is not None
        assert "Comparison: iteration 1 vs 3" in result
        assert "iter 1: A" in result
        mock_engine.return_value.compare.assert_called_once_with(1, 3)

    def test_replay_clear(self):
        """Test 6: /replay clear clears snapshots for current thread."""
        _reset_cache()
        mock_engine = _make_engine_class(clear_count=5)

        with patch.object(sc, "_get_replay_engine", return_value=mock_engine):
            result = sc.resolve_slash_command("/replay clear", {"thread_id": "t1"})

        assert result is not None
        assert "Cleared 5 snapshot(s)" in result
        mock_engine.return_value.clear_snapshots.assert_called_once()
