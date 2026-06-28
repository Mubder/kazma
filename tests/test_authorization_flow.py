"""Tests for Authorization Flow — Cross-division access request lifecycle.

Covers:
- Request creation and validation
- Admin approval flow
- Admin denial flow
- Duration capping
- Expiration checking
- Audit trail integration
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from kazma_core.audit_logger import AuditLogger
from kazma_core.authorization_flow import AuthorizationFlow
from kazma_core.rbac import RBACEngine


@pytest.fixture
def tmp_paths(tmp_path):
    """Return paths for RBAC and audit databases."""
    return {
        "rbac": str(tmp_path / "rbac.db"),
        "audit": str(tmp_path / "audit.db"),
    }


@pytest_asyncio.fixture
async def auth_flow(tmp_paths):
    """Return an AuthorizationFlow with fresh databases."""
    rbac = RBACEngine(db_path=tmp_paths["rbac"])
    audit = AuditLogger(db_path=tmp_paths["audit"])
    flow = AuthorizationFlow(rbac=rbac, audit_logger=audit)
    yield flow
    await rbac.close()
    await audit.close()


# ─── Request Creation Tests ───────────────────────────────────────────


class TestRequestCreation:
    """Test authorization request creation."""

    @pytest.mark.asyncio
    async def test_create_request(self, auth_flow: AuthorizationFlow):
        """Can create a cross-division access request."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        req = await auth_flow.request_access(
            user_id="alice",
            source_division="gas_oil",
            target_division="tourism",
            resource="bookings",
            justification="Joint venture analysis",
        )
        assert req.status == "pending"
        assert req.user_id == "alice"
        assert req.target_division == "tourism"
        assert req.justification == "Joint venture analysis"
        assert req.duration_hours <= auth_flow.max_approval_duration_hours

    @pytest.mark.asyncio
    async def test_request_requires_source_membership(self, auth_flow: AuthorizationFlow):
        """User not in source division cannot request."""
        with pytest.raises(PermissionError):
            await auth_flow.request_access(
                "alice",
                "gas_oil",
                "tourism",
                "bookings",
                "reason",
            )

    @pytest.mark.asyncio
    async def test_request_invalid_target_division(self, auth_flow: AuthorizationFlow):
        """Invalid target division raises ValueError."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        with pytest.raises(ValueError):
            await auth_flow.request_access(
                "alice",
                "gas_oil",
                "nonexistent",
                "bookings",
                "reason",
            )

    @pytest.mark.asyncio
    async def test_duration_capped(self, auth_flow: AuthorizationFlow):
        """Requested duration is capped at max_approval_duration_hours."""
        auth_flow.max_approval_duration_hours = 12
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
            duration_hours=48,
        )
        assert req.duration_hours == 12

    @pytest.mark.asyncio
    async def test_request_stored(self, auth_flow: AuthorizationFlow):
        """Created request can be retrieved by ID."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
        )
        fetched = await auth_flow.get_request(req.id)
        assert fetched is not None
        assert fetched.id == req.id


# ─── Approval Tests ───────────────────────────────────────────────────


class TestApproval:
    """Test approval flow."""

    @pytest.mark.asyncio
    async def test_approve_request(self, auth_flow: AuthorizationFlow):
        """Admin can approve a pending request."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("admin_bob", "tourism", "admin")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
        )
        result = await auth_flow.approve_request(req.id, "admin_bob")
        assert result.success is True
        assert result.expires_at != ""

        # Verify request status updated
        fetched = await auth_flow.get_request(req.id)
        assert fetched is not None
        assert fetched.status == "approved"
        assert fetched.approver_id == "admin_bob"

    @pytest.mark.asyncio
    async def test_approve_grants_role(self, auth_flow: AuthorizationFlow):
        """Approval grants viewer role in target division."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("admin_bob", "tourism", "admin")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
        )
        await auth_flow.approve_request(req.id, "admin_bob")

        # Alice should now have viewer in tourism
        roles = await auth_flow.rbac.get_user_roles("alice", "tourism")
        assert len(roles) >= 1
        role_names = {r["role"] for r in roles}
        assert "viewer" in role_names

    @pytest.mark.asyncio
    async def test_approve_nonexistent_request(self, auth_flow: AuthorizationFlow):
        """Cannot approve nonexistent request."""
        result = await auth_flow.approve_request("fake_id", "admin")
        assert result.success is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_approve_already_resolved(self, auth_flow: AuthorizationFlow):
        """Cannot approve already resolved request."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("admin_bob", "tourism", "admin")
        await auth_flow.rbac.assign_role("admin_carol", "tourism", "admin")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
        )
        await auth_flow.approve_request(req.id, "admin_bob")

        result = await auth_flow.approve_request(req.id, "admin_carol")
        assert result.success is False
        assert "already" in result.message

    @pytest.mark.asyncio
    async def test_approve_requires_admin_role(self, auth_flow: AuthorizationFlow):
        """Non-admin cannot approve requests."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("viewer_bob", "tourism", "viewer")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
        )
        result = await auth_flow.approve_request(req.id, "viewer_bob")
        assert result.success is False
        assert "not an admin" in result.message


# ─── Denial Tests ─────────────────────────────────────────────────────


class TestDenial:
    """Test denial flow."""

    @pytest.mark.asyncio
    async def test_deny_request(self, auth_flow: AuthorizationFlow):
        """Admin can deny a pending request."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("admin_bob", "tourism", "admin")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
        )
        result = await auth_flow.deny_request(req.id, "admin_bob", "Not justified")
        assert result.success is True

        fetched = await auth_flow.get_request(req.id)
        assert fetched is not None
        assert fetched.status == "denied"
        assert fetched.denial_reason == "Not justified"

    @pytest.mark.asyncio
    async def test_deny_nonexistent_request(self, auth_flow: AuthorizationFlow):
        """Cannot deny nonexistent request."""
        result = await auth_flow.deny_request("fake_id", "admin", "reason")
        assert result.success is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_deny_requires_admin(self, auth_flow: AuthorizationFlow):
        """Non-admin cannot deny requests."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("viewer_bob", "tourism", "viewer")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason",
        )
        result = await auth_flow.deny_request(req.id, "viewer_bob", "No")
        assert result.success is False
        assert "not an admin" in result.message


# ─── Query and Expiration Tests ───────────────────────────────────────


class TestQueriesAndExpiration:
    """Test request queries and expiration logic."""

    @pytest.mark.asyncio
    async def test_get_pending_requests(self, auth_flow: AuthorizationFlow):
        """Can list pending requests by division."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("admin_bob", "tourism", "admin")
        await auth_flow.rbac.assign_role("admin_carol", "gas_oil", "admin")

        await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "reason1",
        )
        await auth_flow.request_access(
            "alice",
            "gas_oil",
            "general_trading",
            "inventory",
            "reason2",
        )

        pending = await auth_flow.get_pending_requests()
        assert len(pending) == 2

        pending_tourism = await auth_flow.get_pending_requests("tourism")
        assert len(pending_tourism) == 1
        assert pending_tourism[0].target_division == "tourism"

    @pytest.mark.asyncio
    async def test_audit_trail_on_approval(self, auth_flow: AuthorizationFlow):
        """Approval is audit logged."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("admin_bob", "tourism", "admin")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "auditable reason",
        )
        await auth_flow.approve_request(req.id, "admin_bob")

        trail = await auth_flow.audit.get_audit_trail(division="tourism")
        # Find the authorization_decision entry
        decisions = [e for e in trail if e.event_type == "authorization_decision"]
        assert len(decisions) >= 1
        assert decisions[0].result == "approved"
        assert decisions[0].approver_id == "admin_bob"

    @pytest.mark.asyncio
    async def test_audit_trail_on_denial(self, auth_flow: AuthorizationFlow):
        """Denial is audit logged."""
        await auth_flow.rbac.assign_role("alice", "gas_oil", "trader")
        await auth_flow.rbac.assign_role("admin_bob", "tourism", "admin")

        req = await auth_flow.request_access(
            "alice",
            "gas_oil",
            "tourism",
            "bookings",
            "auditable reason",
        )
        await auth_flow.deny_request(req.id, "admin_bob", "Policy violation")

        trail = await auth_flow.audit.get_audit_trail(division="tourism")
        decisions = [e for e in trail if e.event_type == "authorization_decision"]
        assert len(decisions) >= 1
        assert decisions[0].result == "denied"
