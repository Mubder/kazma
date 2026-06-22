"""Tests for DelegationProtocol — request lifecycle and execution."""

from __future__ import annotations

import time

from kazma_core.delegation.protocol import (
    DelegationProtocol,
    DelegationRequest,
    DelegationResult,
    RequestStatus,
)
from kazma_core.delegation.security import DelegationSecurity


class TestProtocolInit:
    """Test protocol initialization."""

    def test_default_init(self):
        proto = DelegationProtocol(agent_id="agent-1")
        assert proto.agent_id == "agent-1"
        assert proto.PROTOCOL_VERSION == "1.0.0"

    def test_stats_empty(self):
        proto = DelegationProtocol(agent_id="agent-1")
        stats = proto.get_stats()
        assert stats["pending_count"] == 0
        assert stats["completed_count"] == 0
        assert stats["total_cost"] == 0.0


class TestCreateDelegationRequest:
    """Test request creation."""

    async def test_create_basic_request(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req = await proto.create_delegation_request(
            task_description="Summarize document",
            required_capabilities=["summarization"],
        )
        assert isinstance(req, DelegationRequest)
        assert req.requester_id == "agent-1"
        assert req.task_description == "Summarize document"
        assert req.required_capabilities == ["summarization"]
        assert req.max_budget == 0.10
        assert req.timeout_seconds == 300

    async def test_create_with_custom_params(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req = await proto.create_delegation_request(
            task_description="Translate text",
            required_capabilities=["translation", "arabic"],
            max_budget=0.50,
            timeout_seconds=600,
        )
        assert req.max_budget == 0.50
        assert req.timeout_seconds == 600

    async def test_request_has_uuid(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req1 = await proto.create_delegation_request("task1", [])
        req2 = await proto.create_delegation_request("task2", [])
        assert req1.request_id != req2.request_id

    async def test_request_stored_as_pending(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req = await proto.create_delegation_request("task", ["cap"])
        pending = proto.get_pending_request(req.request_id)
        assert pending is not None
        assert pending.task_description == "task"

    async def test_request_with_signature(self):
        sec = DelegationSecurity(agent_id="agent-1")
        proto = DelegationProtocol(agent_id="agent-1", security=sec)
        req = await proto.create_delegation_request("signed task", ["cap"])
        assert len(req.signature) == 128  # Ed25519 signature hex

    async def test_request_with_payload(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req = await proto.create_delegation_request("task", ["cap"], payload={"file_path": "/data/test.csv"})
        assert req.payload == {"file_path": "/data/test.csv"}


class TestReceiveDelegationRequest:
    """Test request reception and validation."""

    async def test_accept_valid_request(self):
        proto = DelegationProtocol(agent_id="agent-2")
        req = DelegationRequest(
            request_id="req-1",
            requester_id="agent-1",
            task_description="Do something",
            required_capabilities=["general"],
            max_budget=0.10,
            timeout_seconds=300,
            created_at=time.time(),
        )
        resp = await proto.receive_delegation_request(req)
        assert resp.status == RequestStatus.ACCEPTED

    async def test_reject_zero_budget(self):
        proto = DelegationProtocol(agent_id="agent-2")
        req = DelegationRequest(
            request_id="req-2",
            requester_id="agent-1",
            task_description="Free work",
            required_capabilities=[],
            max_budget=0.0,
            timeout_seconds=300,
            created_at=time.time(),
        )
        resp = await proto.receive_delegation_request(req)
        assert resp.status == RequestStatus.REJECTED
        assert "Budget" in resp.reason

    async def test_reject_negative_timeout(self):
        proto = DelegationProtocol(agent_id="agent-2")
        req = DelegationRequest(
            request_id="req-3",
            requester_id="agent-1",
            task_description="Impossible",
            required_capabilities=[],
            max_budget=0.10,
            timeout_seconds=-1,
            created_at=time.time(),
        )
        resp = await proto.receive_delegation_request(req)
        assert resp.status == RequestStatus.REJECTED
        assert "Timeout" in resp.reason

    async def test_reject_expired_request(self):
        proto = DelegationProtocol(agent_id="agent-2")
        req = DelegationRequest(
            request_id="req-4",
            requester_id="agent-1",
            task_description="Old task",
            required_capabilities=[],
            max_budget=0.10,
            timeout_seconds=1,
            created_at=time.time() - 100,  # 100 seconds ago
        )
        resp = await proto.receive_delegation_request(req)
        assert resp.status == RequestStatus.REJECTED
        assert "expired" in resp.reason.lower()

    async def test_reject_invalid_signature(self):
        sec = DelegationSecurity(agent_id="agent-2")
        proto = DelegationProtocol(agent_id="agent-2", security=sec)
        req = DelegationRequest(
            request_id="req-5",
            requester_id="agent-1",
            task_description="Task",
            required_capabilities=[],
            max_budget=0.10,
            timeout_seconds=300,
            created_at=time.time(),
            signature="0" * 128,  # Fake signature
        )
        resp = await proto.receive_delegation_request(req)
        assert resp.status == RequestStatus.REJECTED
        assert "signature" in resp.reason.lower()


class TestExecuteDelegatedTask:
    """Test task execution."""

    async def test_execute_without_executor(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req = DelegationRequest(
            request_id="req-1",
            requester_id="agent-1",
            task_description="Task",
            required_capabilities=[],
            max_budget=0.10,
            timeout_seconds=300,
            created_at=time.time(),
        )
        result = await proto.execute_delegated_task(req)
        assert result.status == RequestStatus.PENDING

    async def test_execute_with_executor(self):
        async def my_executor(req: DelegationRequest) -> DelegationResult:
            return DelegationResult(
                request_id=req.request_id,
                executor_id="agent-1",
                status=RequestStatus.COMPLETED,
                output={"answer": 42},
                cost_incurred=0.01,
            )

        proto = DelegationProtocol(agent_id="agent-1", executor=my_executor)
        req = DelegationRequest(
            request_id="req-1",
            requester_id="agent-1",
            task_description="Compute",
            required_capabilities=[],
            max_budget=0.10,
            timeout_seconds=300,
            created_at=time.time(),
        )
        result = await proto.execute_delegated_task(req)
        assert result.status == RequestStatus.COMPLETED
        assert result.output == {"answer": 42}
        assert result.cost_incurred == 0.01

    async def test_execute_executor_failure(self):
        async def failing_executor(req: DelegationRequest) -> DelegationResult:
            raise RuntimeError("Executor crashed")

        proto = DelegationProtocol(agent_id="agent-1", executor=failing_executor)
        req = DelegationRequest(
            request_id="req-1",
            requester_id="agent-1",
            task_description="Task",
            required_capabilities=[],
            max_budget=0.10,
            timeout_seconds=300,
            created_at=time.time(),
        )
        result = await proto.execute_delegated_task(req)
        assert result.status == RequestStatus.FAILED
        assert "Executor crashed" in result.error


class TestReportCompletion:
    """Test completion reporting."""

    async def test_report_completion(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req = await proto.create_delegation_request("task", ["cap"])
        result = DelegationResult(
            request_id=req.request_id,
            executor_id="agent-1",
            status=RequestStatus.COMPLETED,
            output="done",
            cost_incurred=0.05,
        )
        success = await proto.report_completion(req.request_id, result)
        assert success is True
        assert req.request_id not in proto._pending_requests
        assert req.request_id in proto._completed_requests

    async def test_report_unknown_request(self):
        proto = DelegationProtocol(agent_id="agent-1")
        result = DelegationResult(
            request_id="unknown",
            executor_id="agent-1",
            status=RequestStatus.COMPLETED,
        )
        success = await proto.report_completion("unknown", result)
        assert success is False

    async def test_total_cost_tracking(self):
        proto = DelegationProtocol(agent_id="agent-1")
        req = await proto.create_delegation_request("task", ["cap"])
        result = DelegationResult(
            request_id=req.request_id,
            executor_id="agent-1",
            status=RequestStatus.COMPLETED,
            cost_incurred=0.05,
        )
        await proto.report_completion(req.request_id, result)
        assert proto.get_total_delegation_cost() == 0.05
