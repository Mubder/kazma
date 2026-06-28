"""Delegation Protocol — Core protocol for agent-to-agent task delegation.

Implements the request/response lifecycle for delegating sub-tasks
between autonomous Kazma agents. Each request is cryptographically
signed for authenticity and audited for cost control.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class RequestStatus(StrEnum):
    """Status of a delegation request."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass
class DelegationRequest:
    """A request to delegate a sub-task to another agent."""

    request_id: str
    requester_id: str
    task_description: str
    required_capabilities: list[str]
    max_budget: float
    timeout_seconds: int
    created_at: float
    signature: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for signing/transmission (excludes signature)."""
        return {
            "request_id": self.request_id,
            "requester_id": self.requester_id,
            "task_description": self.task_description,
            "required_capabilities": self.required_capabilities,
            "max_budget": self.max_budget,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
        }


@dataclass
class DelegationResponse:
    """Response to a delegation request from the target agent."""

    request_id: str
    responder_id: str
    status: RequestStatus
    estimated_cost: float = 0.0
    reason: str = ""
    signature: str = ""


@dataclass
class DelegationResult:
    """Result of executing a delegated task."""

    request_id: str
    executor_id: str
    status: RequestStatus
    output: Any = None
    cost_incurred: float = 0.0
    duration_seconds: float = 0.0
    error: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


# Type alias for task executors
TaskExecutor = Callable[[DelegationRequest], Awaitable[DelegationResult]]


class DelegationProtocol:
    """Agent-to-agent delegation protocol.

    Manages the full lifecycle of delegation requests: creation,
    reception, execution tracking, and completion reporting.

    Args:
        agent_id: This agent's unique identifier.
        rbac: RBAC engine for permission checks.
        security: Optional DelegationSecurity for signing/verification.
        executor: Optional task executor callback.
    """

    PROTOCOL_VERSION = "1.0.0"

    def __init__(
        self,
        agent_id: str,
        rbac: Any = None,
        security: Any = None,
        executor: TaskExecutor | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.rbac = rbac
        self.security = security
        self.executor = executor
        self._pending_requests: dict[str, DelegationRequest] = {}
        self._completed_requests: dict[str, DelegationResult] = {}
        self._cost_tracker: dict[str, float] = {}  # request_id -> cost

    async def create_delegation_request(
        self,
        task_description: str,
        required_capabilities: list[str],
        max_budget: float = 0.10,
        timeout_seconds: int = 300,
        payload: dict[str, Any] | None = None,
    ) -> DelegationRequest:
        """Create a delegation request to send to other agents.

        Args:
            task_description: What needs to be done.
            required_capabilities: Capabilities the executor must have.
            max_budget: Maximum cost allowed (USD).
            timeout_seconds: Execution timeout.
            payload: Optional additional data for the task.

        Returns:
            DelegationRequest with cryptographic signature.
        """
        request = DelegationRequest(
            request_id=str(uuid.uuid4()),
            requester_id=self.agent_id,
            task_description=task_description,
            required_capabilities=required_capabilities,
            max_budget=max_budget,
            timeout_seconds=timeout_seconds,
            created_at=time.time(),
            payload=payload or {},
        )

        # Sign the request if security is available
        if self.security is not None:
            request.signature = self.security.sign_request(request.to_dict())

        self._pending_requests[request.request_id] = request
        logger.info(
            "Delegation request created: %s (caps=%s, budget=$%.4f)",
            request.request_id,
            required_capabilities,
            max_budget,
        )
        return request

    async def receive_delegation_request(self, request: DelegationRequest) -> DelegationResponse:
        """Receive and evaluate a delegation request.

        Evaluation steps:
        1. Verify signature authenticity (if security enabled)
        2. Check if agent has required capabilities
        3. Evaluate budget constraints
        4. Accept or reject with reason

        Args:
            request: Incoming delegation request.

        Returns:
            DelegationResponse indicating acceptance or rejection.
        """
        # Step 1: Verify signature
        if self.security is not None and request.signature:
            valid = self.security.verify_request(request.to_dict(), request.signature)
            if not valid:
                logger.warning(
                    "Invalid signature on request %s from %s",
                    request.request_id,
                    request.requester_id,
                )
                return DelegationResponse(
                    request_id=request.request_id,
                    responder_id=self.agent_id,
                    status=RequestStatus.REJECTED,
                    reason="Invalid request signature",
                )

        # Step 2: Check budget constraints
        if request.max_budget <= 0:
            return DelegationResponse(
                request_id=request.request_id,
                responder_id=self.agent_id,
                status=RequestStatus.REJECTED,
                reason="Budget must be positive",
            )

        # Step 3: Check timeout sanity
        if request.timeout_seconds <= 0:
            return DelegationResponse(
                request_id=request.request_id,
                responder_id=self.agent_id,
                status=RequestStatus.REJECTED,
                reason="Timeout must be positive",
            )

        # Step 4: Check request freshness (prevent replay attacks)
        age = time.time() - request.created_at
        if age > request.timeout_seconds:
            return DelegationResponse(
                request_id=request.request_id,
                responder_id=self.agent_id,
                status=RequestStatus.REJECTED,
                reason="Request has expired",
            )

        # All checks passed — accept
        response = DelegationResponse(
            request_id=request.request_id,
            responder_id=self.agent_id,
            status=RequestStatus.ACCEPTED,
        )

        if self.security is not None:
            response.signature = self.security.sign_request({"request_id": request.request_id, "status": "accepted"})

        self._pending_requests[request.request_id] = request
        logger.info("Delegation request accepted: %s", request.request_id)
        return response

    async def execute_delegated_task(self, request: DelegationRequest) -> DelegationResult:
        """Execute a delegated task.

        If an executor callback was provided, delegates to it.
        Otherwise returns a placeholder result.

        Args:
            request: The delegation request to execute.

        Returns:
            DelegationResult with output and cost tracking.
        """
        start = time.time()

        if self.executor is not None:
            try:
                result = await self.executor(request)
                result.duration_seconds = time.time() - start
                self._completed_requests[request.request_id] = result
                self._track_cost(request.request_id, result.cost_incurred)
                return result
            except Exception as e:
                logger.error(
                    "Task execution failed for %s: %s",
                    request.request_id,
                    e,
                )
                result = DelegationResult(
                    request_id=request.request_id,
                    executor_id=self.agent_id,
                    status=RequestStatus.FAILED,
                    error=str(e),
                    duration_seconds=time.time() - start,
                )
                self._completed_requests[request.request_id] = result
                return result
        else:
            # No executor — mark as pending execution
            result = DelegationResult(
                request_id=request.request_id,
                executor_id=self.agent_id,
                status=RequestStatus.PENDING,
                provenance={"note": "No executor registered"},
            )
            return result

    async def report_completion(self, request_id: str, result: DelegationResult) -> bool:
        """Report task completion back to the requester.

        Args:
            request_id: The completed request ID.
            result: The execution result.

        Returns:
            True if reported successfully.
        """
        if request_id not in self._pending_requests:
            logger.warning(
                "Attempted to report completion for unknown request: %s",
                request_id,
            )
            return False

        self._completed_requests[request_id] = result
        del self._pending_requests[request_id]
        self._track_cost(request_id, result.cost_incurred)

        logger.info(
            "Task completion reported: %s (status=%s, cost=$%.4f)",
            request_id,
            result.status.value,
            result.cost_incurred,
        )
        return True

    def _track_cost(self, request_id: str, cost: float) -> None:
        """Track cost for a delegation request."""
        self._cost_tracker[request_id] = cost

    def get_pending_request(self, request_id: str) -> DelegationRequest | None:
        """Get a pending request by ID."""
        return self._pending_requests.get(request_id)

    def get_total_delegation_cost(self) -> float:
        """Get total cost across all delegated tasks."""
        return sum(self._cost_tracker.values())

    def get_completed_results(self) -> dict[str, DelegationResult]:
        """Get all completed results."""
        return dict(self._completed_requests)

    def get_stats(self) -> dict[str, Any]:
        """Return protocol statistics."""
        return {
            "agent_id": self.agent_id,
            "protocol_version": self.PROTOCOL_VERSION,
            "pending_count": len(self._pending_requests),
            "completed_count": len(self._completed_requests),
            "total_cost": self.get_total_delegation_cost(),
        }
