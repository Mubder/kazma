"""Result aggregation strategies for fan-out style swarm tasks."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kazma_core.swarm.task import SwarmTask, WorkerResult

__all__ = ["AggregationResult", "ResultAggregator", "Synthesis"]

logger = logging.getLogger(__name__)

#: The most a synthesis may write.
_SYNTHESIS_MAX_TOKENS = 4096

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
    #: What the synthesis's model call cost (SW-E15): the task's.
    synthesis_tokens: int = 0
    synthesis_cost: float = 0.0


@dataclass
class Synthesis:
    """A synthesized answer and what its model call cost (zero for a
    caller-supplied synthesizer or the fallback when the model is down)."""

    text: str
    tokens: int = 0
    cost: float = 0.0
    record: dict[str, Any] = field(default_factory=dict)


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
            synthesis = await self.synthesize_with_usage(task, successful_results)
            metadata["synthesized"] = True
            if synthesis.record:
                metadata["synthesis"] = synthesis.record
            return AggregationResult(
                aggregated_output=synthesis.text,
                synthesized_output=synthesis.text,
                metadata=metadata,
                synthesis_tokens=synthesis.tokens,
                synthesis_cost=synthesis.cost,
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
        return (await self.synthesize_with_usage(
            task,
            successful_results,
            system_prompt=system_prompt,
            final_instruction=final_instruction,
        )).text

    async def synthesize_with_usage(
        self,
        task: SwarmTask,
        successful_results: list[WorkerResult],
        *,
        system_prompt: str | None = None,
        final_instruction: str | None = None,
    ) -> Synthesis:
        """:meth:`synthesize` with what its model call cost (SW-E15)."""
        if self._synthesizer is not None:
            synthesized = self._synthesizer(task, successful_results)
            if inspect.isawaitable(synthesized):
                synthesized = await synthesized
            return Synthesis(text=synthesized)
        return await self._synthesize_by_model(
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
        return (await self.synthesize_with_usage(
            task, successful_results, system_prompt=system_prompt, final_instruction=final_instruction,
        )).text

    async def _synthesize_by_model(
        self,
        task: SwarmTask,
        successful_results: list[WorkerResult],
        *,
        system_prompt: str | None = None,
        final_instruction: str | None = None,
    ) -> Synthesis:
        """The model's consolidated answer (``task_model.call_task_model``:
        ledgered, its usage returned); the deterministic fallback, a WARNING
        and a counted failure when the model cannot answer."""
        prompt_sections = [f"Task:\n{task.prompt}"]
        if task.context.strip():
            prompt_sections.append(f"Context:\n{task.context.strip()}")
        # The workers' text reaches the synthesis model as data, never as its
        # instructions (Swarm audit SW-S10).
        from kazma_core.safety.prompt_fence import fence_untrusted

        prompt_sections.append(
            "Worker responses:\n"
            + "\n\n".join(
                f"{result.worker}:\n"
                + fence_untrusted(result.output or "", source=f"swarm_worker:{result.worker}")
                for result in successful_results
            )
        )
        prompt_sections.append(
            final_instruction or "Produce a single consolidated answer."
        )
        prompt = "\n\n".join(prompt_sections)

        # The shared provider client is never closed here: the main agent
        # still needs it (closing it closed the process-wide httpx pool).
        from kazma_core.swarm.dispatch_helpers import wait_timeout
        from kazma_core.swarm.task_model import call_task_model

        try:
            answer = await call_task_model(
                _get_llm_provider(),
                [{"role": "system", "content": system_prompt or _SYNTHESIS_SYSTEM_PROMPT},
                 {"role": "user", "content": prompt}],
                task_id=task.id, label="swarm-synthesis", max_tokens=_SYNTHESIS_MAX_TOKENS,
                deadline=wait_timeout(task.timeout),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[ResultAggregator] synthesis fallback used for task '%s': %s", task.id, exc)
            from kazma_core.metrics import record_swarm_failure

            record_swarm_failure("synthesis")
            return Synthesis(text=self._fallback_synthesis(task, successful_results),
                             record={"status": "fallback", "error": type(exc).__name__})
        return Synthesis(text=answer.text, tokens=answer.tokens, cost=answer.cost_usd,
                         record={"status": "synthesized", **answer.record()})

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
        from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

        registry = get_model_registry()
        if get_current_tenant_id():
            return registry.get_client()
        with tenant_scope("default"):
            return registry.get_client()
    except Exception:
        return None
