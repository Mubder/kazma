"""Conversation Summarization Worker (#9).

After every ~50 turns, compresses the pipeline_logs.db context into
a concise State Summary entry stored in the Episodic Memory layer.

Usage in SwarmEngine:
    engine.summary_worker = SummaryWorker(interval=50)
    # ... after each turn ...
    await engine.summary_worker.maybe_summarize(engine)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SummaryWorker:
    """Periodic context compression worker.

    Triggers every ``interval`` turns.  Queries the pipeline_logs.db
    for recent activity, passes it through the LLM for summarization,
    and stores the result as an episodic memory entry.
    """

    def __init__(self, interval: int = 50) -> None:
        self.interval = interval
        self._turn_count: int = 0
        self._last_summary: str = ""

    async def maybe_summarize(self, engine: Any) -> str | None:
        """Check if a summary is due and run it.

        Args:
            engine: SwarmEngine instance for accessing logger and memory.

        Returns:
            The summary text if one was generated, None otherwise.
        """
        self._turn_count += 1
        if self._turn_count % self.interval != 0:
            return None

        logger.info("[SummaryWorker] Generating summary after %d turns", self._turn_count)
        try:
            # Collect recent pipeline logs
            from kazma_core.swarm.memory.pipeline_logger import get_pipeline_logger
            plog = get_pipeline_logger()
            recent = plog.recent(limit=self.interval)

            context = "\n".join(
                f"[{e.get('worker_name', '?')}/{e.get('stage', '?')}] {e.get('message', '')[:200]}"
                for e in recent
            ) or "No recent activity."

            # Generate summary via LLM
            from kazma_core.model_registry import get_model_registry
            provider = get_model_registry().get_client()
            if provider is None:
                logger.warning("[SummaryWorker] No provider available")
                return None

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a conversation summarizer. Compress the following "
                        "pipeline activity log into a concise 3-5 bullet point "
                        "State Summary. Focus on decisions made, tasks completed, "
                        "errors encountered, and key insights."
                    ),
                },
                {"role": "user", "content": f"Summarize this activity:\n{context}"},
            ]
            response = await provider.chat(messages)
            summary = response.content if hasattr(response, "content") else str(response)

            # Store in episodic memory
            try:
                from kazma_core.swarm.memory.adapter import get_adapter
                adapter = get_adapter()
                if adapter is not None:
                    await adapter.log_evolution(
                        task_id=f"summary-{self._turn_count}",
                        worker_name="summary",
                        summary=summary[:300],
                        delta="",
                    )
            except Exception:
                pass

            self._last_summary = summary
            logger.info("[SummaryWorker] Summary stored (%d chars)", len(summary))
            return summary
        except Exception as exc:
            logger.warning("[SummaryWorker] Summarization failed: %s", exc)
            return None

    @property
    def last_summary(self) -> str:
        return self._last_summary
