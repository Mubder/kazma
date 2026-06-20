"""Swarm Intelligence — Coordinates multiple agents for complex tasks.

Provides parallel execution, consensus building, and pipeline (cascade)
patterns for multi-agent collaboration.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from kazma_core.delegation.orchestrator import (
    DelegationOrchestrator,
    OrchestrationResult,
)
from kazma_core.delegation.protocol import (
    DelegationProtocol,
    DelegationResult,
    RequestStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class ConsensusResult:
    """Result of consensus-based multi-agent execution."""
    task_description: str
    responses: list[DelegationResult] = field(default_factory=list)
    consensus_reached: bool = False
    consensus_output: Any = None
    agreement_ratio: float = 0.0
    total_cost: float = 0.0
    duration_seconds: float = 0.0
    min_responses_required: int = 0
    actual_responses: int = 0


@dataclass
class CascadeResult:
    """Result of cascade (pipeline) execution."""
    pipeline_stages: list[dict[str, Any]] = field(default_factory=list)
    final_output: Any = None
    all_succeeded: bool = True
    total_cost: float = 0.0
    duration_seconds: float = 0.0
    failed_stage: int = -1


class SwarmIntelligence:
    """Coordinates multiple agents for complex tasks.

    Provides three execution patterns:
    - parallel_execute: Run multiple tasks concurrently
    - consensus_execute: Run same task on multiple agents, reach consensus
    - cascade_execute: Pipeline where output feeds next stage

    Args:
        orchestrator: DelegationOrchestrator for task management.
    """

    def __init__(self, orchestrator: DelegationOrchestrator) -> None:
        self.orchestrator = orchestrator

    async def parallel_execute(
        self,
        tasks: list[str],
        max_concurrent: int = 5,
        budget_per_task: float = 0.10,
    ) -> list[DelegationResult]:
        """Execute multiple tasks in parallel across agents.

        1. Discover available agents
        2. Assign tasks based on capabilities
        3. Execute in parallel with concurrency limit
        4. Collect and aggregate results

        Args:
            tasks: List of task descriptions to execute.
            max_concurrent: Maximum concurrent executions.
            budget_per_task: Budget per task.

        Returns:
            List of DelegationResult for each task.
        """
        start = time.time()
        semaphore = asyncio.Semaphore(max_concurrent)
        results: list[DelegationResult | None] = [None] * len(tasks)

        async def _run_task(idx: int, task: str) -> None:
            async with semaphore:
                request = await self.orchestrator.protocol.create_delegation_request(
                    task_description=task,
                    required_capabilities=[],
                    max_budget=budget_per_task,
                    timeout_seconds=300,
                )
                result = await self.orchestrator.protocol.execute_delegated_task(request)
                results[idx] = result

        # Execute all tasks in parallel
        done = await asyncio.gather(
            *[_run_task(i, t) for i, t in enumerate(tasks)],
            return_exceptions=True,
        )

        # Log any exceptions and filter out None results
        for item in done:
            if isinstance(item, Exception):
                logger.error("Parallel task failed: %s", item)
        final_results = [r for r in results if r is not None]

        elapsed = time.time() - start
        logger.info(
            "Parallel execution complete: %d tasks in %.2fs",
            len(final_results),
            elapsed,
        )
        return final_results

    async def consensus_execute(
        self,
        task: str,
        min_responses: int = 3,
        budget_per_response: float = 0.05,
    ) -> ConsensusResult:
        """Execute task across multiple agents and reach consensus.

        Useful for:
        - Verification tasks
        - Quality assurance
        - Redundant execution for reliability

        Args:
            task: The task to execute on multiple agents.
            min_responses: Minimum responses needed for consensus.
            budget_per_response: Budget per agent response.

        Returns:
            ConsensusResult with agreement analysis.
        """
        start = time.time()
        result = ConsensusResult(
            task_description=task,
            min_responses_required=min_responses,
        )

        # Discover available agents
        agents = await self.orchestrator.discovery.discover(
            ["general"], max_results=min_responses * 2
        )

        # Execute on multiple agents
        exec_tasks = []
        for agent in agents[:min_responses * 2]:
            request = await self.orchestrator.protocol.create_delegation_request(
                task_description=task,
                required_capabilities=[],
                max_budget=budget_per_response,
                timeout_seconds=120,
            )
            exec_tasks.append(
                self.orchestrator.protocol.execute_delegated_task(request)
            )

        # Gather responses with timeout
        try:
            responses = await asyncio.wait_for(
                asyncio.gather(*exec_tasks, return_exceptions=True),
                timeout=120,
            )
            for r in responses:
                if isinstance(r, DelegationResult):
                    result.responses.append(r)
                    result.total_cost += r.cost_incurred
        except asyncio.TimeoutError:
            logger.warning("Consensus execution timed out")

        result.actual_responses = len(result.responses)

        # Check consensus: compare outputs
        if result.responses:
            outputs = [
                r.output for r in result.responses if r.output is not None
            ]
            if outputs:
                # Simple majority consensus
                from collections import Counter

                output_counts = Counter(str(o) for o in outputs)
                most_common, count = output_counts.most_common(1)[0]
                result.agreement_ratio = count / len(outputs)
                result.consensus_reached = (
                    result.agreement_ratio >= 0.5
                    and result.actual_responses >= min_responses
                )
                result.consensus_output = most_common

        result.duration_seconds = time.time() - start
        logger.info(
            "Consensus: %d/%d responses, agreement=%.1f%%, reached=%s",
            result.actual_responses,
            min_responses,
            result.agreement_ratio * 100,
            result.consensus_reached,
        )
        return result

    async def cascade_execute(
        self,
        pipeline: list[str],
        budget_per_stage: float = 0.10,
    ) -> CascadeResult:
        """Execute pipeline where each agent's output feeds the next.

        Useful for:
        - Multi-stage processing
        - Data transformation pipelines
        - Complex workflows

        Args:
            pipeline: Ordered list of task descriptions.
            budget_per_stage: Budget per pipeline stage.

        Returns:
            CascadeResult with per-stage results and final output.
        """
        start = time.time()
        result = CascadeResult()
        current_input: Any = None

        for i, stage_task in enumerate(pipeline):
            stage_info: dict[str, Any] = {
                "stage": i,
                "task": stage_task,
                "status": "pending",
                "cost": 0.0,
            }

            # Build task description with previous output
            full_task = stage_task
            if current_input is not None:
                full_task = f"{stage_task}\n\nInput from previous stage: {current_input}"

            try:
                request = await self.orchestrator.protocol.create_delegation_request(
                    task_description=full_task,
                    required_capabilities=[],
                    max_budget=budget_per_stage,
                    timeout_seconds=300,
                )
                exec_result = await asyncio.wait_for(
                    self.orchestrator.protocol.execute_delegated_task(request),
                    timeout=300,
                )

                stage_info["status"] = (
                    "completed"
                    if exec_result.status == RequestStatus.COMPLETED
                    else "failed"
                )
                stage_info["cost"] = exec_result.cost_incurred
                result.total_cost += exec_result.cost_incurred

                if exec_result.status == RequestStatus.COMPLETED:
                    current_input = exec_result.output
                    result.final_output = exec_result.output
                else:
                    result.all_succeeded = False
                    result.failed_stage = i
                    stage_info["error"] = exec_result.error
                    break

            except asyncio.TimeoutError:
                result.all_succeeded = False
                result.failed_stage = i
                stage_info["status"] = "timed_out"
                break

            result.pipeline_stages.append(stage_info)

        result.duration_seconds = time.time() - start
        logger.info(
            "Cascade: %d/%d stages, all_succeeded=%s, cost=$%.4f",
            len(result.pipeline_stages),
            len(pipeline),
            result.all_succeeded,
            result.total_cost,
        )
        return result

    def get_swarm_stats(self) -> dict[str, Any]:
        """Return swarm intelligence statistics."""
        orch_stats = self.orchestrator.get_stats()
        return {
            "orchestrator_stats": orch_stats,
            "protocol_stats": self.orchestrator.protocol.get_stats(),
        }
