"""Tests for gw-061 bug fixes.

BUG 1: Optional[T] / Union[X, None] type schema generation
BUG 2: RBAC reads from division_permissions table (not hardcoded dict)
BUG 3: _legacy_agent.py renamed to agent_runner.py
BUG 4: check_expired revokes the viewer role from DB
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Optional, Union

import pytest
import pytest_asyncio

from kazma_core.audit_logger import AuditLogger
from kazma_core.authorization_flow import AuthorizationFlow
from kazma_core.rbac import RBACEngine, _DEFAULT_PERMISSIONS


# ── BUG 1: Optional/Union type schema tests ─────────────────────────


class TestOptionalSchemaBug:
    """BUG 1: _python_type_to_json_schema must handle Optional[T] correctly."""

    def test_optional_str_returns_string_schema(self):
        """Optional[str] should produce {'type': 'string'}, not {'type': 'string'} fallback."""
        from kazma_core.agent.tool_registry import _python_type_to_json_schema

        schema = _python_type_to_json_schema(Optional[str])
        assert schema == {"type": "string"}

    def test_optional_int_returns_integer_schema(self):
        """Optional[int] should produce {'type': 'integer'}."""
        from kazma_core.agent.tool_registry import _python_type_to_json_schema

        schema = _python_type_to_json_schema(Optional[int])
        assert schema == {"type": "integer"}

    def test_union_with_none_returns_inner_type(self):
        """Union[str, None] should produce {'type': 'string'} (same as Optional[str])."""
        from kazma_core.agent.tool_registry import _python_type_to_json_schema

        schema = _python_type_to_json_schema(Union[str, None])
        assert schema == {"type": "string"}

    def test_pipe_syntax_optional(self):
        """str | None (Python 3.10+ syntax) should produce {'type': 'string'}."""
        from kazma_core.agent.tool_registry import _python_type_to_json_schema

        schema = _python_type_to_json_schema(str | None)
        assert schema == {"type": "string"}

    def test_plain_str_still_works(self):
        """Bare str should still produce {'type': 'string'} (regression check)."""
        from kazma_core.agent.tool_registry import _python_type_to_json_schema

        schema = _python_type_to_json_schema(str)
        assert schema == {"type": "string"}


# ── BUG 2: RBAC reads from DB, not hardcoded dict ───────────────────


class TestRBACDBPermissionsBug:
    """BUG 2: check_permission must query division_permissions table."""

    @pytest.mark.asyncio
    async def test_permission_comes_from_db(self, tmp_path):
        """Custom permission inserted into DB should be used by check_permission."""
        db_path = str(tmp_path / "rbac.db")
        engine = RBACEngine(db_path=db_path)
        try:
            db = await engine._get_db()
            # Add a custom permission not in hardcoded defaults
            await db.execute(
                "INSERT INTO division_permissions (division, role, resource_pattern, actions) "
                "VALUES (?, ?, ?, ?)",
                ("gas_oil", "viewer", "reports", json.dumps(["read", "export"])),
            )
            await db.commit()

            await engine.assign_role("alice", "gas_oil", "viewer")
            result = await engine.check_permission("alice", "gas_oil", "reports", "export")
            assert result.allowed is True, "DB-defined permission 'export' should be allowed"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_fallback_when_db_empty(self, tmp_path):
        """When division_permissions table has no rows for the role, fallback to defaults."""
        db_path = str(tmp_path / "rbac.db")
        engine = RBACEngine(db_path=db_path)
        try:
            db = await engine._get_db()
            # Clear all permissions from the table
            await db.execute("DELETE FROM division_permissions")
            await db.commit()

            await engine.assign_role("alice", "gas_oil", "admin")
            result = await engine.check_permission("alice", "gas_oil", "pricing", "read")
            assert result.allowed is True, "Hardcoded fallback should still grant 'read'"
        finally:
            await engine.close()

    @pytest.mark.asyncio
    async def test_db_overrides_hardcoded(self, tmp_path):
        """DB permissions take precedence over hardcoded defaults."""
        db_path = str(tmp_path / "rbac.db")
        engine = RBACEngine(db_path=db_path)
        try:
            db = await engine._get_db()
            # Remove the default wildcard read for viewer and add a restricted set
            await db.execute(
                "DELETE FROM division_permissions WHERE division = ? AND role = ?",
                ("gas_oil", "viewer"),
            )
            await db.execute(
                "INSERT INTO division_permissions (division, role, resource_pattern, actions) "
                "VALUES (?, ?, ?, ?)",
                ("gas_oil", "viewer", "pricing", json.dumps(["read"])),
            )
            await db.commit()

            await engine.assign_role("alice", "gas_oil", "viewer")
            # Should still allow read on pricing (explicitly in DB)
            result = await engine.check_permission("alice", "gas_oil", "pricing", "read")
            assert result.allowed is True
            # Should NOT allow read on contracts (wildcard removed from DB)
            result2 = await engine.check_permission("alice", "gas_oil", "contracts", "read")
            assert result2.allowed is False, "Without wildcard, viewer should be denied on contracts"
        finally:
            await engine.close()


# ── BUG 3: _legacy_agent.py renamed to agent_runner.py ──────────────


class TestAgentRunnerRename:
    """BUG 3: The production agent module should be importable as agent_runner."""

    def test_agent_runner_module_exists(self):
        """kazma_core.agent_runner should be importable."""
        import kazma_core.agent_runner as mod
        assert hasattr(mod, "KazmaAgent")
        assert hasattr(mod, "AgentConfig")
        assert hasattr(mod, "run_agent")

    def test_no_legacy_module(self):
        """kazma_core._legacy_agent should NOT exist."""
        import importlib
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("kazma_core._legacy_agent")

    def test_agent_package_still_reexports(self):
        """kazma_core.agent should still re-export the names for backward compat."""
        from kazma_core.agent import KazmaAgent, AgentConfig, run_agent
        assert KazmaAgent is not None
        assert AgentConfig is not None


# ── BUG 4: check_expired revokes the viewer role ────────────────────


class TestExpireRevokesRole:
    """BUG 4: Expiring an approved request must revoke the viewer role."""

    @pytest_asyncio.fixture
    async def flow(self, tmp_path):
        rbac = RBACEngine(db_path=str(tmp_path / "rbac.db"))
        audit = AuditLogger(db_path=str(tmp_path / "audit.db"))
        f = AuthorizationFlow(rbac=rbac, audit_logger=audit)
        yield f
        await rbac.close()
        await audit.close()

    @pytest.mark.asyncio
    async def test_expiry_revokes_viewer_role(self, flow):
        """After expiration, the viewer role granted on approval should be removed."""
        await flow.rbac.assign_role("alice", "gas_oil", "trader")
        await flow.rbac.assign_role("admin_bob", "tourism", "admin")

        req = await flow.request_access(
            "alice", "gas_oil", "tourism", "bookings", "reason",
        )
        result = await flow.approve_request(req.id, "admin_bob")
        assert result.success is True

        # Alice should have viewer in tourism
        roles_before = await flow.rbac.get_user_roles("alice", "tourism")
        assert any(r["role"] == "viewer" for r in roles_before)

        # Backdate the expiry to force expiration
        req_obj = await flow.get_request(req.id)
        req_obj.expires_at = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()

        expired = await flow.check_expired()
        assert req.id in expired

        # Viewer role should now be revoked
        roles_after = await flow.rbac.get_user_roles("alice", "tourism")
        assert not any(r["role"] == "viewer" for r in roles_after), \
            "Viewer role should be revoked after expiry"

    @pytest.mark.asyncio
    async def test_unexpired_request_keeps_role(self, flow):
        """If request hasn't expired yet, the viewer role should remain."""
        await flow.rbac.assign_role("alice", "gas_oil", "trader")
        await flow.rbac.assign_role("admin_bob", "tourism", "admin")

        req = await flow.request_access(
            "alice", "gas_oil", "tourism", "bookings", "reason",
        )
        await flow.approve_request(req.id, "admin_bob")

        # Don't backdate — should not expire
        expired = await flow.check_expired()
        assert req.id not in expired

        roles = await flow.rbac.get_user_roles("alice", "tourism")
        assert any(r["role"] == "viewer" for r in roles)
