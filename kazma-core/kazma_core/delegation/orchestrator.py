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
from enum import StrEnum
from typing import Any

from kazma_core.delegation.discovery import AgentDiscovery, AgentInfo
from kazma_core.delegation.protocol import (
    DelegationProtocol,
    DelegationResult,
    RequestStatus,
)

logger = logging.getLogger(__name__)


class SubTaskStatus(StrEnum):
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
        max_orchestrations: int = 100,
    ) -> None:
        self.protocol = protocol
        self.discovery = discovery
        self.tracer = tracer
        self.cost_breaker = cost_breaker
        self._max_orchestrations = max_orchestrations
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
        exec_results = await self._execute_delegations(assignments, budget_per_task, timeout_seconds)

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
        result.total_cost = sum(st.result.cost_incurred for st in sub_tasks if st.result is not None)
        result.duration_seconds = time.time() - start
        result.synthesized_output = self._synthesize_results(sub_tasks)

        completed = sum(1 for st in sub_tasks if st.status == SubTaskStatus.COMPLETED)
        result.status = RequestStatus.COMPLETED if completed == len(sub_tasks) else RequestStatus.FAILED

        # Record cost
        if self.cost_breaker is not None:
            self.cost_breaker.record_cost(result.total_cost)

        # Evict oldest if over limit
        if len(self._active_orchestrations) >= self._max_orchestrations:
            oldest = next(iter(self._active_orchestrations))
            del self._active_orchestrations[oldest]
        self._active_orchestrations[result.task_id] = result
        logger.info(
            "Orchestration %s complete: %d/%d sub-tasks, cost=$%.4f",
            result.task_id,
            completed,
            len(sub_tasks),
            result.total_cost,
        )
        # Schedule cleanup of completed task after logging
        asyncio.ensure_future(self._cleanup_after_delay(result.task_id, delay=60.0))
        return result

    async def _cleanup_after_delay(self, task_id: str, delay: float = 60.0) -> None:
        """Remove a completed orchestration from the cache after a delay."""
        await asyncio.sleep(delay)
        self._active_orchestrations.pop(task_id, None)
        logger.debug("Orchestration %s removed from active cache", task_id)

    def _decompose_task(self, task: str, max_agents: int) -> list[SubTask]:
        """Decompose a task into sub-tasks using heuristic splitting.

        Splits on sentence boundaries (periods, semicolons, newlines) when
        the task contains multiple independent clauses. Falls back to single
        task if no clear boundaries found.
        """
        import re

        # Split on sentence boundaries that suggest independent steps
        parts = re.split(r'[;\n]|(?<=\.)\s+', task.strip())
        parts = [p.strip() for p in parts if p.strip()]

        # If no meaningful split or only 1 part, treat as single task
        if len(parts) <= 1:
            return [
                SubTask(
                    task_id="sub-0",
                    description=task,
                    required_capabilities=["general"],
                    max_budget=0.10,
                    timeout_seconds=300,
                )
            ]

        # Cap at max_agents
        parts = parts[:max_agents]

        sub_tasks = []
        for i, part in enumerate(parts):
            # Infer capabilities from keywords
            caps = ["general"]
            if any(w in part.lower() for w in ["code", "implement", "write", "function", "class"]):
                caps.append("coding")
            if any(w in part.lower() for w in ["test", "verify", "check", "validate"]):
                caps.append("testing")
            if any(w in part.lower() for w in ["search", "find", "look", "research"]):
                caps.append("research")

            sub_tasks.append(SubTask(
                task_id=f"sub-{i}",
                description=part,
                required_capabilities=caps,
                max_budget=round(0.10 / len(parts), 2),
                timeout_seconds=300,
            ))

        return sub_tasks

    async def _discover_and_assign(
        self,
        sub_tasks: list[SubTask],
        max_agents: int,
    ) -> dict[str, AgentInfo]:
        """Discover agents and assign to sub-tasks.

        Prefers WorkerRegistry lookup by expertise tag.  Falls back
        to the legacy AgentDiscovery for agents not in the registry.
        """
        assignments: dict[str, AgentInfo] = {}
        assigned_agents: set[str] = set()

        for sub_task in sub_tasks:
            agent: AgentInfo | None = None

            # 1 — Try WorkerRegistry by expertise tag
            try:
                from kazma_core.swarm.registry import WorkerRegistry

                registry = WorkerRegistry()
                for cap in sub_task.required_capabilities:
                    entries = registry.find_by_expertise(cap)
                    for entry in entries:
                        if entry.name not in assigned_agents:
                            agent = AgentInfo(
                                agent_id=entry.name,
                                capabilities=entry.expertise,
                                metadata={
                                    "model": entry.model,
                                    "provider": entry.provider,
                                    "system_prompt": entry.system_prompt,
                                    "role": entry.roles[0] if entry.roles else "",
                                },
                            )
                            break
                    if agent:
                        break
            except Exception:
                pass

            # 2 — Fall back to legacy discovery
            if agent is None:
                candidates = await self.discovery.discover(
                    sub_task.required_capabilities,
                    max_results=max_agents,
                )
                for candidate in candidates:
                    if candidate.agent_id not in assigned_agents:
                        agent = candidate
                        break

            if agent is not None:
                assignments[sub_task.task_id] = agent
                sub_task.assigned_agent = agent.agent_id
                sub_task.status = SubTaskStatus.ASSIGNED
                assigned_agents.add(agent.agent_id)

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

        async def _execute_one(sub_task_id: str, agent: AgentInfo) -> tuple[str, DelegationResult]:
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

    def _synthesize_results(self, sub_tasks: list[SubTask]) -> dict[str, Any]:
        """Synthesize results from all sub-tasks."""
        outputs = {}
        for st in sub_tasks:
            if st.result and st.result.output is not None:
                outputs[st.task_id] = st.result.output
        return {
            "sub_task_count": len(sub_tasks),
            "outputs": outputs,
            "completed": sum(1 for st in sub_tasks if st.status == SubTaskStatus.COMPLETED),
        }

    async def handle_timeout(self, request_id: str) -> None:
        """Handle delegation timeout for a specific request."""
        logger.warning("Delegation timeout: %s", request_id)
        orch = self._active_orchestrations.get(request_id)
        if orch is None:
            return
        for st in orch.sub_tasks:
            if st.status == SubTaskStatus.PENDING and st.result is None:
                st.status = SubTaskStatus.TIMED_OUT
                if st.assigned_agent:
                    await self.discovery.update_reputation(st.assigned_agent, 0.5)

    async def handle_failure(self, request_id: str, error: str) -> None:
        """Handle delegation failure for a specific request."""
        logger.error("Delegation failure: %s — %s", request_id, error)
        orch = self._active_orchestrations.get(request_id)
        if orch is None:
            return
        for st in orch.sub_tasks:
            if st.result and st.result.request_id == request_id:
                st.status = SubTaskStatus.FAILED

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
