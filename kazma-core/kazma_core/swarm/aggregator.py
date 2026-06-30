"""Result aggregation strategies for fan-out style swarm tasks."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kazma_core.swarm.task import SwarmTask, WorkerResult

logger = logging.getLogger(__name__)

SynthesisCallable = Callable[
    [SwarmTask, list[WorkerResult]],
    str | Awaitable[str],
]

_SYNTHESIS_SYSTEM_PROMPT = """You consolidate multiple worker responses into one answer.

Preserve the important technical points from each response, resolve obvious
conflicts, and write a concise unified answer. If a worker failed, ignore that
failed response. When you reference a point from a worker, explicitly name that
worker in the synthesis.
"""


@dataclass
class AggregationResult:
    """Normalized output returned by an aggregation strategy."""

    aggregated_output: str | None = None
    synthesized_output: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ResultAggregator:
    """Apply aggregation strategies to fan-out worker results."""

    def __init__(self, synthesizer: SynthesisCallable | None = None) -> None:
        self._synthesizer = synthesizer

    async def aggregate(
        self,
        task: SwarmTask,
        worker_results: list[WorkerResult],
    ) -> AggregationResult:
        """Aggregate *worker_results* according to *task.aggregation*."""
        strategy = (task.aggregation or "collect").strip().lower()
        metadata: dict[str, Any] = {
            "aggregation_strategy": strategy,
            "successful_workers": [
                result.worker for result in worker_results if result.status == "success"
            ],
            "failed_workers": [
                result.worker for result in worker_results if result.status != "success"
            ],
        }
        successful_results = [
            result for result in worker_results if result.status == "success"
        ]

        if strategy == "collect":
            return AggregationResult(metadata=metadata)
        if not successful_results:
            return AggregationResult(metadata=metadata)

        if strategy == "first_valid":
            selected = successful_results[0]
            metadata["selected_worker"] = selected.worker
            return AggregationResult(
                aggregated_output=selected.output,
                metadata=metadata,
            )

        if strategy == "merge_all":
            return AggregationResult(
                aggregated_output="\n\n".join(
                    f"[{result.worker}] {result.output}" for result in successful_results
                ),
                metadata=metadata,
            )

        if strategy == "vote":
            tally: dict[str, int] = {}
            first_seen_order: dict[str, int] = {}
            first_seen_output: dict[str, str] = {}
            first_seen_worker: dict[str, str] = {}

            for index, result in enumerate(successful_results):
                normalized_output = result.output.strip()
                tally[normalized_output] = tally.get(normalized_output, 0) + 1
                first_seen_order.setdefault(normalized_output, index)
                first_seen_output.setdefault(normalized_output, result.output)
                first_seen_worker.setdefault(normalized_output, result.worker)

            winner_key = max(
                tally,
                key=lambda candidate: (
                    tally[candidate],
                    -first_seen_order[candidate],
                ),
            )
            metadata["vote_tally"] = tally
            metadata["selected_worker"] = first_seen_worker[winner_key]
            return AggregationResult(
                aggregated_output=first_seen_output[winner_key],
                metadata=metadata,
            )

        if strategy == "synthesize":
            synthesized_output = await self.synthesize(task, successful_results)
            metadata["synthesized"] = True
            return AggregationResult(
                aggregated_output=synthesized_output,
                synthesized_output=synthesized_output,
                metadata=metadata,
            )

        raise ValueError(f"Unknown aggregation strategy: '{task.aggregation}'")

    async def synthesize(
        self,
        task: SwarmTask,
        successful_results: list[WorkerResult],
        *,
        system_prompt: str | None = None,
        final_instruction: str | None = None,
    ) -> str:
        """Generate a synthesized answer from successful worker results."""
        return await self._synthesize(
            task,
            successful_results,
            system_prompt=system_prompt,
            final_instruction=final_instruction,
        )

    async def _synthesize(
        self,
        task: SwarmTask,
        successful_results: list[WorkerResult],
        *,
        system_prompt: str | None = None,
        final_instruction: str | None = None,
    ) -> str:
        """Generate a consolidated answer from successful worker results."""
        if self._synthesizer is not None:
            synthesized = self._synthesizer(task, successful_results)
            if inspect.isawaitable(synthesized):
                return await synthesized
            return synthesized

        prompt_sections = [f"Task:\n{task.prompt}"]
        if task.context.strip():
            prompt_sections.append(f"Context:\n{task.context.strip()}")
        prompt_sections.append(
            "Worker responses:\n"
            + "\n\n".join(
                f"{result.worker}:\n{result.output}" for result in successful_results
            )
        )
        prompt_sections.append(
            final_instruction or "Produce a single consolidated answer."
        )
        prompt = "\n\n".join(prompt_sections)

        provider = None
        try:
            provider = _get_llm_provider()
            if provider is None:
                raise RuntimeError("LLM provider unavailable.")
            response = await provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt or _SYNTHESIS_SYSTEM_PROMPT,
                    },
                    {"role": "user", "content": prompt},
                ]
            )
            content = (response.content or "").strip()
            if content:
                return content
            raise RuntimeError("LLM returned an empty synthesis.")
        except Exception as exc:
            logger.warning("[ResultAggregator] synthesis fallback used: %s", exc)
            return self._fallback_synthesis(task, successful_results)
        finally:
            if provider is not None:
                # Do NOT close the shared provider client — the main agent may still need it
                pass  # await provider.close() was closing the process-wide httpx pool

    @staticmethod
    def _fallback_synthesis(
        task: SwarmTask,
        successful_results: list[WorkerResult],
    ) -> str:
        """Produce a deterministic synthesis if the LLM is unavailable."""
        bullet_points = "\n".join(
            f"- {result.worker}: {result.output}" for result in successful_results
        )
        return (
            f"Synthesized answer for: {task.prompt}\n\n"
            f"{bullet_points}"
        )


def _get_llm_provider() -> Any | None:
    """Return an LLM provider from the global registry."""
    try:
        from kazma_core.model_registry import get_model_registry
        registry = get_model_registry()
        return registry.get_client()
    except Exception:
        return None
