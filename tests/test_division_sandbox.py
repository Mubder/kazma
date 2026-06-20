"""Tests for Division Sandbox — Division isolation enforcement.

Covers:
- Sandboxed operation execution
- Access denial for unauthorized users
- Sensitive resource approval flow
- Cross-division access requests
- Audit logging integration
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from kazma_core.rbac import RBACEngine
from kazma_core.audit_logger import AuditLogger
from kazma_core.division_sandbox import DivisionSandbox, SandboxResult, CrossDivisionRequest


@pytest.fixture
def tmp_paths(tmp_path):
    """Return paths for RBAC and audit databases."""
    return {
        "rbac": str(tmp_path / "rbac.db"),
        "audit": str(tmp_path / "audit.db"),
    }


@pytest_asyncio.fixture
async def sandbox(tmp_paths):
    """Return a DivisionSandbox with fresh databases."""
    rbac = RBACEngine(db_path=tmp_paths["rbac"])
    audit = AuditLogger(db_path=tmp_paths["audit"])
    ds = DivisionSandbox(rbac=rbac, audit_logger=audit)
    yield ds
    await rbac.close()
    await audit.close()


async def sample_operation(*args, **kwargs):
    """A simple async operation for testing."""
    return {"division": kwargs.get("_division_context"), "args": args}


async def failing_operation(*args, **kwargs):
    """An operation that raises an exception."""
    raise RuntimeError("Operation failed intentionally")


# ─── Sandboxed Execution Tests ────────────────────────────────────────

class TestSandboxedExecution:
    """Test operation execution within division sandboxes."""

    @pytest.mark.asyncio
    async def test_authorized_execution(self, sandbox: DivisionSandbox):
        """User with correct role can execute in sandbox."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "trader")
        result = await sandbox.execute_in_sandbox(
            "alice", "gas_oil", sample_operation, resource="pricing", action="read",
        )
        assert result.success is True
        assert result.result["division"] == "gas_oil"
        assert result.audit_entry_id != ""

    @pytest.mark.asyncio
    async def test_unauthorized_user_denied(self, sandbox: DivisionSandbox):
        """User not in division is denied."""
        result = await sandbox.execute_in_sandbox(
            "alice", "gas_oil", sample_operation,
            resource="pricing", action="read",
        )
        assert result.success is False
        assert "not in division" in result.error
        assert result.audit_entry_id != ""

    @pytest.mark.asyncio
    async def test_wrong_role_denied(self, sandbox: DivisionSandbox):
        """User with viewer role cannot write."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "viewer")
        result = await sandbox.execute_in_sandbox(
            "alice", "gas_oil", sample_operation,
            resource="pricing", action="write",
        )
        assert result.success is False
        assert "does not have" in result.error

    @pytest.mark.asyncio
    async def test_operation_failure_captured(self, sandbox: DivisionSandbox):
        """Operation failures are captured and audit-logged."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "trader")
        result = await sandbox.execute_in_sandbox(
            "alice", "gas_oil", failing_operation,
            resource="pricing", action="read",
        )
        assert result.success is False
        assert "Operation failed" in result.error
        assert result.audit_entry_id != ""

    @pytest.mark.asyncio
    async def test_sensitive_resource_blocked(self, sandbox: DivisionSandbox):
        """Write on sensitive resource returns pending_approval."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "trader")
        result = await sandbox.execute_in_sandbox(
            "alice", "gas_oil", sample_operation,
            resource="contracts", action="write",
        )
        assert result.success is False
        assert "Requires admin approval" in result.error


# ─── Cross-Division Access Tests ──────────────────────────────────────

class TestCrossDivisionAccess:
    """Test cross-division access request flow."""

    @pytest.mark.asyncio
    async def test_request_cross_division(self, sandbox: DivisionSandbox):
        """Can create a cross-division access request."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "trader")
        req = await sandbox.request_cross_division_access(
            user_id="alice",
            source_division="gas_oil",
            target_division="tourism",
            resource="bookings",
            reason="Need to check tourism data for joint venture",
        )
        assert req.status == "pending"
        assert req.source_division == "gas_oil"
        assert req.target_division == "tourism"
        assert req.reason == "Need to check tourism data for joint venture"

    @pytest.mark.asyncio
    async def test_request_requires_source_membership(self, sandbox: DivisionSandbox):
        """User not in source division cannot request."""
        with pytest.raises(PermissionError):
            await sandbox.request_cross_division_access(
                user_id="alice",
                source_division="gas_oil",
                target_division="tourism",
                resource="bookings",
                reason="Just because",
            )

    @pytest.mark.asyncio
    async def test_request_invalid_division(self, sandbox: DivisionSandbox):
        """Invalid division raises ValueError."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "trader")
        with pytest.raises(ValueError):
            await sandbox.request_cross_division_access(
                user_id="alice",
                source_division="gas_oil",
                target_division="invalid_div",
                resource="bookings",
                reason="test",
            )

    @pytest.mark.asyncio
    async def test_get_pending_requests(self, sandbox: DivisionSandbox):
        """Can list pending cross-division requests."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "trader")
        await sandbox.rbac.assign_role("bob", "tourism", "agent")

        req1 = await sandbox.request_cross_division_access(
            "alice", "gas_oil", "tourism", "bookings", "reason1",
        )
        req2 = await sandbox.request_cross_division_access(
            "bob", "tourism", "gas_oil", "pricing", "reason2",
        )

        pending = await sandbox.get_pending_requests()
        assert len(pending) == 2

        pending_tourism = await sandbox.get_pending_requests("tourism")
        assert len(pending_tourism) == 1
        assert pending_tourism[0].id == req1.id

    @pytest.mark.asyncio
    async def test_audit_logged_on_request(self, sandbox: DivisionSandbox):
        """Cross-division request is audit logged."""
        await sandbox.rbac.assign_role("alice", "gas_oil", "trader")
        await sandbox.request_cross_division_access(
            "alice", "gas_oil", "tourism", "bookings", "auditable reason",
        )

        trail = await sandbox.audit.get_audit_trail(user_id="alice")
        assert len(trail) >= 1
        assert trail[0].action == "cross_division_request"
        assert trail[0].result == "pending_approval"
