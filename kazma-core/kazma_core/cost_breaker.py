"""Cost Circuit Breaker — Halts agent if cost exceeds threshold without user interaction.

Prevents runaway spending by enforcing a hard cost ceiling. The agent is
halted if total cost exceeds the threshold AND no user interaction has
occurred for the configured timeout window.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Default: $0.50 max cost, 5 minute user silence window
DEFAULT_MAX_COST = 0.50
DEFAULT_SILENCE_WINDOW_SECONDS = 300  # 5 minutes


@dataclass
class CostCircuitBreaker:
    """Halts agent if cost exceeds threshold without user interaction.

    The breaker trips when:
    1. Total accumulated cost >= max_cost, AND
    2. No user interaction for >= silence_window_seconds

    This prevents the agent from burning through budget autonomously
    without any human engagement.

    Attributes:
        max_cost: Maximum allowed cost in USD before the breaker can trip.
        silence_window_seconds: How long (seconds) without user interaction
            before the breaker trips.
        current_cost: Running total of accumulated cost.
        last_user_interaction: Timestamp of the last user message.
        _halted: Whether the breaker has tripped.
    """

    max_cost: float = field(default_factory=lambda: float(os.getenv("KAZMA_MAX_COST", str(DEFAULT_MAX_COST))))
    silence_window_seconds: float = field(
        default_factory=lambda: float(os.getenv("KAZMA_SILENCE_WINDOW", str(DEFAULT_SILENCE_WINDOW_SECONDS)))
    )
    current_cost: float = 0.0
    last_user_interaction: float = field(default_factory=time.time)
    _halted: bool = field(default=False, init=False, repr=False)

    def record_cost(self, amount: float) -> None:
        """Record LLM API cost.

        Args:
            amount: Dollar cost of this API call (must be >= 0).
        """
        if amount < 0:
            raise ValueError(f"Cost amount must be non-negative, got {amount}")
        self.current_cost += amount
        logger.debug(
            "Cost recorded: $%.4f (total: $%.4f / $%.2f)",
            amount,
            self.current_cost,
            self.max_cost,
        )
        if self.current_cost >= self.max_cost:
            logger.warning(
                "Cost threshold reached: $%.4f >= $%.2f",
                self.current_cost,
                self.max_cost,
            )

    def record_user_interaction(self) -> None:
        """Reset the silence timer on user message.

        Call this whenever a user sends a message to the agent.
        This prevents the breaker from tripping while the user is
        actively engaged.
        """
        self.last_user_interaction = time.time()
        # If user interacts while halted, un-halt (user override)
        if self._halted:
            logger.info("User interaction while halted — resetting breaker")
            self._halted = False

    def should_halt(self) -> bool:
        """Return True if cost exceeded AND no user interaction for silence window.

        The breaker trips only when BOTH conditions are met:
        1. current_cost >= max_cost
        2. time since last_user_interaction >= silence_window_seconds
        """
        if self._halted:
            return True

        if self.current_cost < self.max_cost:
            return False

        silence_duration = time.time() - self.last_user_interaction
        if silence_duration >= self.silence_window_seconds:
            self._halted = True
            logger.warning(
                "Circuit breaker TRIPPED: $%.4f spent, %.0fs since last user interaction",
                self.current_cost,
                silence_duration,
            )
            return True

        return False

    def reset(self) -> None:
        """Reset the breaker to initial state.

        Call this at session start or when the user explicitly resets.
        """
        self.current_cost = 0.0
        self.last_user_interaction = time.time()
        self._halted = False
        logger.info("Circuit breaker reset")

    @property
    def is_halted(self) -> bool:
        """Whether the breaker has tripped."""
        return self._halted

    @property
    def cost_headroom(self) -> float:
        """Remaining budget before the breaker can trip (USD)."""
        return max(0.0, self.max_cost - self.current_cost)

    @property
    def silence_remaining(self) -> float:
        """Seconds of silence remaining before the breaker trips (if over budget)."""
        if self.current_cost < self.max_cost:
            return float("inf")
        elapsed = time.time() - self.last_user_interaction
        return max(0.0, self.silence_window_seconds - elapsed)

    def status(self) -> dict[str, float | bool]:
        """Return current breaker status for observability / dashboard.

        Returns a dict with:
            - current_cost: Accumulated cost in USD
            - max_cost: Cost threshold in USD
            - cost_headroom: Remaining budget in USD
            - is_halted: Whether the breaker has tripped
            - seconds_since_user: Time since last user interaction
            - silence_remaining: Seconds until breaker trips (inf if under budget)
        """
        return {
            "current_cost": self.current_cost,
            "max_cost": self.max_cost,
            "cost_headroom": self.cost_headroom,
            "is_halted": self._halted,
            "seconds_since_user": time.time() - self.last_user_interaction,
            "silence_remaining": self.silence_remaining,
        }


def create_cost_breaker(
    max_cost: float | None = None,
    silence_window: float | None = None,
) -> CostCircuitBreaker:
    """Factory to create a CostCircuitBreaker with configured thresholds.

    Args:
        max_cost: Maximum cost in USD. Defaults to KAZMA_MAX_COST env or 0.50.
        silence_window: Silence window in seconds. Defaults to KAZMA_SILENCE_WINDOW env or 300.
    """
    kwargs: dict[str, float] = {}
    if max_cost is not None:
        kwargs["max_cost"] = max_cost
    if silence_window is not None:
        kwargs["silence_window_seconds"] = silence_window
    return CostCircuitBreaker(**kwargs)
