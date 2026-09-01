"""Wave 6: H-12 swarm fan-out tri-state + M-14 reliability remainder.

H-12 is shared_approvals / FanOut only — web claim_gate is not retargeted.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.swarm import shared_approvals
from kazma_core.swarm.autoscaler import AutoScaler
from kazma_core.swarm.bus import ApprovalRequest, FanOutBusAdapter
from kazma_core.swarm.reliability import CircuitBreaker, CircuitBreakerOpenError, CircuitState
from kazma_core.swarm.task_store import TaskStore


def _mock_store() -> tuple[dict, MagicMock]:
    store: dict = {}
    mock = MagicMock()
    mock.get.side_effect = lambda k, d=None: store.get(k, d)
    mock.set.side_effect = lambda k, v, category="general": store.__setitem__(k, v)
    return store, mock


# ── H-12 ────────────────────────────────────────────────────────────────


def test_single_reject_settles_when_expected_one() -> None:
    store, mock = _mock_store()
    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        shared_approvals.create_pending("t-one")
        shared_approvals.resolve("t-one", False)
        assert shared_approvals.get_result("t-one") is False


def test_reject_is_a_vote_until_expected_voters() -> None:
    store, mock = _mock_store()
    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        shared_approvals.create_pending("t-votes", expected_voters=2)
        shared_approvals.resolve("t-votes", False)
        assert shared_approvals.get_result("t-votes") is None
        row = store.get("swarm.approval.t-votes")
        assert int(row["reject_votes"]) == 1
        shared_approvals.resolve("t-votes", False)
        assert shared_approvals.get_result("t-votes") is False


def test_approve_wins_after_one_reject_when_expected_two() -> None:
    """H-12 ordering A: Discord deny (timeout-as-False) then Telegram Approve."""
    store, mock = _mock_store()
    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        shared_approvals.create_pending("t-deny-then-yes", expected_voters=2)
        shared_approvals.resolve("t-deny-then-yes", False)
        assert shared_approvals.get_result("t-deny-then-yes") is None
        shared_approvals.resolve("t-deny-then-yes", True)
        assert shared_approvals.get_result("t-deny-then-yes") is True


def test_approve_not_clobbered_by_later_reject() -> None:
    """H-12 ordering B: Telegram Approve then Discord deny must stay True."""
    store, mock = _mock_store()
    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        shared_approvals.create_pending("t-yes-then-deny", expected_voters=2)
        shared_approvals.resolve("t-yes-then-deny", True)
        shared_approvals.resolve("t-yes-then-deny", False)
        assert shared_approvals.get_result("t-yes-then-deny") is True


@pytest.mark.asyncio
async def test_timeout_is_a_vote_not_global_deny_when_expected_gt_one() -> None:
    store, mock = _mock_store()
    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        shared_approvals.create_pending("t-timeout-vote", expected_voters=2)
        ok = await shared_approvals.wait_for_resolution("t-timeout-vote", timeout=0.35)
        assert ok is False
        # One waiter gave up — the request is still pending so a later
        # Approve on another platform can still win.
        assert shared_approvals.get_result("t-timeout-vote") is None
        shared_approvals.resolve("t-timeout-vote", True)
        assert shared_approvals.get_result("t-timeout-vote") is True


@pytest.mark.asyncio
async def test_fanout_stamps_expected_voters(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def _cp(task_id, *, meta=None, expected_voters=1, deadline=None):
        seen["task_id"] = task_id
        seen["expected"] = expected_voters
        seen["deadline"] = deadline

    monkeypatch.setattr(
        "kazma_core.swarm.shared_approvals.create_pending", _cp
    )

    class _A:
        async def send(self, message):
            return None

        async def send_report(self, report):
            return None

        async def request_approval(self, approval, timeout=60.0):
            return False

    fan = FanOutBusAdapter([_A(), _A()])  # type: ignore[arg-type]
    ok = await fan.request_approval(
        ApprovalRequest(
            worker_name="w",
            task_description="t",
            proposed_output="x",
            task_id="task-fan",
        ),
        timeout=2.0,
    )
    assert ok is False
    assert seen["task_id"] == "task-fan"
    assert seen["expected"] == 2
    assert seen["deadline"] is not None


def test_h12_comment_is_not_web_claim_gate() -> None:
    src = Path("kazma-core/kazma_core/swarm/shared_approvals.py").read_text(
        encoding="utf-8"
    )
    assert "claim_gate" in src
    assert "NOT web" in src or "not web" in src.lower()


# ── M-14 autoscaler ─────────────────────────────────────────────────────


def test_reap_idle_skips_busy_worker() -> None:
    busy = SimpleNamespace(busy=True, name="busy-w")
    idle = SimpleNamespace(busy=False, name="idle-w")
    engine = MagicMock()
    engine.get_worker.side_effect = lambda n: {"busy-w": busy, "idle-w": idle}.get(n)
    scaler = AutoScaler(engine, idle_ttl=0.01)
    now = time.monotonic()
    scaler._instances["busy-w"] = ("tmpl", now - 10, now - 10)
    scaler._instances["idle-w"] = ("tmpl", now - 10, now - 10)
    n = scaler.reap_idle()
    assert n == 1
    engine.remove_worker.assert_called_once_with("idle-w")
    assert "busy-w" in scaler._instances
    assert "idle-w" not in scaler._instances


def test_dispatch_records_activity_at_completion() -> None:
    src = Path("kazma-core/kazma_core/swarm/worker_dispatch.py").read_text(
        encoding="utf-8"
    )
    assert src.count("record_activity") >= 2
    assert "mark_completed" in src


# ── M-14 breaker ────────────────────────────────────────────────────────


def test_check_or_raise_refreshes_shared_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "1")
    store, mock = _mock_store()
    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        remote = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        remote.record_failure()
        assert remote.state == CircuitState.OPEN
        remote.persist_shared("w-open")
        local = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        assert local.state == CircuitState.CLOSED
        with pytest.raises(CircuitBreakerOpenError):
            local.check_or_raise("w-open")


def test_half_open_probe_lease_blocks_second_replica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "1")
    store, mock = _mock_store()
    with patch("kazma_core.config_store.get_config_store", return_value=mock):
        a = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        a.record_failure()
        a.persist_shared("w-probe")
        time.sleep(0.08)
        a.check_or_raise("w-probe")
        assert a._probe_in_flight is True
        b = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        with pytest.raises(CircuitBreakerOpenError):
            b.check_or_raise("w-probe")
        assert b._probe_in_flight is False
        a.record_success()
        assert a._probe_in_flight is False


# ── M-14 PG metrics ─────────────────────────────────────────────────────


def test_pg_metrics_sql_side_increment(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = TaskStore(db_path=str(tmp_path / "metrics.db"))
    store._pg = True
    calls: list[tuple[str, tuple]] = []

    class _Pool:
        def execute(self, sql, params=None):
            calls.append((sql, params))

    monkeypatch.setattr("kazma_core.db.pg_helpers.get_pool", lambda: _Pool())
    store.record_worker_metric(worker="w", tasks_completed=1, latency=0.2, tokens=10)
    assert len(calls) == 1
    sql, params = calls[0]
    assert "INSERT INTO kazma_swarm_worker_metrics" in sql
    assert "ON CONFLICT" in sql
    assert "EXCLUDED.tasks_completed" in sql
    assert "SELECT" not in sql.upper().split("INSERT")[0]
    assert params[0] == "w"
    store.close()


def test_sqlite_metrics_still_accumulate(tmp_path) -> None:
    store = TaskStore(db_path=str(tmp_path / "metrics.db"))
    store.record_worker_metric(worker="w", tasks_completed=1, latency=2.0, tokens=100)
    store.record_worker_metric(worker="w", tasks_completed=1, latency=4.0, tokens=50)
    rows = store.get_worker_metrics("w")
    assert len(rows) == 1
    assert rows[0]["tasks_completed"] == 2
    assert rows[0]["total_tokens"] == 150
    assert rows[0]["avg_latency"] == pytest.approx(3.0)
    store.close()


# ── M-14 MCP ────────────────────────────────────────────────────────────


def test_path_mode_uses_mutator_sot() -> None:
    from kazma_core.mcp.manager import _mcp_path_mode
    from kazma_core.safety.side_effects import _MUTATOR_TOKENS

    assert "save" in _MUTATOR_TOKENS
    assert "put" in _MUTATOR_TOKENS
    assert "apply" in _MUTATOR_TOKENS
    assert _mcp_path_mode("save_file") == "write"
    assert _mcp_path_mode("append_text") == "write"
    assert _mcp_path_mode("apply_patch") == "write"
    assert _mcp_path_mode("put_object") == "write"
    assert _mcp_path_mode("list_dir") == "read"
    assert _mcp_path_mode("read_file") == "read"


@pytest.mark.asyncio
async def test_read_resource_goes_through_scope_guard(monkeypatch, tmp_path) -> None:
    from kazma_core.mcp.manager import AsyncMCPManager, MCPServerHandle

    mgr = AsyncMCPManager()
    mgr._servers["fs"] = MCPServerHandle(name="fs", transport="stdio", connected=True)
    mgr._send = AsyncMock(return_value={"contents": [{"text": "x"}]})

    root_a = tmp_path / "repo-a"
    root_b = tmp_path / "repo-b"
    root_a.mkdir()
    root_b.mkdir()

    import kazma_core.ide.workspace_scope as ws_scope
    import kazma_core.workspace.binding as binding

    monkeypatch.setattr(ws_scope, "resolve_workspace_root", lambda: root_a.resolve())
    monkeypatch.setattr(binding, "get_bound_mcp_root", lambda: root_b.resolve())
    monkeypatch.delenv("KAZMA_MCP_SCOPE_GUARD", raising=False)

    out = await mgr.read_resource("fs", "file://notes.md")
    assert out["is_error"] is True
    assert "different workspace" in out["content"]

    listed = await mgr.list_resources("fs")
    assert listed == []
    mgr._send.assert_not_called()
