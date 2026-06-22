"""Tests for RBAC Engine — Role-Based Access Control.

Covers:
- Role assignment and revocation
- Permission checks per division/role
- Sensitive resource detection
- Division boundary enforcement
- User role queries
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from kazma_core.rbac import DIVISIONS, RBACEngine


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary RBAC database."""
    return str(tmp_path / "rbac.db")


@pytest_asyncio.fixture
async def rbac(tmp_db):
    """Return an RBACEngine with a fresh database."""
    engine = RBACEngine(db_path=tmp_db)
    yield engine
    await engine.close()


# ─── Role Management Tests ────────────────────────────────────────────

class TestRoleManagement:
    """Test role assignment and revocation."""

    @pytest.mark.asyncio
    async def test_assign_role(self, rbac: RBACEngine):
        """Assign a role and verify it persists."""
        result = await rbac.assign_role("alice", "gas_oil", "trader")
        assert result is True

        roles = await rbac.get_user_roles("alice", "gas_oil")
        assert len(roles) == 1
        assert roles[0]["role"] == "trader"
        assert roles[0]["division"] == "gas_oil"

    @pytest.mark.asyncio
    async def test_assign_role_invalid_division(self, rbac: RBACEngine):
        """Cannot assign role to unknown division."""
        result = await rbac.assign_role("alice", "unknown_division", "admin")
        assert result is False

    @pytest.mark.asyncio
    async def test_assign_role_invalid_role(self, rbac: RBACEngine):
        """Cannot assign invalid role for division."""
        result = await rbac.assign_role("alice", "gas_oil", "president")
        assert result is False

    @pytest.mark.asyncio
    async def test_revoke_role(self, rbac: RBACEngine):
        """Revoke a role and verify removal."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        result = await rbac.revoke_role("alice", "gas_oil", "trader")
        assert result is True

        roles = await rbac.get_user_roles("alice", "gas_oil")
        assert len(roles) == 0

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_role(self, rbac: RBACEngine):
        """Revoking a role that doesn't exist returns False."""
        result = await rbac.revoke_role("alice", "gas_oil", "trader")
        assert result is False

    @pytest.mark.asyncio
    async def test_multiple_roles_same_user(self, rbac: RBACEngine):
        """User can have roles in multiple divisions."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        await rbac.assign_role("alice", "tourism", "manager")

        roles_all = await rbac.get_user_roles("alice")
        assert len(roles_all) == 2

        roles_gas = await rbac.get_user_roles("alice", "gas_oil")
        assert len(roles_gas) == 1
        assert roles_gas[0]["role"] == "trader"

        roles_tourism = await rbac.get_user_roles("alice", "tourism")
        assert len(roles_tourism) == 1
        assert roles_tourism[0]["role"] == "manager"

    @pytest.mark.asyncio
    async def test_get_division_users(self, rbac: RBACEngine):
        """Get all users in a division."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        await rbac.assign_role("bob", "gas_oil", "analyst")
        await rbac.assign_role("charlie", "tourism", "agent")

        users = await rbac.get_division_users("gas_oil")
        assert len(users) == 2
        user_ids = {u["user_id"] for u in users}
        assert "alice" in user_ids
        assert "bob" in user_ids

    @pytest.mark.asyncio
    async def test_get_user_divisions(self, rbac: RBACEngine):
        """Get all divisions a user belongs to."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        await rbac.assign_role("alice", "tourism", "manager")

        divisions = await rbac.get_user_divisions("alice")
        assert set(divisions) == {"gas_oil", "tourism"}


# ─── Permission Check Tests ───────────────────────────────────────────

class TestPermissionChecks:
    """Test permission checking logic."""

    @pytest.mark.asyncio
    async def test_viewer_can_read(self, rbac: RBACEngine):
        """Viewer role can read any resource."""
        await rbac.assign_role("alice", "gas_oil", "viewer")
        result = await rbac.check_permission("alice", "gas_oil", "pricing", "read")
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_viewer_cannot_write(self, rbac: RBACEngine):
        """Viewer role cannot write."""
        await rbac.assign_role("alice", "gas_oil", "viewer")
        result = await rbac.check_permission("alice", "gas_oil", "pricing", "write")
        assert result.allowed is False
        assert "does not have" in result.reason

    @pytest.mark.asyncio
    async def test_trader_can_write(self, rbac: RBACEngine):
        """Trader role can write general resources."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        result = await rbac.check_permission("alice", "gas_oil", "pricing", "write")
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_admin_full_access(self, rbac: RBACEngine):
        """Admin role has full access."""
        await rbac.assign_role("alice", "gas_oil", "admin")
        for action in ["read", "write", "delete", "admin"]:
            result = await rbac.check_permission("alice", "gas_oil", "pricing", action)
            assert result.allowed is True, f"Admin should have '{action}' access"

    @pytest.mark.asyncio
    async def test_no_role_denied(self, rbac: RBACEngine):
        """User with no role in division is denied."""
        result = await rbac.check_permission("alice", "gas_oil", "pricing", "read")
        assert result.allowed is False
        assert "no role" in result.reason

    @pytest.mark.asyncio
    async def test_unknown_division(self, rbac: RBACEngine):
        """Unknown division returns denied."""
        result = await rbac.check_permission("alice", "unknown", "pricing", "read")
        assert result.allowed is False
        assert "Unknown division" in result.reason

    @pytest.mark.asyncio
    async def test_sensitive_resource_requires_approval(self, rbac: RBACEngine):
        """Sensitive resources like contracts require approval for write."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        result = await rbac.check_permission("alice", "gas_oil", "contracts", "write")
        assert result.allowed is True
        assert result.requires_approval is True

    @pytest.mark.asyncio
    async def test_sensitive_read_no_approval(self, rbac: RBACEngine):
        """Reading sensitive resources does not require approval."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        result = await rbac.check_permission("alice", "gas_oil", "contracts", "read")
        assert result.allowed is True
        assert result.requires_approval is False

    @pytest.mark.asyncio
    async def test_division_isolation(self, rbac: RBACEngine):
        """Roles don't transfer across divisions."""
        await rbac.assign_role("alice", "gas_oil", "admin")

        # Alice is admin in gas_oil, but has no role in tourism
        result = await rbac.check_permission("alice", "tourism", "bookings", "read")
        assert result.allowed is False


# ─── Division Boundary Tests ──────────────────────────────────────────

class TestDivisionBoundaries:
    """Test division isolation and boundaries."""

    @pytest.mark.asyncio
    async def test_division_definitions(self):
        """All expected divisions are defined."""
        assert "gas_oil" in DIVISIONS
        assert "tourism" in DIVISIONS
        assert "general_trading" in DIVISIONS

    @pytest.mark.asyncio
    async def test_division_roles(self):
        """Each division has expected roles."""
        assert "admin" in DIVISIONS["gas_oil"]["roles"]
        assert "admin" in DIVISIONS["tourism"]["roles"]
        assert "admin" in DIVISIONS["general_trading"]["roles"]

    @pytest.mark.asyncio
    async def test_sensitive_data_per_division(self):
        """Each division has sensitive data defined."""
        assert "contracts" in DIVISIONS["gas_oil"]["sensitive_data"]
        assert "bookings" in DIVISIONS["tourism"]["sensitive_data"]
        assert "inventory" in DIVISIONS["general_trading"]["sensitive_data"]

    @pytest.mark.asyncio
    async def test_tourism_viewer_cannot_access_gas_oil(self, rbac: RBACEngine):
        """Tourism viewer has no access to gas_oil."""
        await rbac.assign_role("alice", "tourism", "viewer")
        result = await rbac.check_permission("alice", "gas_oil", "pricing", "read")
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_clear(self, rbac: RBACEngine):
        """Clear removes all user roles."""
        await rbac.assign_role("alice", "gas_oil", "trader")
        await rbac.assign_role("bob", "tourism", "agent")
        count = await rbac.clear()
        assert count == 2

        roles = await rbac.get_user_roles("alice")
        assert len(roles) == 0
