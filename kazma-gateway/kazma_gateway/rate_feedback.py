"""Rate Limit User Feedback — Friendly throttling messages with cooldown.

When a user hits inbound rate limits, instead of silently dropping the
message, send a feedback message showing remaining capacity and reset time.

Usage:
    from kazma_gateway.rate_feedback import RateFeedbackManager

    rfm = RateFeedbackManager(limit=30, window_seconds=60, cooldown_seconds=30)
    if rfm.is_limited("telegram:12345"):
        feedback = rfm.get_feedback("telegram:12345")
        # send feedback to user
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class _UserBucket:
    """Per-user token bucket for rate limiting."""

    tokens: float
    last_refill: float
    last_feedback: float = 0.0


class RateFeedbackManager:
    """Inbound rate limiter with user-friendly feedback messages.

    Args:
        limit:            Max requests per window (default 30).
        window_seconds:   Window duration in seconds (default 60).
        cooldown_seconds: Minimum seconds between feedback messages (default 30).
    """

    def __init__(
        self,
        limit: int = 30,
        window_seconds: int = 60,
        cooldown_seconds: int = 30,
        enabled: bool = True,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._cooldown = cooldown_seconds
        self._enabled = enabled
        self._buckets: dict[str, _UserBucket] = {}

    def _refill(self, bucket: _UserBucket) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        refill_rate = self._limit / self._window
        bucket.tokens = min(self._limit, bucket.tokens + elapsed * refill_rate)
        bucket.last_refill = now

    def _get_bucket(self, user_id: str) -> _UserBucket:
        """Get or create a bucket for a user."""
        if user_id not in self._buckets:
            self._buckets[user_id] = _UserBucket(
                tokens=float(self._limit),
                last_refill=time.monotonic(),
            )
        return self._buckets[user_id]

    def is_limited(self, user_id: str) -> bool:
        """Check if a user is currently rate-limited.

        If not limited, consumes one token. If limited, returns True
        without consuming.
        """
        if not self._enabled:
            return False

        bucket = self._get_bucket(user_id)
        self._refill(bucket)

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return False
        return True

    def get_remaining(self, user_id: str) -> int:
        """Get remaining tokens for a user (after refill)."""
        bucket = self._get_bucket(user_id)
        self._refill(bucket)
        return max(0, int(bucket.tokens))

    def get_reset_seconds(self, user_id: str) -> int:
        """Get seconds until the user has at least 1 token available."""
        bucket = self._get_bucket(user_id)
        self._refill(bucket)
        if bucket.tokens >= 1:
            return 0
        deficit = 1 - bucket.tokens
        refill_rate = self._limit / self._window
        return max(1, int(deficit / refill_rate))

    def should_send_feedback(self, user_id: str) -> bool:
        """Check if we should send feedback (respects cooldown)."""
        bucket = self._get_bucket(user_id)
        now = time.monotonic()
        if now - bucket.last_feedback >= self._cooldown:
            bucket.last_feedback = now
            return True
        return False

    def get_feedback_message(self, user_id: str) -> str:
        """Generate a user-friendly rate limit feedback message."""
        remaining = self.get_remaining(user_id)
        reset = self.get_reset_seconds(user_id)
        return f"⏳ Slow down — {remaining}/{self._limit} requests available. Resets in {reset}s."

    def record_feedback(self, user_id: str) -> None:
        """Record that feedback was sent (update cooldown timer)."""
        bucket = self._get_bucket(user_id)
        bucket.last_feedback = time.monotonic()
