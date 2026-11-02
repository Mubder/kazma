"""Rate Limit User Feedback — Friendly throttling messages with cooldown.

When a user hits inbound rate limits, instead of silently dropping the
message, send a feedback message showing remaining capacity and reset time.

Usage:
    from kazma_gateway.rate_feedback import RateFeedbackManager

    rfm = RateFeedbackManager(limit=30, window_seconds=60, cooldown_seconds=30)
    if rfm.is_limited("telegram:12345"):
        feedback = rfm.get_feedback_message("telegram:12345")
        # send feedback to user
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kazma_core.english_count import count_noun

__all__ = [
    "RateFeedbackManager",
    "follow_rate_limit_settings",
]

logger = logging.getLogger(__name__)

#: The setting: messages per person per minute, per chat app.
RATE_LIMITS_KEY = "gateway.rate_limits"
_PLATFORMS = ("telegram", "discord", "slack")


@dataclass
class _UserBucket:
    """Per-user token bucket for rate limiting."""

    tokens: float
    last_refill: float
    last_feedback: float = 0.0


class RateFeedbackManager:
    """Inbound rate limiter with user-friendly feedback messages.

    Args:
        limit:            Max requests per window (default 30) or dictionary of platform limits.
        window_seconds:   Window duration in seconds (default 60).
        cooldown_seconds: Minimum seconds between feedback messages (default 30).
    """

    def __init__(
        self,
        limit: int | dict[str, int] = 30,
        window_seconds: int = 60,
        cooldown_seconds: int = 30,
        enabled: bool = True,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._cooldown = cooldown_seconds
        self._enabled = enabled
        self._buckets: dict[str, _UserBucket] = {}

    def set_limits(self, limit: int | dict[str, int]) -> None:
        """Take new limits now; each person's bucket keeps its count."""
        self._limit = limit

    def _get_limit_for_user(self, user_id: str) -> int:
        """Get the limit configured for a specific user ID's platform."""
        if isinstance(self._limit, dict):
            platform = user_id.split(":", 1)[0] if ":" in user_id else "telegram"
            return self._limit.get(platform, self._limit.get("default", 30))
        return self._limit

    def _unlimited(self, user_id: str) -> bool:
        """A limit (or window) of 0 or less means no limit for that platform.

        An operator who set ``gateway.rate_limits.<platform>: 0`` meant "off";
        the bucket then refilled at 0 a second and ``get_reset_seconds``
        divided by it, killing the gateway's consumer at the next message, so
        every chat app stopped answering (audit 2026-10-26 AUD-033).
        """
        return self._window <= 0 or self._get_limit_for_user(user_id) <= 0

    def _refill(self, user_id: str, bucket: _UserBucket) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        if self._unlimited(user_id):
            bucket.last_refill = now
            return
        limit = self._get_limit_for_user(user_id)
        refill_rate = limit / self._window
        bucket.tokens = min(float(limit), bucket.tokens + elapsed * refill_rate)
        bucket.last_refill = now

    def _get_bucket(self, user_id: str) -> _UserBucket:
        """Get or create a bucket for a user."""
        if user_id not in self._buckets:
            # Periodic cleanup: remove stale buckets when dict grows large
            if len(self._buckets) > 1000:
                now = time.monotonic()
                stale = [
                    uid for uid, b in self._buckets.items()
                    if (now - b.last_refill) > self._window * 10
                ]
                for uid in stale:
                    del self._buckets[uid]
            limit = self._get_limit_for_user(user_id)
            self._buckets[user_id] = _UserBucket(
                tokens=float(limit),
                last_refill=time.monotonic(),
            )
        return self._buckets[user_id]

    def is_limited(self, user_id: str) -> bool:
        """Check if a user is currently rate-limited.

        If not limited, consumes one token. If limited, returns True
        without consuming.
        """
        if not self._enabled or self._unlimited(user_id):
            return False

        bucket = self._get_bucket(user_id)
        self._refill(user_id, bucket)

        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return False
        return True

    def get_remaining(self, user_id: str) -> int:
        """Get remaining tokens for a user (after refill)."""
        bucket = self._get_bucket(user_id)
        self._refill(user_id, bucket)
        return max(0, int(bucket.tokens))

    def get_reset_seconds(self, user_id: str) -> int:
        """Get seconds until the user has at least 1 token available."""
        bucket = self._get_bucket(user_id)
        self._refill(user_id, bucket)
        if bucket.tokens >= 1 or self._unlimited(user_id):
            return 0
        deficit = 1 - bucket.tokens
        limit = self._get_limit_for_user(user_id)
        refill_rate = limit / self._window
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
        limit = self._get_limit_for_user(user_id)
        return f"⏳ Slow down — {remaining}/{count_noun(limit, 'request')} available. Resets in {reset}s."


def _limits_from(config_store: Any) -> dict[str, int]:
    """The per-platform limits the setting holds (an empty dict: none set)."""
    raw = config_store.get(RATE_LIMITS_KEY, {}) or {}
    limits: dict[str, int] = {}
    if isinstance(raw, dict):
        for platform in _PLATFORMS:
            if platform in raw:
                try:
                    limits[platform] = int(raw[platform])
                except (TypeError, ValueError):
                    logger.warning("[Gateway] %s.%s is not a number: %r", RATE_LIMITS_KEY, platform, raw[platform])
    return limits


def follow_rate_limit_settings(gateway: Any, config_store: Any) -> Callable[[], None]:
    """Apply ``gateway.rate_limits`` to *gateway* now and on every change.

    The limits were read once when the server started (2026-11, FA-127): a
    limit changed in Settings did nothing until a restart. Returns the call
    that stops following.
    """
    def apply() -> None:
        limits = _limits_from(config_store)
        current = getattr(gateway, "_rate_feedback", None)
        if not limits:
            if current is not None:
                gateway.set_rate_feedback(None)
                logger.info("[Gateway] rate feedback off (no %s)", RATE_LIMITS_KEY)
            return
        if isinstance(current, RateFeedbackManager):
            current.set_limits(limits)
            logger.info("[Gateway] rate limits now %s", limits)
            return
        gateway.set_rate_feedback(RateFeedbackManager(limit=limits, window_seconds=60, cooldown_seconds=30))
        logger.info("[Gateway] Rate feedback wired (limits=%s, cooldown=30s)", limits)

    def on_change(keys: Any) -> None:
        if keys is None or any(k == RATE_LIMITS_KEY or str(k).startswith(RATE_LIMITS_KEY + ".") for k in keys):
            apply()

    apply()
    return config_store.add_change_listener(on_change)
