"""Tests for Audit Logger — Access and authorization audit trails.

Covers:
- Access attempt logging
- Authorization decision logging
- Audit trail querying with filters
- Clear functionality
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from kazma_core.audit_logger import AuditLogger


@pytest.fixture
def tmp_audit_db(tmp_path):
    """Return a path to a temporary audit database."""
    return str(tmp_path / "audit.db")


@pytest_asyncio.fixture
async def audit(tmp_audit_db):
    """Return an AuditLogger with a fresh database."""
    logger = AuditLogger(db_path=tmp_audit_db)
    yield logger
    await logger.close()


# ─── Access Attempt Logging Tests ──────────────────────────────────────


class TestAccessLogging:
    """Test access attempt logging."""

    @pytest.mark.asyncio
    async def test_log_access_attempt(self, audit: AuditLogger):
        """Can log an access attempt."""
        entry = await audit.log_access_attempt(
            user_id="alice",
            division="gas_oil",
            resource="pricing",
            action="read",
            result="allowed",
        )
        assert entry.id != ""
        assert entry.event_type == "access_attempt"
        assert entry.user_id == "alice"
        assert entry.division == "gas_oil"
        assert entry.resource == "pricing"
        assert entry.action == "read"
        assert entry.result == "allowed"

    @pytest.mark.asyncio
    async def test_log_access_denied(self, audit: AuditLogger):
        """Can log a denied access attempt."""
        entry = await audit.log_access_attempt(
            user_id="alice",
            division="gas_oil",
            resource="pricing",
            action="write",
            result="denied",
            reason="No permission",
        )
        assert entry.result == "denied"
        assert entry.reason == "No permission"

    @pytest.mark.asyncio
    async def test_log_access_with_metadata(self, audit: AuditLogger):
        """Can log access with extra metadata."""
        entry = await audit.log_access_attempt(
            user_id="alice",
            division="gas_oil",
            resource="pricing",
            action="read",
            result="allowed",
            metadata={"request_id": "abc123", "source": "api"},
        )
        assert "abc123" in entry.metadata_json

    @pytest.mark.asyncio
    async def test_entry_has_timestamp(self, audit: AuditLogger):
        """Each entry has an ISO timestamp."""
        entry = await audit.log_access_attempt(
            user_id="alice",
            division="gas_oil",
            resource="x",
            action="r",
            result="allowed",
        )
        ts = datetime.fromisoformat(entry.timestamp)
        assert ts.tzinfo is not None  # Should be UTC


# ─── Authorization Decision Logging Tests ─────────────────────────────


class TestDecisionLogging:
    """Test authorization decision logging."""

    @pytest.mark.asyncio
    async def test_log_authorization_decision(self, audit: AuditLogger):
        """Can log an authorization decision."""
        entry = await audit.log_authorization_decision(
            request_id="req-123",
            approver_id="admin_bob",
            decision="approved",
            reason="Approved for joint venture",
            user_id="alice",
            division="tourism",
            resource="bookings",
        )
        assert entry.event_type == "authorization_decision"
        assert entry.request_id == "req-123"
        assert entry.approver_id == "admin_bob"
        assert entry.result == "approved"

    @pytest.mark.asyncio
    async def test_log_denial_decision(self, audit: AuditLogger):
        """Can log a denial decision."""
        entry = await audit.log_authorization_decision(
            request_id="req-456",
            approver_id="admin_carol",
            decision="denied",
            reason="Policy violation",
        )
        assert entry.result == "denied"
        assert entry.reason == "Policy violation"


# ─── Audit Trail Query Tests ──────────────────────────────────────────


class TestAuditTrail:
    """Test audit trail querying."""

    @pytest.mark.asyncio
    async def test_query_all_entries(self, audit: AuditLogger):
        """Can query all entries."""
        await audit.log_access_attempt("alice", "gas_oil", "x", "r", "allowed")
        await audit.log_access_attempt("bob", "tourism", "y", "w", "denied")

        trail = await audit.get_audit_trail()
        assert len(trail) == 2

    @pytest.mark.asyncio
    async def test_filter_by_user(self, audit: AuditLogger):
        """Can filter by user_id."""
        await audit.log_access_attempt("alice", "gas_oil", "x", "r", "allowed")
        await audit.log_access_attempt("bob", "tourism", "y", "w", "denied")
        await audit.log_access_attempt("alice", "gas_oil", "z", "r", "allowed")

        trail = await audit.get_audit_trail(user_id="alice")
        assert len(trail) == 2
        assert all(e.user_id == "alice" for e in trail)

    @pytest.mark.asyncio
    async def test_filter_by_division(self, audit: AuditLogger):
        """Can filter by division."""
        await audit.log_access_attempt("alice", "gas_oil", "x", "r", "allowed")
        await audit.log_access_attempt("bob", "tourism", "y", "w", "denied")

        trail = await audit.get_audit_trail(division="gas_oil")
        assert len(trail) == 1
        assert trail[0].division == "gas_oil"

    @pytest.mark.asyncio
    async def test_filter_by_date_range(self, audit: AuditLogger):
        """Can filter by date range."""
        await audit.log_access_attempt("alice", "gas_oil", "x", "r", "allowed")

        now = datetime.now(UTC)
        trail = await audit.get_audit_trail(
            start_date=now - timedelta(hours=1),
            end_date=now + timedelta(hours=1),
        )
        assert len(trail) == 1

    @pytest.mark.asyncio
    async def test_filter_by_date_range_no_results(self, audit: AuditLogger):
        """Empty result when date range doesn't match."""
        await audit.log_access_attempt("alice", "gas_oil", "x", "r", "allowed")

        past = datetime(2020, 1, 1, tzinfo=UTC)
        trail = await audit.get_audit_trail(start_date=past, end_date=past)
        assert len(trail) == 0

    @pytest.mark.asyncio
    async def test_limit_results(self, audit: AuditLogger):
        """Can limit number of results."""
        for i in range(10):
            await audit.log_access_attempt("alice", "gas_oil", f"r{i}", "r", "allowed")

        trail = await audit.get_audit_trail(limit=5)
        assert len(trail) == 5

    @pytest.mark.asyncio
    async def test_results_ordered_by_timestamp_desc(self, audit: AuditLogger):
        """Most recent entries come first."""
        await audit.log_access_attempt("alice", "gas_oil", "x", "r", "allowed")
        # Small delay to ensure different timestamps
        import asyncio

        await asyncio.sleep(0.01)
        await audit.log_access_attempt("alice", "gas_oil", "y", "r", "allowed")

        trail = await audit.get_audit_trail()
        assert len(trail) == 2
        # First result should be newer
        assert trail[0].timestamp >= trail[1].timestamp


# ─── Clear Tests ───────────────────────────────────────────────────────


class TestClear:
    """Test clearing audit log."""

    @pytest.mark.asyncio
    async def test_clear_all(self, audit: AuditLogger):
        """Clear removes all entries."""
        await audit.log_access_attempt("alice", "gas_oil", "x", "r", "allowed")
        await audit.log_access_attempt("bob", "tourism", "y", "w", "denied")

        count = await audit.clear()
        assert count == 2

        trail = await audit.get_audit_trail()
        assert len(trail) == 0

    @pytest.mark.asyncio
    async def test_clear_empty_db(self, audit: AuditLogger):
        """Clear on empty DB returns 0."""
        count = await audit.clear()
        assert count == 0
