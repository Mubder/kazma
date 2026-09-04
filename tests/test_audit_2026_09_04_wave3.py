"""Wave 3 verification tests: Turn & Job Liveness.

Covers:
- H3: Recurring cron failure budget (3 strikes), ops alert, case-insensitive daily timing.
- H6: Commitment clarify/confirm checkpointer-less / auto_deny guard (no hung interrupt()).
- H12: Commitment CAS state transitions (no late approve / revive race).
- M1: Swarm bus approval timeout dynamically resolved from settings.
- H4: SessionStore atomic update() for per-thread RMW serialization.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.agent.state import SupervisorState
from kazma_core.cron.scheduler import (
    CronScheduler,
    JobStatus,
    ScheduledJob,
    SQLiteCronStore,
)
from kazma_core.safety.commitment import EffectDecision
from kazma_core.safety.commitment.store import (
    Commitment,
    create_commitment,
    get_commitment,
    sweep_expired,
    update_status,
)
from kazma_core.safety.side_effects import get_effect_profile
from kazma_core.swarm.bus import SwarmMessageBus, _DEFAULT_APPROVAL_TIMEOUT
from kazma_core.swarm.safety import SafetyMiddleware
from kazma_gateway.agent_handler.store import _InMemoryStore
from kazma_gateway.stores.sqlite import SQLiteSessionStore


# ============================================================================
# H3: Cron Failure Budget & Case-Insensitive Daily Timing
# ============================================================================

@pytest.mark.asyncio
async def test_cron_timing_case_insensitive(tmp_path):
    db_path = str(tmp_path / "cron.db")
    store = SQLiteCronStore(db_path)
    await store.init()
    scheduler = CronScheduler(store=store)

    job = ScheduledJob(
        job_id="job_daily_test",
        timing="  DaILy at 9am  ",
        prompt="run daily report",
        platform="telegram",
        thread_id="t1",
        status=JobStatus.RUNNING,
    )
    await store.insert(job)

    # Stale recovery: should detect daily timing case-insensitively and reschedule
    await scheduler._recover_stale_running()
    refreshed = (await store.list_active())[0]
    assert refreshed.status == JobStatus.PENDING
    assert refreshed.next_run is not None
    await store.close()


@pytest.mark.asyncio
async def test_cron_failure_budget_and_alerts(tmp_path):
    db_path = str(tmp_path / "cron.db")
    store = SQLiteCronStore(db_path)
    await store.init()
    scheduler = CronScheduler(store=store)

    job = ScheduledJob(
        job_id="failing_job",
        timing="daily at 9am",
        prompt="fail me",
        platform="telegram",
        thread_id="t1",
        status=JobStatus.RUNNING,
    )
    await store.insert(job)
    job_id = job.job_id

    # Mock ops alert
    with patch("kazma_core.observability.ops_alerts.alert") as mock_alert:
        # First failure
        fresh_job = (await store.list_active())[0]
        await scheduler._finalize(fresh_job, failed=True)
        jobs = await store.list_active()
        assert len(jobs) == 1
        assert jobs[0].failure_count == 1
        assert jobs[0].status == JobStatus.PENDING  # rescheduled
        mock_alert.assert_called_once()
        assert "cron.job_failure" in mock_alert.call_args[0]

        # Second failure
        fresh_job = (await store.list_active())[0]
        await scheduler._finalize(fresh_job, failed=True)
        jobs = await store.list_active()
        assert jobs[0].failure_count == 2
        assert jobs[0].status == JobStatus.PENDING

        # Third failure - failure budget exceeded
        fresh_job = (await store.list_active())[0]
        await scheduler._finalize(fresh_job, failed=True)
        # Should now be marked FAILED (no longer in list_active)
        active_jobs = await store.list_active()
        assert len(active_jobs) == 0
        all_jobs = await store.list_all()
        assert len(all_jobs) == 1
        assert all_jobs[0].failure_count == 3
        assert all_jobs[0].status == JobStatus.FAILED

    # Test success resets failure count
    job2 = ScheduledJob(
        job_id="flaky_job",
        timing="daily at 9am",
        prompt="recover me",
        platform="telegram",
        thread_id="t1",
        status=JobStatus.RUNNING,
    )
    await store.insert(job2)
    await store.bump_failure(job2.job_id)
    jobs = await store.list_active()
    job2_active = [j for j in jobs if j.job_id == job2.job_id][0]
    assert job2_active.failure_count == 1

    await scheduler._finalize(job2_active, failed=False)
    all_jobs = await store.list_all()
    job2_final = [j for j in all_jobs if j.job_id == job2.job_id][0]
    assert job2_final.failure_count == 0
    await store.close()


# ============================================================================
# H6: Commitment clarify/confirm checkpointer-less / auto_deny guard
# ============================================================================

def test_commitment_resolve_gate_checkpointerless_auto_deny():
    from kazma_core.agent.graph_tool_worker import _commitment_resolve_gate

    profile = get_effect_profile("schedule_task")
    fake_decision = EffectDecision(
        decision="clarify",
        reason="need details",
        profile=profile,
        clarify_question="What time would you like to schedule?",
        commitment_id="cmt-123",
    )

    state: SupervisorState = {
        "thread_id": "test-thread",
        "tenant_id": "default",
        "messages": [],
    }
    pending = [{"id": "call-1", "name": "calendar_schedule", "arguments": {}}]

    with patch("kazma_core.safety.commitment.authorize_effect", return_value=fake_decision), \
         patch("kazma_core.safety.commitment.constraints.is_commitment_enabled", return_value=True), \
         patch("kazma_core.safety.side_effects.requires_semantic_check", return_value=True):

        # When allow_interrupt=False (checkpointer-less or auto_deny), it must NOT interrupt
        open_pending, blocked = _commitment_resolve_gate(state, pending, allow_interrupt=False)
        assert len(open_pending) == 0
        assert len(blocked) == 1
        assert blocked[0]["is_error"] is True
        assert blocked[0]["outcome"] == "terminal"
        assert "cannot pause for human approval" in blocked[0]["content"]


# ============================================================================
# H12: Commitment CAS state transitions (no late approve / revive race)
# ============================================================================

def test_commitment_cas_prevents_revival_of_expired(tmp_path, monkeypatch):
    test_db = str(tmp_path / "commitments.db")
    monkeypatch.setattr("kazma_core.safety.commitment.store.memory_ops_db", lambda: test_db)

    now = time.time()
    cmt = Commitment(
        thread_id="t1",
        tenant_id="default",
        act="calendar_event",
        slots={"title": "Test"},
        status="ready",
        expires_at=now + 0.1,
    )
    cid = create_commitment(cmt)
    assert cid is not None

    # Expire it directly via sweep_expired
    time.sleep(0.2)
    swept = sweep_expired(now=time.time())
    assert swept >= 1
    assert get_commitment(cid).status == "expired"

    # Attempting to update_status to "committed" must be refused and return expired
    updated = update_status(cid, "committed")
    assert updated is not None
    assert updated.status == "expired"


# ============================================================================
# M1: Swarm Bus Approval Timeout from Settings
# ============================================================================

def test_swarm_safety_middleware_timeout_settings():
    middleware = SafetyMiddleware(enabled=True)
    assert middleware.approval_timeout == 300.0

    # Dynamic lookup from get_hitl_config
    with patch("kazma_core.safety.hitl.get_hitl_config", return_value={"approval_timeout_seconds": 120.0}):
        assert middleware.approval_timeout == 120.0

    # Clamping tests
    with patch("kazma_core.safety.hitl.get_hitl_config", return_value={"approval_timeout_seconds": 2.0}):
        assert middleware.approval_timeout == 10.0  # Min 10s

    with patch("kazma_core.safety.hitl.get_hitl_config", return_value={"approval_timeout_seconds": 1000.0}):
        assert middleware.approval_timeout == 600.0  # Max 600s


@pytest.mark.asyncio
async def test_swarm_bus_request_approval_default_timeout():
    mock_adapter = MagicMock()
    mock_adapter.request_approval = MagicMock(return_value=asyncio.Future())
    mock_adapter.request_approval.return_value.set_result(True)

    bus = SwarmMessageBus(adapter=mock_adapter)
    with patch("kazma_core.safety.hitl.get_hitl_config", return_value={"approval_timeout_seconds": 45.0}):
        approved = await bus.request_approval(
            worker_name="tester",
            task_description="test task",
            proposed_output="output",
        )
        assert approved is True
        # Verify timeout passed to adapter was resolved from hitl config (45.0)
        assert mock_adapter.request_approval.call_args[1]["timeout"] == 45.0


# ============================================================================
# H4: SessionStore Atomic update()
# ============================================================================

@pytest.mark.asyncio
async def test_in_memory_session_store_atomic_update():
    store = _InMemoryStore()
    thread_id = "thread-rmw-test"
    await store.put(thread_id, {"counter": 0})

    async def worker():
        for _ in range(50):
            def _inc(ctx: dict[str, Any]) -> dict[str, Any]:
                ctx["counter"] = ctx.get("counter", 0) + 1
                return ctx

            await store.update(thread_id, _inc)
            await asyncio.sleep(0.001)

    # 4 concurrent workers incrementing 50 times each = 200 total
    await asyncio.gather(*[worker() for _ in range(4)])
    final_ctx = await store.get(thread_id)
    assert final_ctx["counter"] == 200


@pytest.mark.asyncio
async def test_sqlite_session_store_atomic_update(tmp_path):
    db_path = str(tmp_path / "sessions.db")
    store = SQLiteSessionStore(db_path=db_path)
    thread_id = "thread-sqlite-rmw"
    await store.put(thread_id, {"count": 0})

    async def worker():
        for _ in range(25):
            def _inc(ctx: dict[str, Any]) -> dict[str, Any]:
                ctx["count"] = ctx.get("count", 0) + 1
                return ctx

            await store.update(thread_id, _inc)
            await asyncio.sleep(0.001)

    # 4 concurrent workers incrementing 25 times each = 100 total
    await asyncio.gather(*[worker() for _ in range(4)])
    final_ctx = await store.get(thread_id)
    assert final_ctx["count"] == 100
