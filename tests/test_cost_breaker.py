"""Tests for CostCircuitBreaker — cost tracking and halt logic."""

from __future__ import annotations

import time

import pytest
from kazma_core.cost_breaker import (
    DEFAULT_MAX_COST,
    DEFAULT_SILENCE_WINDOW_SECONDS,
    CostCircuitBreaker,
    create_cost_breaker,
)


class TestCostCircuitBreakerInit:
    """Test breaker initialization."""

    def test_default_values(self):
        breaker = CostCircuitBreaker()
        assert breaker.max_cost == DEFAULT_MAX_COST
        assert breaker.silence_window_seconds == DEFAULT_SILENCE_WINDOW_SECONDS
        assert breaker.current_cost == 0.0
        assert breaker.is_halted is False

    def test_custom_values(self):
        breaker = CostCircuitBreaker(max_cost=1.0, silence_window_seconds=60)
        assert breaker.max_cost == 1.0
        assert breaker.silence_window_seconds == 60

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("KAZMA_MAX_COST", "2.00")
        monkeypatch.setenv("KAZMA_SILENCE_WINDOW", "120")
        breaker = CostCircuitBreaker()
        assert breaker.max_cost == 2.0
        assert breaker.silence_window_seconds == 120


class TestRecordCost:
    """Test cost recording."""

    def test_record_positive_cost(self):
        breaker = CostCircuitBreaker(max_cost=1.0)
        breaker.record_cost(0.10)
        assert breaker.current_cost == 0.10

    def test_record_cumulative_cost(self):
        breaker = CostCircuitBreaker(max_cost=1.0)
        breaker.record_cost(0.10)
        breaker.record_cost(0.20)
        assert breaker.current_cost == pytest.approx(0.30)

    def test_record_negative_cost_raises(self):
        breaker = CostCircuitBreaker()
        with pytest.raises(ValueError, match="non-negative"):
            breaker.record_cost(-0.05)

    def test_record_zero_cost(self):
        breaker = CostCircuitBreaker()
        breaker.record_cost(0.0)
        assert breaker.current_cost == 0.0


class TestRecordUserInteraction:
    """Test user interaction recording."""

    def test_updates_timestamp(self):
        breaker = CostCircuitBreaker()
        old_time = breaker.last_user_interaction
        time.sleep(0.01)
        breaker.record_user_interaction()
        assert breaker.last_user_interaction > old_time

    def test_unhalts_on_interaction(self):
        breaker = CostCircuitBreaker(max_cost=0.01, silence_window_seconds=0)
        breaker.record_cost(0.02)
        # Force halt
        assert breaker.should_halt() is True
        # User interacts — should un-halt
        breaker.record_user_interaction()
        assert breaker.is_halted is False


class TestShouldHalt:
    """Test halt logic."""

    def test_under_budget_no_halt(self):
        breaker = CostCircuitBreaker(max_cost=1.0, silence_window_seconds=0)
        breaker.record_cost(0.50)
        assert breaker.should_halt() is False

    def test_over_budget_no_silence_no_halt(self):
        breaker = CostCircuitBreaker(max_cost=0.10, silence_window_seconds=60)
        breaker.record_cost(0.20)
        # Just went over budget, user was just here
        assert breaker.should_halt() is False

    def test_over_budget_and_silence_halt(self):
        breaker = CostCircuitBreaker(max_cost=0.10, silence_window_seconds=0)
        breaker.record_cost(0.20)
        # silence_window=0 means immediate halt when over budget
        assert breaker.should_halt() is True

    def test_stays_halted(self):
        breaker = CostCircuitBreaker(max_cost=0.10, silence_window_seconds=0)
        breaker.record_cost(0.20)
        assert breaker.should_halt() is True
        # Should stay halted even on re-check
        assert breaker.should_halt() is True

    def test_user_interaction_resets(self):
        breaker = CostCircuitBreaker(max_cost=0.10, silence_window_seconds=60)
        breaker.record_cost(0.20)
        # Not halted yet (within silence window)
        assert breaker.should_halt() is False
        # User interacts — resets timer
        breaker.record_user_interaction()
        assert breaker.should_halt() is False


class TestReset:
    """Test breaker reset."""

    def test_reset_clears_state(self):
        breaker = CostCircuitBreaker(max_cost=0.10, silence_window_seconds=0)
        breaker.record_cost(0.20)
        breaker.should_halt()  # trips the breaker
        assert breaker.is_halted is True

        breaker.reset()
        assert breaker.current_cost == 0.0
        assert breaker.is_halted is False
        assert breaker.cost_headroom == 0.10


class TestStatusProperties:
    """Test status and properties."""

    def test_cost_headroom(self):
        breaker = CostCircuitBreaker(max_cost=1.0)
        breaker.record_cost(0.30)
        assert breaker.cost_headroom == pytest.approx(0.70)

    def test_cost_headroom_floor(self):
        breaker = CostCircuitBreaker(max_cost=0.10)
        breaker.record_cost(0.20)
        assert breaker.cost_headroom == 0.0

    def test_silence_remaining_under_budget(self):
        breaker = CostCircuitBreaker(max_cost=1.0, silence_window_seconds=60)
        breaker.record_cost(0.50)
        assert breaker.silence_remaining == float("inf")

    def test_silence_remaining_over_budget(self):
        breaker = CostCircuitBreaker(max_cost=0.10, silence_window_seconds=60)
        breaker.record_cost(0.20)
        remaining = breaker.silence_remaining
        assert 0 <= remaining <= 60

    def test_status_dict(self):
        breaker = CostCircuitBreaker(max_cost=0.50, silence_window_seconds=300)
        breaker.record_cost(0.25)
        status = breaker.status()
        assert status["current_cost"] == 0.25
        assert status["max_cost"] == 0.50
        assert status["cost_headroom"] == pytest.approx(0.25)
        assert status["is_halted"] is False
        assert isinstance(status["seconds_since_user"], float)
        assert status["silence_remaining"] == float("inf")


class TestCreateCostBreaker:
    """Test the factory function."""

    def test_create_default(self):
        breaker = create_cost_breaker()
        assert isinstance(breaker, CostCircuitBreaker)
        assert breaker.max_cost == DEFAULT_MAX_COST

    def test_create_custom(self):
        breaker = create_cost_breaker(max_cost=2.0, silence_window=120)
        assert breaker.max_cost == 2.0
        assert breaker.silence_window_seconds == 120
