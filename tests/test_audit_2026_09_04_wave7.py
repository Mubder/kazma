"""Industrial Audit 2026-09-04 — Wave 7 Verification Suite.

Validates:
  L1: Dead imports and unused _pending_dispatch_tasks removed from tool_registry.
  L2: Dead functions removed from long_task (__all__ and module namespace).
  L3: Clean llm resolution in graph_respond (no undeclared state._llm fallback).
  L4: Redundant router branch removed from graph_builder.
  L7: handled_in_process removed from CallbackAction dataclass.
  L8: SESSION_TTL_SECONDS shared import & dynamic formatting in gateway.
  L9: Card storm suppression pruning & bounding in gateway hitl.
  L10: SQLiteCronStore purge_terminal_jobs & bounded list_all.
  L11: ConfigStore atomic_update and shared_approvals reject vote CAS atomicity.
  L12: ReliabilityRegistry _concurrency_cache bounding & limit clamping.
  M21: Duplicate DELETE /api/mcp/servers removed from routes_direct/misc.py.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestWave7DeadCodeAndSignatures:
    """Tests L1, L2, L3, L4, L7, M21."""

    def test_l1_tool_registry_clean(self) -> None:
        import kazma_core.agent.tool_registry as tr_mod

        assert not hasattr(tr_mod, "_pending_dispatch_tasks")
        src = inspect.getsource(tr_mod)
        assert "_pending_dispatch_tasks" not in src
        assert "from datetime import UTC" not in src

    def test_l2_long_task_dead_symbols_removed(self) -> None:
        import kazma_core.agent.long_task as lt_mod

        assert not hasattr(lt_mod, "tool_call_signature")
        assert not hasattr(lt_mod, "record_budget_exhausted")
        assert "tool_call_signature" not in lt_mod.__all__
        assert "record_budget_exhausted" not in lt_mod.__all__

    def test_l3_graph_respond_no_state_llm_fallback(self) -> None:
        import kazma_core.agent.graph_respond as gr_mod

        src = inspect.getsource(gr_mod)
        assert 'state.get("_llm")' not in src
        assert "_llm = llm\n" in src

    def test_l4_graph_builder_router_clean(self) -> None:
        import kazma_core.agent.graph_builder as gb_mod

        src = inspect.getsource(gb_mod)
        assert "elif _used < _hard and max_iter < _hard:" not in src

    def test_l7_callback_action_handled_in_process_removed(self) -> None:
        from kazma_gateway.adapters.platform_callbacks import CallbackAction

        sig = inspect.signature(CallbackAction)
        assert "handled_in_process" not in sig.parameters

    def test_m21_misc_routes_no_duplicate_mcp_delete(self) -> None:
        import kazma_ui.routes_direct.misc as misc_mod

        src = inspect.getsource(misc_mod)
        assert '@self.app.delete("/api/mcp/servers/{server_name}")' not in src


class TestWave7GatewayConsistency:
    """Tests L8, L9."""

    def test_l8_session_ttl_canonical_import(self) -> None:
        from kazma_core.sessions.ttl import SESSION_TTL_SECONDS
        import kazma_gateway.agent_handler.graph as gw_graph

        assert hasattr(gw_graph, "SESSION_TTL_SECONDS")
        assert gw_graph.SESSION_TTL_SECONDS == SESSION_TTL_SECONDS

        import kazma_gateway.agent_handler.hitl as gw_hitl

        src = inspect.getsource(gw_hitl)
        assert "SESSION_TTL_SECONDS" in src

    def test_l9_card_storm_pruning_and_bounding(self) -> None:
        import time
        from kazma_gateway.agent_handler.hitl import _recent_cards, approval_card_suppressed

        _recent_cards.clear()
        now = time.time()

        # Seed expired entries (>240s old)
        for i in range(150):
            _recent_cards[f"old-thread-{i}"] = [(now - 500.0, "fp", True)]

        # Trigger suppression check on a new thread
        approval_card_suppressed("active-thread-1", "bash", {"command": "ls"})

        # Stale threads should have been pruned
        assert len(_recent_cards) < 128
        assert "active-thread-1" in _recent_cards
        assert len(_recent_cards["active-thread-1"]) == 1

        # Test hard cap ceiling at 512
        for i in range(600):
            approval_card_suppressed(f"bulk-thread-{i}", "bash", {"command": f"echo {i}"})

        assert len(_recent_cards) <= 512
        _recent_cards.clear()


class TestWave7CronPurgeAndReliability:
    """Tests L10, L11, L12."""

    @pytest.mark.asyncio
    async def test_l10_cron_store_purge_terminal_jobs(self, tmp_path: Path) -> None:
        from datetime import datetime, timedelta, timezone
        from kazma_core.cron.scheduler import SQLiteCronStore, ScheduledJob, JobStatus

        db_file = tmp_path / "test_cron.db"
        store = SQLiteCronStore(db_path=str(db_file))
        await store.init()

        # Insert 1 active job and 3 terminal jobs
        job_active = ScheduledJob(
            job_id="job-active",
            timing="5m",
            prompt="active prompt",
            platform="telegram",
            thread_id="t1",
            status=JobStatus.PENDING,
        )
        await store.insert(job_active)

        # 2 old terminal jobs (>20 days old)
        old_time = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        job_old_done = ScheduledJob(
            job_id="job-old-done",
            timing="5m",
            prompt="done prompt",
            platform="telegram",
            thread_id="t1",
            status=JobStatus.DONE,
            created_at=old_time,
        )
        await store.insert(job_old_done)

        job_old_failed = ScheduledJob(
            job_id="job-old-failed",
            timing="5m",
            prompt="failed prompt",
            platform="telegram",
            thread_id="t1",
            status=JobStatus.FAILED,
            created_at=old_time,
        )
        await store.insert(job_old_failed)

        # 1 recent terminal job (today)
        job_recent_done = ScheduledJob(
            job_id="job-recent-done",
            timing="5m",
            prompt="recent prompt",
            platform="telegram",
            thread_id="t1",
            status=JobStatus.DONE,
        )
        await store.insert(job_recent_done)

        all_before = await store.list_all(limit=100)
        assert len(all_before) == 4

        # Purge jobs older than 14 days
        purged = await store.purge_terminal_jobs(older_than_days=14, keep_last=10)
        assert purged == 2

        all_after = await store.list_all(limit=100)
        ids = {j.job_id for j in all_after}
        assert ids == {"job-active", "job-recent-done"}

        # Verify limit parameter in list_all
        limited = await store.list_all(limit=1)
        assert len(limited) == 1

        await store.close()

    def test_l11_config_store_atomic_update(self, tmp_path: Path) -> None:
        from kazma_core.config_store import ConfigStore

        db_file = tmp_path / "test_config.db"
        cs = ConfigStore(db_path=str(db_file))

        # Initial set
        cs.set("counter.val", {"count": 1})

        def _increment(val: Any) -> Any:
            d = dict(val) if isinstance(val, dict) else {}
            d["count"] = d.get("count", 0) + 1
            return d

        updated = cs.atomic_update("counter.val", _increment)
        assert updated == {"count": 2}
        assert cs.get("counter.val") == {"count": 2}

        # Multi-update check
        for _ in range(5):
            cs.atomic_update("counter.val", _increment)

        assert cs.get("counter.val") == {"count": 7}
        cs.close()

    @pytest.mark.asyncio
    async def test_l11_shared_approvals_reject_vote_cas(self, tmp_path: Path) -> None:
        from kazma_core.config_store import ConfigStore
        from kazma_core.swarm import shared_approvals

        db_file = tmp_path / "test_shared_appr.db"
        cs = ConfigStore(db_path=str(db_file))

        with patch("kazma_core.config_store.get_config_store", return_value=cs):
            shared_approvals.create_pending("task-concurrent-reject", expected_voters=3)

            # Two concurrent reject votes
            shared_approvals.resolve("task-concurrent-reject", False)
            shared_approvals.resolve("task-concurrent-reject", False)

            # Check durable payload: reject_votes must be 2, status must still be pending
            key = shared_approvals._key("task-concurrent-reject")
            payload = cs.get(key)
            assert payload is not None
            assert payload["reject_votes"] == 2
            assert payload["status"] == "pending"

            # Third reject vote reaches expected_voters=3 -> resolves to False
            shared_approvals.resolve("task-concurrent-reject", False)
            payload3 = cs.get(key)
            assert payload3["reject_votes"] == 3
            assert payload3["status"] == "resolved"
            assert payload3["result"] is False

        cs.close()

    def test_l12_reliability_registry_concurrency_bounding(self) -> None:
        from kazma_core.swarm.reliability_registry import ReliabilityRegistry

        registry = ReliabilityRegistry(worker_names=lambda: [], default_max_concurrent=5)

        # Clamping checks: negative, zero, or absurdly large
        bc_neg = registry.get_bounded_concurrency(-10)
        assert bc_neg.max_concurrent == 1

        bc_large = registry.get_bounded_concurrency(999999)
        assert bc_large.max_concurrent == 1024

        # Bound cache size to 64
        for i in range(1, 100):
            registry.get_bounded_concurrency(i)

        assert len(registry._concurrency_cache) <= 64
