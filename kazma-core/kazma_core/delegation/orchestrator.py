"""Delegation Orchestrator — Multi-agent task decomposition and coordination.

Decomposes complex tasks into sub-tasks, discovers capable agents,
delegates sub-tasks, and synthesizes results. Integrates with the
cost circuit breaker to prevent runaway delegation spending.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from kazma_core.delegation.protocol import (
    DelegationProtocol,
    DelegationRequest,
    DelegationResult,
    RequestStatus,
)
from kazma_core.delegation.discovery import AgentDiscovery, AgentInfo

logger = logging.getLogger(__name__)


class SubTaskStatus(str, Enum):
    """Status of a sub-task in orchestration."""
    PENDING = "pending"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class SubTask:
    """A decomposed sub-task with assignment and result."""
    task_id: str
    description: str
    required_capabilities: list[str]
    assigned_agent: str = ""
    status: SubTaskStatus = SubTaskStatus.PENDING
    result: DelegationResult | None = None
    max_budget: float = 0.10
    timeout_seconds: int = 300
    created_at: float = field(default_factory=time.time)


@dataclass
class OrchestrationResult:
    """Result of orchestrating multiple sub-tasks."""
    task_id: str
    status: RequestStatus
    sub_tasks: list[SubTask] = field(default_factory=list)
    total_cost: float = 0.0
    duration_seconds: float = 0.0
    synthesized_output: Any = None
    error: str = ""


class DelegationOrchestrator:
    """Orchestrates multi-agent task execution.

    Handles task decomposition, agent discovery, delegation,
    timeout handling, and result synthesis.

    Args:
        protocol: DelegationProtocol for request management.
        discovery: AgentDiscovery for finding capable agents.
        tracer: KazmaTracer for observability (optional).
        cost_breaker: CostCircuitBreaker for budget enforcement (optional).
    """

    def __init__(
        self,
        protocol: DelegationProtocol,
        discovery: AgentDiscovery,
        tracer: Any = None,
        cost_breaker: Any = None,
    ) -> None:
        self.protocol = protocol
        self.discovery = discovery
        self.tracer = tracer
        self.cost_breaker = cost_breaker
        self._active_orchestrations: dict[str, OrchestrationResult] = {}

    async def decompose_and_delegate(
        self,
        task: str,
        max_agents: int = 3,
        budget_per_task: float = 0.10,
        timeout_seconds: int = 300,
    ) -> OrchestrationResult:
        """Decompose complex task and delegate to multiple agents.

        1. Analyze task for decomposition points
        2. Identify required capabilities per sub-task
        3. Discover agents with matching capabilities
        4. Create delegation requests
        5. Send requests and collect responses
        6. Synthesize results

        Args:
            task: The complex task to decompose.
            max_agents: Maximum agents to involve.
            budget_per_task: Budget per sub-task.
            timeout_seconds: Timeout per sub-task.

        Returns:
            OrchestrationResult with all sub-task outcomes.
        """
        start = time.time()
        result = OrchestrationResult(
            task_id=f"orch-{int(start * 1000)}",
            status=RequestStatus.EXECUTING,
        )

        # Step 1: Check circuit breaker
        if self.cost_breaker is not None and self.cost_breaker.should_halt():
            result.status = RequestStatus.REJECTED
            result.error = "Circuit breaker halted — budget exceeded"
            return result

        # Step 2: Decompose task
        sub_tasks = self._decompose_task(task, max_agents)
        result.sub_tasks = sub_tasks

        # Step 3: Discover agents for each sub-task
        assignments = await self._discover_and_assign(sub_tasks, max_agents)

        # Step 4: Execute delegations in parallel
        exec_results = await self._execute_delegations(
            assignments, budget_per_task, timeout_seconds
        )

        # Step 5: Collect results
        for sub_task in sub_tasks:
            if sub_task.task_id in exec_results:
                sub_task.result = exec_results[sub_task.task_id]
                sub_task.status = (
                    SubTaskStatus.COMPLETED
                    if exec_results[sub_task.task_id].status == RequestStatus.COMPLETED
                    else SubTaskStatus.FAILED
                )

        # Step 6: Synthesize
        result.total_cost = sum(
            st.result.cost_incurred
            for st in sub_tasks
            if st.result is not None
        )
        result.duration_seconds = time.time() - start
        result.synthesized_output = self._synthesize_results(sub_tasks)

        completed = sum(
            1 for st in sub_tasks if st.status == SubTaskStatus.COMPLETED
        )
        result.status = (
            RequestStatus.COMPLETED
            if completed == len(sub_tasks)
            else RequestStatus.FAILED
        )

        # Record cost
        if self.cost_breaker is not None:
            self.cost_breaker.record_cost(result.total_cost)

        self._active_orchestrations[result.task_id] = result
        logger.info(
            "Orchestration %s complete: %d/%d sub-tasks, cost=$%.4f",
            result.task_id,
            completed,
            len(sub_tasks),
            result.total_cost,
        )
        return result

    def _decompose_task(self, task: str, max_agents: int) -> list[SubTask]:
        """Decompose a task into sub-tasks.

        Uses a simple heuristic: split by logical boundaries.
        In production, this would use an LLM for intelligent decomposition.
        """
        # Simple decomposition: treat the task as a single unit
        # Real implementation would use LLM-based analysis
        sub_tasks = [
            SubTask(
                task_id=f"sub-{i}",
                description=task,
                required_capabilities=["general"],
                max_budget=0.10,
                timeout_seconds=300,
            )
            for i in range(min(1, max_agents))  # Start with single sub-task
        ]
        return sub_tasks

    async def _discover_and_assign(
        self,
        sub_tasks: list[SubTask],
        max_agents: int,
    ) -> dict[str, AgentInfo]:
        """Discover agents and assign to sub-tasks."""
        assignments: dict[str, AgentInfo] = {}
        assigned_agents: set[str] = set()

        for sub_task in sub_tasks:
            candidates = await self.discovery.discover(
                sub_task.required_capabilities, max_results=max_agents
            )

            # Pick the best unassigned agent
            for agent in candidates:
                if agent.agent_id not in assigned_agents:
                    assignments[sub_task.task_id] = agent
                    sub_task.assigned_agent = agent.agent_id
                    sub_task.status = SubTaskStatus.ASSIGNED
                    assigned_agents.add(agent.agent_id)
                    break

        return assignments

    async def _execute_delegations(
        self,
        assignments: dict[str, AgentInfo],
        budget_per_task: float,
        timeout_seconds: int,
    ) -> dict[str, DelegationResult]:
        """Execute all delegations in parallel with concurrency limit."""
        results: dict[str, DelegationResult] = {}
        semaphore = asyncio.Semaphore(min(len(assignments), 5))

        async def _execute_one(
            sub_task_id: str, agent: AgentInfo
        ) -> tuple[str, DelegationResult]:
            async with semaphore:
                request = await self.protocol.create_delegation_request(
                    task_description=f"Sub-task {sub_task_id}",
                    required_capabilities=[],
                    max_budget=budget_per_task,
                    timeout_seconds=timeout_seconds,
                )
                result = await asyncio.wait_for(
                    self.protocol.execute_delegated_task(request),
                    timeout=timeout_seconds,
                )
                return sub_task_id, result

        # Build tasks
        tasks = []
        for sub_task_id, agent in assignments.items():
            tasks.append(_execute_one(sub_task_id, agent))

        if tasks:
            done: list[Any] = list(await asyncio.gather(*tasks, return_exceptions=True))
            for item in done:
                if isinstance(item, Exception):
                    logger.error("Delegation execution error: %s", item)
                    continue
                sub_task_id, result = item
                results[sub_task_id] = result

        return results

    def _synthesize_results(
        self, sub_tasks: list[SubTask]
    ) -> dict[str, Any]:
        """Synthesize results from all sub-tasks."""
        outputs = {}
        for st in sub_tasks:
            if st.result and st.result.output is not None:
                outputs[st.task_id] = st.result.output
        return {
            "sub_task_count": len(sub_tasks),
            "outputs": outputs,
            "completed": sum(
                1 for st in sub_tasks if st.status == SubTaskStatus.COMPLETED
            ),
        }

    async def handle_timeout(self, request_id: str) -> None:
        """Handle delegation timeout.

        Updates sub-task status and updates executor reputation.
        """
        logger.warning("Delegation timeout: %s", request_id)
        # Find the sub-task
        for orch in self._active_orchestrations.values():
            for st in orch.sub_tasks:
                if st.assigned_agent and st.result is None:
                    st.status = SubTaskStatus.TIMED_OUT
                    # Penalize reputation
                    await self.discovery.update_reputation(
                        st.assigned_agent, 0.5
                    )

    async def handle_failure(self, request_id: str, error: str) -> None:
        """Handle delegation failure.

        Updates sub-task status and logs the failure.
        """
        logger.error("Delegation failure: %s — %s", request_id, error)
        for orch in self._active_orchestrations.values():
            for st in orch.sub_tasks:
                if st.result and st.result.request_id == request_id:
                    st.status = SubTaskStatus.FAILED
                    break

    def get_orchestration(self, task_id: str) -> OrchestrationResult | None:
        """Get an orchestration result by ID."""
        return self._active_orchestrations.get(task_id)

    def get_stats(self) -> dict[str, Any]:
        """Return orchestration statistics."""
        orchs = list(self._active_orchestrations.values())
        return {
            "active_orchestrations": len(orchs),
            "total_sub_tasks": sum(len(o.sub_tasks) for o in orchs),
            "total_cost": sum(o.total_cost for o in orchs),
        }
