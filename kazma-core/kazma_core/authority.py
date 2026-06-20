"""Context Authority — Enforces the 80% context compaction rule.

This module provides the runtime layer that monitors token usage and
automatically triggers compaction before context window degradation occurs.
The 80% threshold is hardcoded and NOT configurable.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.state import AgentState
from kazma_core.token_counter import TokenCounter
from kazma_core.compaction import CompactionEngine

logger = logging.getLogger(__name__)


class ContextAuthority:
    """Enforces the 80% context compaction rule.

    This is the authoritative gatekeeper that sits between the agent loop
    and the compaction engine. It checks token usage against the hardcoded
    80% threshold and triggers compaction when exceeded.
    """

    def __init__(self, counter: TokenCounter, compactor: CompactionEngine) -> None:
        self.counter = counter
        self.compactor = compactor

    @property
    def threshold(self) -> int:
        """The 80% token threshold (for monitoring / observability)."""
        return self.counter.threshold

    async def check_and_enforce(self, state: AgentState) -> AgentState:
        """Check token count, compact if needed, return (possibly new) state.

        If the 80% threshold is exceeded, this method triggers compaction:
        pause → snapshot → summarize → fresh context. Otherwise, the state
        is returned unchanged.
        """
        messages = state.get("messages", [])

        if self.counter.should_compact(messages):
            logger.info(
                "Context compaction triggered: %d/%d tokens (threshold=%d)",
                self.counter.count(messages),
                self.counter.window,
                self.threshold,
            )
            new_state = await self.compactor.compact(state)
            new_tokens = self.counter.count(new_state.get("messages", []))
            logger.info(
                "Compaction complete: context reduced from %d to %d tokens",
                self.counter.count(messages),
                new_tokens,
            )
            return new_state

        logger.debug(
            "Context OK: %d/%d tokens (threshold=%d)",
            self.counter.count(messages),
            self.counter.window,
            self.threshold,
        )
        return state


def create_authority(
    model: str = "gpt-4",
    window: int = 128_000,
    llm_client: Any = None,
    checkpoint_manager: Any = None,
    memory_store: Any = None,
) -> ContextAuthority:
    """Factory to create a fully wired ContextAuthority.

    Args:
        model: The model name for token counting.
        window: The context window size in tokens.
        llm_client: LLM client for summarization during compaction.
        checkpoint_manager: CheckpointManager for state snapshots.
        memory_store: Memory store for post-compaction retrieval.
    """
    counter = TokenCounter(model=model, window=window)
    compactor = CompactionEngine(
        llm_client=llm_client,
        checkpoint_manager=checkpoint_manager,
        memory_store=memory_store,
    )
    return ContextAuthority(counter=counter, compactor=compactor)
