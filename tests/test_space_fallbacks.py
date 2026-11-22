"""Spaces S1-02: the tenant fallbacks outside memory, area by area.

Each fallback to ``"default"`` -- the owner's space -- either asks the scope
(``require_space_id``: the bound space; the owner's while Spaces is not
enforced; ``NoSpaceBound`` with nothing bound once it is), reads a stored
record's space (``space_of_record``: a record that names none was written
before Spaces and is the owner's, decision N19), or is declared work of the
install or the machine, with why. The count of those still written out is
the ratchet in ``tests/test_no_space_no_data.py``; this file holds what each
area now does, with the old fallback as the control.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import NoSpaceBound, bind_scope, space_of_record


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


def test_a_record_with_no_space_is_the_owners() -> None:
    for empty in (None, "", "  "):
        assert space_of_record(empty) == scope.OWNER_SPACE_ID
    assert space_of_record("family") == "family"
    assert space_of_record(" family ") == "family"


# ── agent, swarm, runtime ───────────────────────────────────────────────


def test_a_turn_s_state_is_made_in_the_bound_space() -> None:
    from kazma_core.agent.state import initial_supervisor_state

    assert initial_supervisor_state(max_iterations=10)["tenant_id"] == "default"  # one operator, as before
    with bind_scope("family", "layla"):
        assert initial_supervisor_state(max_iterations=10)["tenant_id"] == "family"
    assert initial_supervisor_state(max_iterations=10, tenant_id="acme")["tenant_id"] == "acme"


def test_a_turn_s_state_made_with_no_space_is_refused_once_enforced(enforced) -> None:
    from kazma_core.agent.state import initial_supervisor_state

    with pytest.raises(NoSpaceBound):
        initial_supervisor_state(max_iterations=10)
    with bind_scope("family", "layla"):
        assert initial_supervisor_state(max_iterations=10)["tenant_id"] == "family"


def test_control_the_old_default_made_it_the_owners(enforced) -> None:
    """``tenant_id: str = "default"`` as it was: unbound work, enforced, got
    the owner's space."""

    def old(tenant_id: str = "default") -> str:
        return tenant_id

    assert old() == scope.OWNER_SPACE_ID


def test_the_live_client_s_tenant_is_the_state_s_or_the_scope_s(enforced) -> None:
    from kazma_core.runtime.live_llm import _turn_tenant
    from kazma_core.tenant_context import get_current_tenant_id

    with _turn_tenant({"tenant_id": "family"}):
        assert get_current_tenant_id() == "family"
    with pytest.raises(NoSpaceBound), _turn_tenant({}):
        pass


def test_a_swarm_task_of_no_tenant_is_the_owners_before_spaces() -> None:
    from kazma_core.swarm.worker_dispatch import _worker_scope
    from kazma_core.tenant_context import get_current_tenant_id

    with _worker_scope("", None)():
        assert get_current_tenant_id() == scope.OWNER_SPACE_ID
    with _worker_scope("acme", None)():
        assert get_current_tenant_id() == "acme"


# ── safety: the commitment layer and the bus bridge ─────────────────────


def _pending(thread: str, space: str, n: int) -> None:
    from kazma_core.safety.commitment.store import Commitment, create_commitment

    for k in range(n):
        create_commitment(Commitment(thread_id=thread, act="remind", status="needs_confirm",
                                     goal_text=f"g{k}", tenant_id=space))


def test_a_commitment_made_with_no_tenant_is_in_the_bound_space(enforced) -> None:
    from kazma_core.safety.commitment.store import Commitment

    with bind_scope("family", "layla"):
        assert Commitment(thread_id="t").tenant_id == "family"
    with pytest.raises(NoSpaceBound):
        Commitment(thread_id="t")


def test_the_commitment_reads_name_the_bound_space(enforced) -> None:
    from kazma_core.safety.commitment.store import list_by_thread

    _pending("t-space-read", "family", 1)
    _pending("t-space-read", scope.OWNER_SPACE_ID, 1)
    with bind_scope("family", "layla"):
        assert {c.tenant_id for c in list_by_thread("t-space-read")} == {"family"}
    with pytest.raises(NoSpaceBound):
        list_by_thread("t-space-read")


def test_the_cap_sweep_caps_each_thread_in_its_own_space() -> None:
    """Kazma's own sweep: it capped every thread under the owner's space,
    so another space's thread was never capped."""
    from kazma_core.safety.commitment.store import enforce_all_pending_caps, list_by_thread

    _pending("t-space-cap", "family", 5)
    assert enforce_all_pending_caps(cap=2) >= 3
    with bind_scope("family", "layla"):
        left = [c for c in list_by_thread("t-space-cap") if c.status == "needs_confirm"]
    assert len(left) == 2


def test_control_the_sweep_of_before_left_another_space_uncapped(monkeypatch) -> None:
    from kazma_core.safety.commitment import store

    _pending("t-space-cap-old", "family", 5)
    real = store.enforce_pending_cap
    monkeypatch.setattr(store, "enforce_pending_cap",
                        lambda thread_id, *, cap=None, tenant_id=None: real(thread_id, cap=cap,
                                                                           tenant_id="default"))
    store.enforce_all_pending_caps(cap=2)
    with bind_scope("family", "layla"):
        left = [c for c in store.list_by_thread("t-space-cap-old") if c.status == "needs_confirm"]
    assert len(left) == 5


def test_an_effect_s_jobs_are_read_in_the_bound_space(monkeypatch) -> None:
    """The registry and a handoff name no tenant: the cancel resolver read
    the owner's scheduled jobs for anyone's call."""
    from kazma_core.safety.commitment import authorize

    read: list[str] = []

    def jobs(thread_id=None, tenant_id=None, statuses=()):
        read.append(tenant_id)
        return []

    import kazma_core.safety.commitment.constraints as constraints

    monkeypatch.setattr(constraints, "cron_pending_jobs", jobs)
    with bind_scope("family", "layla"):
        authorize.authorize_effect("cancel_scheduled", {"job_id": "cron-x"}, thread_id="t")
    assert read and set(read) == {"family"}


def test_a_bridge_approval_with_no_space_is_refused_once_enforced(enforced, monkeypatch) -> None:
    from kazma_core.safety import bus_bridge, hitl_gates

    registered: list[object] = []
    monkeypatch.setattr(bus_bridge, "bridge_enabled", lambda: True)
    monkeypatch.setattr(hitl_gates, "gate_registry_enabled", lambda: True)
    monkeypatch.setattr(bus_bridge, "live_watcher", lambda: object())
    monkeypatch.setattr(hitl_gates, "register_gate", lambda row: registered.append(row))
    asked = bus_bridge.request_approval_via_registry(tool_name="mcp__fs__write_file", tool_args="{}")
    assert asyncio.run(asked) is False
    assert registered == []


# ── cron, X and Scheduled ───────────────────────────────────────────────


async def test_a_booking_that_names_no_tenant_is_in_the_bound_space(tmp_path) -> None:
    """The Scheduled page names no tenant on an install that sees its whole
    schedule; it passed the owner's, so a job booked in another space was
    the owner's."""
    from kazma_core.cron.scheduler import CronScheduler
    from kazma_core.cron.store import SQLiteCronStore

    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    sched = CronScheduler(store=store, graph_builder=None)
    try:
        with bind_scope("family", "layla"):
            booked = await sched.schedule(timing="2h", prompt="water the plants", kind="reminder",
                                          tenant_id="", delivery_target="telegram:1")
            # control: the page's old fallback named the owner's space
            old = await sched.schedule(timing="2h", prompt="water the plants", kind="reminder",
                                       tenant_id="" or "default", delivery_target="telegram:1")
        assert (await store.get(booked["job_id"])).tenant_id == "family"
        assert (await store.get(old["job_id"])).tenant_id == scope.OWNER_SPACE_ID
    finally:
        await store.close()


def test_a_job_made_or_stored_with_no_tenant(enforced) -> None:
    from kazma_core.cron.store import ScheduledJob, SQLiteCronStore

    with bind_scope("family", "layla"):
        assert ScheduledJob(job_id="j", timing="2h", prompt="p", platform="telegram",
                            thread_id="t").tenant_id == "family"
    with pytest.raises(NoSpaceBound):
        ScheduledJob(job_id="j", timing="2h", prompt="p", platform="telegram", thread_id="t")
    row = {"job_id": "j", "timing": "2h", "prompt": "p", "platform": "telegram", "thread_id": "t",
           "next_run": None, "last_result": None, "tenant_id": None}
    # A row stored before Spaces is the owner's (N19), whatever is bound.
    with bind_scope("family", "layla"):
        assert SQLiteCronStore._row_to_job(None, row).tenant_id == scope.OWNER_SPACE_ID


def test_a_legacy_x_post_with_no_tenant(tmp_path, enforced) -> None:
    from kazma_core.x_api.schedule import XScheduledStore

    store = XScheduledStore(db_path=tmp_path / "x.db")
    with bind_scope("family", "layla"):
        mine = store.add(text="hello", fire_at=4_000_000_000.0)
    with pytest.raises(NoSpaceBound):
        store.add(text="hello", fire_at=4_000_000_000.0)
    with bind_scope("family", "layla"):
        assert store.get(mine).tenant_id == "family"
    unnamed = store.add(text="older", fire_at=4_000_000_000.0, tenant_id="family")
    import sqlite3

    conn = sqlite3.connect(tmp_path / "x.db")
    try:
        conn.execute("UPDATE x_scheduled_posts SET tenant_id = '' WHERE id = ?", (unnamed,))
        conn.commit()
    finally:
        conn.close()
    # A row stored before Spaces is the owner's (N19).
    [older] = [post for post in store.list_due(now=5_000_000_000.0) if post.id == unnamed]
    assert older.tenant_id == scope.OWNER_SPACE_ID
