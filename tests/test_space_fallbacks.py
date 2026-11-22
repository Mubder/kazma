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


# ── the web UI and the rest of core ─────────────────────────────────────


def test_a_request_s_approvals_are_its_space_s(monkeypatch) -> None:
    """The pending approvals of `kazma mcp` are listed by the request's
    space; with none bound once Spaces is enforced, none is matched."""
    from kazma_ui.routes_direct.misc import _current_tenant, _is_own_bridge_gate

    gate = {"mechanism": "bus_bridge", "tenant_id": "family"}
    older = {"mechanism": "bus_bridge", "tenant_id": ""}
    with bind_scope("family", "layla"):
        assert _current_tenant() == "family"
        assert _is_own_bridge_gate(gate) and not _is_own_bridge_gate(older)
    assert _current_tenant() == scope.OWNER_SPACE_ID  # one operator, as before
    assert _is_own_bridge_gate(older)  # a row from before Spaces is the owner's
    monkeypatch.setattr(scope, "_enforced", True)
    assert _current_tenant() == ""
    assert not _is_own_bridge_gate(gate) and not _is_own_bridge_gate(older)


def test_control_the_old_request_tenant_was_the_owners(enforced) -> None:
    from kazma_core.tenant_context import get_current_tenant_id

    old = (get_current_tenant_id() or "default").strip() or "default"
    assert old == scope.OWNER_SPACE_ID


def test_a_tenant_key_named_empty_is_the_bound_space_s() -> None:
    from kazma_core.tenant_isolation import tenant_key

    with bind_scope("family", "layla"):
        assert tenant_key("x.y", tenant_id="") == "tenant.family.x.y"
        assert tenant_key("x.y", tenant_id=" ") == "tenant.family.x.y"
    assert tenant_key("x.y", tenant_id="") == "x.y"


# ── memory: the helper, the mirror and the backends ─────────────────────


def test_a_parameter_left_out_is_today_s_value_until_spaces_is_enforced(monkeypatch) -> None:
    """``space_named_or_bound``: the named space; with none, the owner's while
    Spaces is off -- whatever tenant a request bound (an older multi-user
    install) and inside Kazma's own work -- and the bound space once it is on."""
    from kazma_core.spaces.scope import install_work, space_named_or_bound
    from kazma_core.tenant_context import tenant_scope

    assert space_named_or_bound("acme") == "acme"
    assert space_named_or_bound(None) == space_named_or_bound("") == scope.OWNER_SPACE_ID
    with tenant_scope("acme"):
        assert space_named_or_bound(None) == scope.OWNER_SPACE_ID
    with install_work("a sweep"):
        assert space_named_or_bound(None) == scope.OWNER_SPACE_ID
    monkeypatch.setattr(scope, "_enforced", True)
    with bind_scope("family", "layla"):
        assert space_named_or_bound(None) == "family"
    with pytest.raises(NoSpaceBound):
        space_named_or_bound(None)


def test_control_require_space_id_would_move_rows_while_spaces_is_off() -> None:
    from kazma_core.spaces.scope import install_work, require_space_id
    from kazma_core.tenant_context import tenant_scope

    with tenant_scope("acme"):
        assert require_space_id() == "acme"  # not the owner's, as the old default was
    with install_work("a sweep"), pytest.raises(NoSpaceBound):
        require_space_id()


def test_an_episode_row_names_its_space() -> None:
    from kazma_core.memory.dual_write import episode_row

    def row(**kw):
        return episode_row(session_id="s", turn_number=1, user_text="hi", **kw)["tenant_id"]

    assert row() == scope.OWNER_SPACE_ID  # Spaces off: as before
    with bind_scope("family", "layla"):
        assert row() == scope.OWNER_SPACE_ID  # still off: nothing moves
        assert row(tenant_id="family") == "family"


def test_an_episode_row_with_no_space_is_refused_once_enforced(enforced) -> None:
    from kazma_core.memory.dual_write import episode_row

    with bind_scope("family", "layla"):
        assert episode_row(session_id="s", turn_number=1, user_text="hi")["tenant_id"] == "family"
    with pytest.raises(NoSpaceBound):
        episode_row(session_id="s", turn_number=1, user_text="hi")


def test_a_mirror_row_naming_no_space_is_the_owners() -> None:
    from kazma_core.memory.state_backend import _episode_params

    assert _episode_params({"id": "e1", "tenant_id": ""})[1] == scope.OWNER_SPACE_ID
    assert _episode_params({"id": "e1", "tenant_id": "family"})[1] == "family"


def test_the_mirror_searches_name_their_tenant() -> None:
    """Every caller names it (recall); a caller that names none does not run."""
    import inspect

    from kazma_core.memory import graph_backend, state_backend

    for fn in (state_backend.search_state_episodes, state_backend.search_state_beliefs,
               state_backend.backfill_state_mirror, graph_backend.clear_tenant_edges):
        assert inspect.signature(fn).parameters["tenant_id"].default is inspect.Parameter.empty, fn


# ── memory: forget ──────────────────────────────────────────────────────


def _journal_tenant(seq: int) -> str:
    from kazma_core.memory import forget_journal

    conn = forget_journal._connect()
    try:
        return conn.execute("SELECT tenant_id FROM forget_journal WHERE seq = ?", (seq,)).fetchone()[0]
    finally:
        conn.close()


def test_a_forget_entry_naming_no_tenant_is_today_s_until_enforced(monkeypatch) -> None:
    from kazma_core.memory import forget_journal

    with bind_scope("family", "layla"):
        seq = forget_journal.append("turn", tenant_id=None, session_key="s-1", turn_number=1)
    assert _journal_tenant(seq) == scope.OWNER_SPACE_ID  # Spaces off: nothing moves
    monkeypatch.setattr(scope, "_enforced", True)
    with bind_scope("family", "layla"):
        seq = forget_journal.append("turn", tenant_id=None, session_key="s-2", turn_number=1)
    assert _journal_tenant(seq) == "family"
    with pytest.raises(NoSpaceBound):
        forget_journal.append("turn", tenant_id=None, session_key="s-3", turn_number=1)


def test_a_forget_by_the_memory_tool_with_no_space_is_refused_once_enforced(enforced) -> None:
    """The memory tool hands forget_mentions and keep_turn_out the request's
    tenant, which is None with nothing bound: they acted as the owner's."""
    from kazma_core.memory.forget import forget_mentions, keep_turn_out

    with pytest.raises(NoSpaceBound):
        forget_mentions("a codeword-123", tenant_id=None)
    with pytest.raises(NoSpaceBound):
        keep_turn_out("s-4", 1, "what is the codeword?", tenant_id=None)
