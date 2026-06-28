"""Tests for rate limit user feedback."""

from __future__ import annotations

from kazma_gateway.rate_feedback import RateFeedbackManager


class TestRateFeedback:
    """Tests for RateFeedbackManager."""

    def test_feedback_on_rate_limit(self) -> None:
        """Feedback message is generated when user is throttled."""
        rfm = RateFeedbackManager(limit=2, window_seconds=60, cooldown_seconds=0)

        # Consume all tokens
        assert not rfm.is_limited("user:1")  # token 1
        assert not rfm.is_limited("user:1")  # token 2
        assert rfm.is_limited("user:1")  # limited!

        # Should generate feedback
        assert rfm.should_send_feedback("user:1")
        msg = rfm.get_feedback_message("user:1")
        assert "Slow down" in msg
        assert "requests available" in msg

    def test_feedback_cooldown(self) -> None:
        """Second feedback within cooldown period is suppressed."""
        rfm = RateFeedbackManager(limit=1, window_seconds=60, cooldown_seconds=30)

        # Exhaust token
        assert not rfm.is_limited("user:2")
        assert rfm.is_limited("user:2")

        # First feedback — should be allowed
        assert rfm.should_send_feedback("user:2")
        rfm.record_feedback("user:2")

        # Second feedback within cooldown — should be suppressed
        assert not rfm.should_send_feedback("user:2")

    def test_feedback_message_format(self) -> None:
        """Feedback message has correct remaining and reset info."""
        rfm = RateFeedbackManager(limit=5, window_seconds=60, cooldown_seconds=0, enabled=False)

        msg = rfm.get_feedback_message("user:3")
        assert "5" in msg  # limit
        assert "Slow down" in msg
        assert "requests available" in msg

    def test_feedback_disabled(self) -> None:
        """When enabled=False, no user is rate-limited."""
        rfm = RateFeedbackManager(limit=1, window_seconds=60, cooldown_seconds=0, enabled=False)

        # Should never be limited
        assert not rfm.is_limited("user:4")
        assert not rfm.is_limited("user:4")
        assert not rfm.is_limited("user:4")

    def test_remaining_and_reset(self) -> None:
        """get_remaining and get_reset_seconds return correct values."""
        rfm = RateFeedbackManager(limit=3, window_seconds=60, cooldown_seconds=0)

        # Consume all
        rfm.is_limited("user:5")
        rfm.is_limited("user:5")
        rfm.is_limited("user:5")

        assert rfm.get_remaining("user:5") == 0
        assert rfm.get_reset_seconds("user:5") > 0

    def test_separate_users_independent(self) -> None:
        """Rate limits are per-user, not global."""
        rfm = RateFeedbackManager(limit=1, window_seconds=60, cooldown_seconds=0)

        rfm.is_limited("user:a")  # consume user:a's token
        assert rfm.is_limited("user:a")  # user:a limited
        assert not rfm.is_limited("user:b")  # user:b still has token
