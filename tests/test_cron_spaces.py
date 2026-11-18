"""Spaces S1-14: a scheduled job carries its space and the account that
booked it, and fires as them.

Guards act only where Spaces is enforced; an install with one account, and
the older multi-user mode (which keeps its refusal), behave as before.
Enforced:

* every Scheduled read and change keeps to the caller's space (another
  space's job id reads as unknown);
* a fire runs as a whole scope: the job's space and its creator; a job
  whose tenant is no space of the install does not fire;
* every address Kazma can deliver to before people link their own chat
  apps (S3-05) is the owner's, so a booking by anyone else is refused, and
  a job that would act for anyone else does not fire: their words never
  reach the owner's chat.

Each guard is taken out as a control, and the control shows what it stops.
The route and tool cases are in ``tests/_scheduled_space_cases.py``.
"""

from __future__ import annotations

import importlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kazma_core.cron import scheduler as sm
from kazma_core.cron.store import JobStatus, ScheduledJob, SQLiteCronStore
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._scheduled_space_cases import ITEM, OWNER_CHAT
from tests._space_matrix import Matrix, load_cases

CASES = load_cases()
SHARED = "cron-shared"

ROUTES = (
    "DELETE /api/scheduled/cron/{job_id}", "GET /api/scheduled/cron/{job_id}", "GET /api/scheduled/recipes",
    "GET /api/scheduled/tasks", "GET /scheduled", "POST /api/scheduled/cron",
    "POST /api/scheduled/cron/{job_id}/restore", "POST /api/scheduled/recipes/{recipe_id}",
    "PUT /api/scheduled/cron/{job_id}",
)
TOOLS = ("tool:cancel_scheduled", "tool:edit_scheduled", "tool:list_scheduled", "tool:schedule_task")


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, str]]:
    """What reached a chat: (target, text)."""
    messages = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")
    out: list[tuple[str, str]] = []

    async def backend(target_id, text, **_kw):
        out.append((target_id, text))
        return f"sent:{target_id}"

    monkeypatch.setitem(messages._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", lambda *_a, **_k: True)
    return out


class _ScopeGraph:
    """A turn that answers, and records the scope and tenant it ran in."""

    def __init__(self) -> None:
        self.ran_in: list[tuple[Any, str]] = []

    async def ainvoke(self, state, config=None):
        from kazma_core.tenant_context import get_current_tenant_id

        self.ran_in.append((scope.current_scope(), get_current_tenant_id() or ""))
        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": "Done: the plants are watered."})
        return {"messages": messages}


async def _store(tmp_path) -> SQLiteCronStore:
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    return store


async def _plant(store, *, tenant: str, creator: str, kind: str = "reminder", prompt: str = "water the plants",
                 thread: str = "thread-x", target: str = OWNER_CHAT,
                 due_ago: timedelta = timedelta(seconds=5)) -> ScheduledJob:
    job = ScheduledJob(
        job_id=f"cron-{secrets.token_hex(6)}", timing="2h",
        prompt=prompt, platform="telegram", thread_id=thread, kind=kind,
        next_run=(datetime.now(UTC) - due_ago).isoformat(),
        tenant_id=tenant, delivery_target=target, creator=creator,
    )
    await store.insert(job)
    return job


async def _fire(sched, store, job_id: str) -> ScheduledJob:
    job = await store.get(job_id)
    assert job is not None
    await sched._execute(job)
    after = await store.get(job_id)
    assert after is not None
    return after


def _fails(target: str, matrix: Matrix) -> None:
    """The case of *target* fails on one of its own assertions."""
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        CASES[target].run(matrix)


def test_the_item_wrote_a_case_for_every_scheduled_route_and_tool() -> None:
    mine = sorted(target for target, registered in CASES.items() if registered.item == ITEM)
    assert mine == sorted([*ROUTES, *TOOLS])


# ── a fire runs in its space ─────────────────────────────────────────────


async def test_a_real_fire_runs_in_its_space(tmp_path, matrix, enforced, sent) -> None:
    identity.create_space("Shared", created_by=OWNER_ACCOUNT_ID, space_id=SHARED)
    store = await _store(tmp_path)
    graph = _ScopeGraph()
    sched = sm.CronScheduler(store=store, graph_builder=lambda: graph)
    try:
        with scope.bind_scope(SHARED, OWNER_ACCOUNT_ID):
            booked = await sched.schedule(timing="2h", prompt="water the plants", kind="task",
                                          delivery_target=OWNER_CHAT, platform="telegram")
        job = await store.get(booked["job_id"])
        assert job is not None and job.tenant_id == SHARED and job.creator == OWNER_ACCOUNT_ID
        done = await _fire(sched, store, job.job_id)
        assert done.last_outcome == "delivered", done.last_result
        [(ran, tenant)] = graph.ran_in
        assert (ran.space_id, ran.asker, tenant) == (SHARED, OWNER_ACCOUNT_ID, SHARED)
        from kazma_core.spaces.threads import thread_home

        assert thread_home(f"cron-{job.job_id}") == SHARED
        assert [t for t, _ in sent] == [OWNER_CHAT]
    finally:
        await store.close()


async def test_control_the_fire_with_no_scope_never_runs_in_its_space(tmp_path, matrix, enforced, sent,
                                                                        monkeypatch) -> None:
    identity.create_space("Shared", created_by=OWNER_ACCOUNT_ID, space_id=SHARED)
    store = await _store(tmp_path)
    graph = _ScopeGraph()
    sched = sm.CronScheduler(store=store, graph_builder=lambda: graph)
    try:
        job = await _plant(store, tenant=SHARED, creator=OWNER_ACCOUNT_ID, kind="task", thread="t-control")
        monkeypatch.setattr(sm, "_fire_binding", lambda _job: None)  # the fire as it was: a tenant alone
        done = await _fire(sched, store, job.job_id)
        assert graph.ran_in == [] and done.last_outcome != "delivering"
        assert sent == []
    finally:
        await store.close()


# ── another space's job ──────────────────────────────────────────────────


def _no_tenant_rule() -> tuple[bool, str | None]:
    """``scheduler._tenant_scope`` taken out: the install's whole schedule,
    as a single-operator install reads it."""
    return False, None


async def test_another_spaces_job_reads_as_unknown(tmp_path, matrix, enforced) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        theirs = await _plant(store, tenant=Matrix.SPACE_B, creator=matrix.account_b, prompt="their secret")
        with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
            tenant = sm.scheduled_tenant_filter()
            assert tenant == Matrix.SPACE_A
            assert await sched.get_job(theirs.job_id, tenant_id=tenant) is None
            listed = await sched.list_jobs(tenant_id=tenant, scoped=True)
            assert theirs.job_id not in str(listed)
            assert (await sched.cancel(theirs.job_id, tenant_id=tenant))["status"] == "not_found"
        assert (await store.get(theirs.job_id)).status == JobStatus.PENDING  # type: ignore[union-attr]
    finally:
        await store.close()


async def test_control_without_the_tenant_rule_another_spaces_job_is_read(tmp_path, matrix, enforced,
                                                                     monkeypatch) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        theirs = await _plant(store, tenant=Matrix.SPACE_B, creator=matrix.account_b, prompt="their secret")
        monkeypatch.setattr(sm, "_tenant_scope", _no_tenant_rule)
        with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
            tenant = sm.scheduled_tenant_filter()
            assert tenant is None
            assert (await sched.get_job(theirs.job_id, tenant_id=tenant))["prompt"] == "their secret"
    finally:
        await store.close()


def test_the_cases_fail_without_the_tenant_rule(matrix, monkeypatch) -> None:
    monkeypatch.setattr(sm, "_tenant_scope", _no_tenant_rule)
    for target in ("GET /api/scheduled/tasks", "GET /api/scheduled/cron/{job_id}",
                   "PUT /api/scheduled/cron/{job_id}", "tool:list_scheduled", "tool:edit_scheduled"):
        _fails(target, matrix)


# ── a job with no space ──────────────────────────────────────────────────


async def test_a_job_with_no_space_does_not_fire(tmp_path, matrix, enforced, sent) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant="tenant-of-nobody", creator="", prompt="a reminder of nobody's")
        done = await _fire(sched, store, job.job_id)
        assert done.last_outcome == "no_space" and done.status == JobStatus.FAILED
        assert sent == []
    finally:
        await store.close()


async def test_control_a_job_with_no_space_fires_without_the_rule(tmp_path, matrix, enforced, sent,
                                                                   monkeypatch) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant="tenant-of-nobody", creator="", prompt="a reminder of nobody's")
        monkeypatch.setattr(sm, "_fire_binding", lambda _job: None)
        await _fire(sched, store, job.job_id)
        assert [t for t, _ in sent] == [OWNER_CHAT]
    finally:
        await store.close()


# ── only the owner's own jobs use the owner's address ────────────────────


async def test_a_booking_by_anyone_but_the_owner_is_refused(tmp_path, matrix, enforced) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        with scope.bind_scope(Matrix.SPACE_A, matrix.account_a), pytest.raises(sm.CronLimitError) as refused:
            await sched.schedule(timing="2h", prompt="water my plants", kind="reminder",
                                 delivery_target=OWNER_CHAT)
        assert refused.value.code == "no_own_address"
        assert await store.count_jobs() == 0
        # The owner books in their own space as before.
        with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
            booked = await sched.schedule(timing="2h", prompt="water the plants", kind="reminder",
                                          delivery_target=OWNER_CHAT)
        job = await store.get(booked["job_id"])
        assert job is not None and job.creator == OWNER_ACCOUNT_ID and job.tenant_id == OWNER_SPACE_ID
    finally:
        await store.close()


async def test_control_without_the_rule_their_words_reach_the_owners_chat(tmp_path, matrix, enforced, sent,
                                                                            monkeypatch) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        monkeypatch.setattr(sm, "_owner_account", lambda: matrix.account_a)  # the rule's check, out
        with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
            booked = await sched.schedule(timing="2h", prompt="person A's private note", kind="reminder",
                                          delivery_target=OWNER_CHAT)
        job = await store.get(booked["job_id"])
        assert job is not None
        await _fire(sched, store, job.job_id)
        assert any(target == OWNER_CHAT and "person A's private note" in text for target, text in sent)
    finally:
        await store.close()


async def test_a_job_acting_for_someone_else_does_not_fire(tmp_path, matrix, enforced, sent) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        # As an older install's might be: in A's space, booked by A.
        job = await _plant(store, tenant=Matrix.SPACE_A, creator=matrix.account_a, prompt="person A's note")
        done = await _fire(sched, store, job.job_id)
        assert done.last_outcome == "no_own_address" and sent == []
        assert "person A's note" not in (done.last_result or "")
    finally:
        await store.close()


async def test_control_a_job_acting_for_someone_else_fires_without_the_rule(tmp_path, matrix, enforced, sent,
                                                                             monkeypatch) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=Matrix.SPACE_A, creator=matrix.account_a, prompt="person A's note")
        monkeypatch.setattr(sm, "_owner_account", lambda: matrix.account_a)
        await _fire(sched, store, job.job_id)
        assert any(target == OWNER_CHAT and "person A's note" in text for target, text in sent)
    finally:
        await store.close()


def test_the_booking_cases_fail_without_the_rule(matrix, monkeypatch) -> None:
    monkeypatch.setattr(sm, "_owner_account", lambda: matrix.account_a)
    for target in ("POST /api/scheduled/cron", "tool:schedule_task"):
        _fails(target, matrix)


# ── a member of a shared space and the owner's job (S1-14 review) ────────


async def _owners_job_in_shared(sched, store, matrix) -> ScheduledJob:
    """A reminder the owner booked in a space person A is also a member of:
    it fires as the owner, into the owner's chat."""
    identity.create_space("Shared", created_by=OWNER_ACCOUNT_ID, space_id=SHARED)
    identity.add_member(SHARED, matrix.account_a)
    with scope.bind_scope(SHARED, OWNER_ACCOUNT_ID):
        booked = await sched.schedule(timing="2h", prompt="the owner's own note", kind="reminder",
                                      delivery_target=OWNER_CHAT, platform="telegram")
    job = await store.get(booked["job_id"])
    assert job is not None and job.creator == OWNER_ACCOUNT_ID
    return job


async def _nobody_refused(self, job_id: str, tenant_id: str | None, **_change: Any) -> str:
    """The rule taken out: every change is the caller's to make."""
    return ""


async def _prompt_only_rule(self, job_id: str, tenant_id: str | None, *, prompt: bool = True,
                            recurring: bool = False) -> str:
    """The first rule of the S1-14 review: only a new prompt (and Run again)
    was refused to someone else; any new time passed."""
    return await _FIRST_RULE(self, job_id, tenant_id) if prompt else ""


async def _FIRST_RULE(self, job_id: str, tenant_id: str | None) -> str:  # noqa: N802
    from kazma_core.spaces.scope import bound_asker

    job = await self._store.get(job_id, tenant_id=tenant_id)
    if job is None:
        return ""
    binding = sm._fire_binding(job)
    acts_for = binding.asker if binding is not None else ""
    caller = bound_asker() or ""
    return "" if caller and caller == acts_for else "not_yours"


async def test_a_member_cannot_make_the_owners_job_run_again_by_its_time(tmp_path, matrix, enforced,
                                                                        sent) -> None:
    """Someone else may only move a pending one-off to another one-off time:
    a recurrence would fire it as the owner every day, and a time recorded
    while it fires puts the finished one-off back."""
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)
        with scope.bind_scope(SHARED, matrix.account_a):
            daily = await sched.reschedule(job.job_id, timing="every day at 09:00", tenant_id=SHARED)
        assert daily["status"] == "not_yours"
        assert (await store.get(job.job_id)).series_start == ""
        assert await store.claim_job(job.job_id, owner="another-runtime")  # it is firing now
        with scope.bind_scope(SHARED, matrix.account_a):
            during = await sched.reschedule(job.job_id, timing="3h", tenant_id=SHARED)
        assert during["status"] == "not_yours"
        assert not (await store.get(job.job_id)).pending_edit
    finally:
        await store.close()


async def test_control_the_prompt_only_rule_lets_a_time_make_it_recur(tmp_path, matrix, enforced,
                                                                      monkeypatch) -> None:
    monkeypatch.setattr(sm.CronScheduler, "_not_theirs_to_change", _prompt_only_rule)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)
        with scope.bind_scope(SHARED, matrix.account_a):
            daily = await sched.reschedule(job.job_id, timing="every day at 09:00", tenant_id=SHARED)
        assert daily["status"] == "rescheduled"
        assert (await store.get(job.job_id)).series_start, "the owner's one-off now fires every day"
    finally:
        await store.close()


async def test_a_member_cannot_put_words_into_a_job_that_fires_as_the_owner(tmp_path, matrix, enforced,
                                                                            sent) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)
        with scope.bind_scope(SHARED, matrix.account_a):
            reworded = await sched.reschedule(job.job_id, prompt="send the owner my words", tenant_id=SHARED)
            moved = await sched.reschedule(job.job_id, timing="3h", tenant_id=SHARED)
        assert reworded["status"] == "not_yours"
        assert moved["status"] == "rescheduled", "a new time is not a new message"
        with scope.bind_scope(SHARED, OWNER_ACCOUNT_ID):
            own = await sched.reschedule(job.job_id, prompt="the owner's new note", tenant_id=SHARED)
        assert own["status"] == "rescheduled"
        await _fire(sched, store, job.job_id)
        assert not any("my words" in text for _target, text in sent)
        assert any(target == OWNER_CHAT and "the owner's new note" in text for target, text in sent)
    finally:
        await store.close()


async def test_control_without_the_rule_a_members_words_reach_the_owners_chat(tmp_path, matrix, enforced,
                                                                               sent, monkeypatch) -> None:
    monkeypatch.setattr(sm.CronScheduler, "_not_theirs_to_change", _nobody_refused)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)
        with scope.bind_scope(SHARED, matrix.account_a):
            reworded = await sched.reschedule(job.job_id, prompt="send the owner my words", tenant_id=SHARED)
        assert reworded["status"] == "rescheduled"
        await _fire(sched, store, job.job_id)
        assert any(target == OWNER_CHAT and "my words" in text for target, text in sent)
    finally:
        await store.close()


async def test_a_member_cannot_run_the_owners_finished_job_again(tmp_path, matrix, enforced) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)
        with scope.bind_scope(SHARED, OWNER_ACCOUNT_ID):
            assert (await sched.cancel(job.job_id, tenant_id=SHARED))["status"] == "cancelled"
        with scope.bind_scope(SHARED, matrix.account_a):
            refused = await sched.restore(job.job_id, timing="3h", tenant_id=SHARED)
        assert refused["status"] == "not_yours"
        assert (await store.get(job.job_id)).status == JobStatus.CANCELLED
        with scope.bind_scope(SHARED, OWNER_ACCOUNT_ID):
            again = await sched.restore(job.job_id, timing="3h", tenant_id=SHARED)
        assert again["status"] == "restored"
    finally:
        await store.close()


async def test_control_without_the_rule_a_member_runs_the_owners_job_again(tmp_path, matrix, enforced,
                                                                           monkeypatch) -> None:
    monkeypatch.setattr(sm.CronScheduler, "_not_theirs_to_change", _nobody_refused)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)
        with scope.bind_scope(SHARED, OWNER_ACCOUNT_ID):
            await sched.cancel(job.job_id, tenant_id=SHARED)
        with scope.bind_scope(SHARED, matrix.account_a):
            again = await sched.restore(job.job_id, timing="3h", tenant_id=SHARED)
        assert again["status"] == "restored"
    finally:
        await store.close()


@pytest.mark.parametrize("target", [
    "PUT /api/scheduled/cron/{job_id}", "POST /api/scheduled/cron/{job_id}/restore", "tool:edit_scheduled",
])
def test_the_shared_space_cases_fail_without_the_rule(target, matrix, monkeypatch) -> None:
    """The matrix's member-of-a-shared-space checks see the rule taken out."""
    monkeypatch.setattr(sm.CronScheduler, "_not_theirs_to_change", _nobody_refused)
    _fails(target, matrix)


def test_the_shared_space_edit_case_fails_with_the_prompt_only_rule(matrix, monkeypatch) -> None:
    monkeypatch.setattr(sm.CronScheduler, "_not_theirs_to_change", _prompt_only_rule)
    _fails("PUT /api/scheduled/cron/{job_id}", matrix)


def test_not_enforced_anyone_may_reword_a_job_as_before(tmp_path, matrix) -> None:
    """One account, or Spaces not enforced: the rule asks nothing."""
    import asyncio

    async def run() -> str:
        store = await _store(tmp_path)
        sched = sm.CronScheduler(store=store, graph_builder=None)
        try:
            job = await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID)
            with scope.bind_scope(OWNER_SPACE_ID, matrix.account_a):
                return (await sched.reschedule(job.job_id, prompt="new words"))["status"]
        finally:
            await store.close()

    assert asyncio.run(run()) == "rescheduled"


# ── a fire whose binding cannot be read (S1-14 review) ───────────────────


def _identity_unreadable(_job: ScheduledJob) -> Any:
    """Not an outage: a registry that cannot be read as one (no cause)."""
    raise identity.IdentityUnavailable("kazma.identity holds str, not a mapping")


def _database_away(_job: ScheduledJob) -> Any:
    """An outage: the identity store's read could not reach the database."""
    from kazma_core.db.postgres_pool import PostgresUnreachable

    raise identity.IdentityUnavailable("the identity store could not be read: PostgresUnreachable") \
        from PostgresUnreachable(3, "connection refused")


@pytest.fixture
def watch(monkeypatch):
    """The real availability watch, with no prober thread signing in."""
    from kazma_core.db import availability

    availability.reset()
    monkeypatch.setattr(availability, "_spawn_prober", lambda *_a, **_k: None)
    yield availability
    availability.reset()


async def test_a_fire_held_by_a_database_outage_waits_and_resets(tmp_path, matrix, enforced, sent, watch,
                                                                 monkeypatch) -> None:
    from kazma_core.tenant_context import get_current_tenant_id

    monkeypatch.setattr(sm, "_fire_binding", _database_away)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await store.get((await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID)).job_id)
        outside = get_current_tenant_id()
        after = await _fire(sched, store, job.job_id)
        assert after.next_run == job.next_run, "at the same occurrence"
        assert after.status == JobStatus.PENDING, "the job waits"
        assert after.last_outcome != "failed" and sent == []
        assert get_current_tenant_id() == outside and scope.current_scope() is None
        assert watch.is_away(), "the next passes hold before they claim"
        assert job.job_id not in sched._in_flight
    finally:
        await store.close()


async def test_a_binding_failure_that_is_no_outage_fails_the_fire_once(tmp_path, matrix, enforced, sent, watch,
                                                                        monkeypatch) -> None:
    """A registry that cannot be read as one is no outage: the fire fails
    (counted, alerted), never claimed and released again every pass."""
    monkeypatch.setattr(sm, "_fire_binding", _identity_unreadable)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID)
        after = await _fire(sched, store, job.job_id)
        assert after.last_outcome == "identity_unavailable", after.last_outcome
        assert after.status != JobStatus.RUNNING and after.failure_count == 1
        assert sent == [] and not watch.is_away()
    finally:
        await store.close()


async def test_control_holding_every_binding_failure_loops(tmp_path, matrix, enforced, watch, monkeypatch) -> None:
    """Negative control: a hold for any failure gives the claim back with
    nothing failed, so a store that never reads again is claimed and
    released every pass, and the job never ends."""
    async def always_hold(self, job, exc):
        await self._store.release_fire(job.job_id, owner=self._owner, reason="identity_unavailable")
        return True

    monkeypatch.setattr(sm, "_fire_binding", _identity_unreadable)
    monkeypatch.setattr(sm.CronScheduler, "_hold_for_identity", always_hold)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID)
        for _ in range(3):
            after = await _fire(sched, store, job.job_id)
        assert after.status == JobStatus.PENDING and after.failure_count == 0
    finally:
        await store.close()


async def test_a_missed_job_whose_binding_is_no_outage_is_recorded_without_a_notice(
        tmp_path, matrix, enforced, sent, watch, monkeypatch) -> None:
    monkeypatch.setattr(sm, "_fire_binding", _identity_unreadable)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID, due_ago=timedelta(hours=3))
        await sched._record_missed(await store.get(job.job_id), datetime.now(UTC))
        assert (await store.get(job.job_id)).status == JobStatus.MISSED
        assert await store.deliveries_for(job.job_id, limit=10) == [] and sent == []
    finally:
        await store.close()


async def test_control_without_the_hold_the_fire_stays_claimed(tmp_path, matrix, enforced, monkeypatch) -> None:
    """Negative control: with the binding's failure not caught (as when it ran
    outside the ``try``), the error leaves ``_execute`` and the claim stays
    RUNNING until recovery, every pass."""
    async def no_hold(self, job, exc):
        raise exc

    monkeypatch.setattr(sm, "_fire_binding", _database_away)
    monkeypatch.setattr(sm.CronScheduler, "_hold_for_identity", no_hold)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID)
        with pytest.raises(identity.IdentityUnavailable):
            await sched._execute(await store.get(job.job_id))
        assert (await store.get(job.job_id)).status == JobStatus.RUNNING
    finally:
        await store.close()


async def _poll(sched) -> None:
    import asyncio

    sched._running = True
    await sched._poll_once()
    if sched._exec_tasks:
        await asyncio.gather(*list(sched._exec_tasks))


async def test_while_the_database_is_away_a_reminder_waits_once_enforced(tmp_path, matrix, sent,
                                                                         monkeypatch) -> None:
    """Who a reminder fires as is read from the identity store, in the
    database: once Spaces is enforced it waits with the tasks. Not enforced,
    a reminder still rings with the database away, as before."""
    from kazma_core.db import availability

    monkeypatch.setattr(availability, "is_away", lambda: True)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID, prompt="the held one")
        monkeypatch.setattr(scope, "_enforced", True)
        await _poll(sched)
        assert (await store.get(job.job_id)).status == JobStatus.PENDING and sent == []
        monkeypatch.setattr(scope, "_enforced", False)
        await _poll(sched)
        assert any("the held one" in text for _target, text in sent)
    finally:
        await store.close()


# ── a missed job and the fire rule (S1-14 review) ────────────────────────


async def test_a_missed_job_that_would_not_fire_queues_no_notice(tmp_path, matrix, enforced, sent) -> None:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=Matrix.SPACE_A, creator=matrix.account_a, prompt="person A's note",
                           due_ago=timedelta(hours=3))
        await sched._record_missed(await store.get(job.job_id), datetime.now(UTC))
        after = await store.get(job.job_id)
        assert after.status == JobStatus.MISSED
        assert await store.deliveries_for(job.job_id, limit=10) == []
        assert sent == []
    finally:
        await store.close()


async def test_control_without_the_rule_a_missed_notice_carries_their_words(tmp_path, matrix, enforced, sent,
                                                                            monkeypatch) -> None:
    monkeypatch.setattr(sm, "_fire_binding", lambda _job: None)  # the fire rule taken out
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=Matrix.SPACE_A, creator=matrix.account_a, prompt="person A's note",
                           due_ago=timedelta(hours=3))
        await sched._record_missed(await store.get(job.job_id), datetime.now(UTC))
        assert any(target == OWNER_CHAT and "person A's note" in text for target, text in sent)
    finally:
        await store.close()


# ── a sibling's address ──────────────────────────────────────────────────


async def _sibling_target_as_it_was(self, thread_id: str, tenant_id: str | None = None) -> str:
    """``SQLiteCronStore.sibling_delivery_target`` as it was before S1-14
    (main's body, unchanged): by thread alone. *tenant_id* is taken because
    today's caller passes it, and ignored, as the old lookup knew no space."""
    if self._db is None or not str(thread_id or "").strip():
        return ""
    try:
        rows = await self._conn().execute_fetchall(
            "SELECT delivery_target FROM cron_jobs "
            "WHERE thread_id = ? AND delivery_target LIKE '%:%' "
            "ORDER BY created_at DESC LIMIT 1",
            (str(thread_id),),
        )
    except Exception:  # noqa: BLE001 - as the old body: a failed lookup finds nothing
        return ""
    for row in rows or []:
        cand = str(row["delivery_target"] or "").strip()
        if ":" in cand:
            return cand
    return ""


async def _target_of_a_job_with_none(tmp_path) -> str:
    """The address the scheduler's own repair chain finds for a job of
    space A with no address, beside space B's job on the same thread."""
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        await _plant(store, tenant=Matrix.SPACE_B, creator="b", thread="one-thread", target="telegram:b-chat",
                     prompt="b's")
        job = await _plant(store, tenant=Matrix.SPACE_A, creator="a", thread="one-thread", target="",
                           prompt="a's")
        return await sched._resolve_target(job)
    finally:
        await store.close()


async def test_a_job_never_takes_another_spaces_address(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        await _plant(store, tenant=Matrix.SPACE_B, creator="b", thread="one-thread", target="telegram:b-chat",
                     prompt="b's")
        assert await store.sibling_delivery_target("one-thread", Matrix.SPACE_A) == ""
        assert await store.sibling_delivery_target("one-thread", Matrix.SPACE_B) == "telegram:b-chat"
    finally:
        await store.close()
    (tmp_path / "chain").mkdir()
    assert await _target_of_a_job_with_none(tmp_path / "chain") != "telegram:b-chat"


async def test_control_the_old_lookup_hands_a_job_another_spaces_address(tmp_path, monkeypatch) -> None:
    """Negative control: the scheduler's repair chain with the lookup as it
    was (by thread alone) gives space A's job space B's address."""
    monkeypatch.setattr(SQLiteCronStore, "sibling_delivery_target", _sibling_target_as_it_was)
    assert await _target_of_a_job_with_none(tmp_path) == "telegram:b-chat"


# ── work bound to no space, and the older modes ──────────────────────────


def test_work_bound_to_no_space_is_refused(matrix, enforced, monkeypatch) -> None:
    from kazma_core.cron.single_operator import NO_SPACE_REFUSAL, scheduled_refusal

    asked: list[str] = []
    monkeypatch.setattr("kazma_core.security.single_operator.single_operator_refusal",
                        lambda refusal: asked.append(refusal) or refusal)
    assert scheduled_refusal() == NO_SPACE_REFUSAL
    with scope.bind_scope(Matrix.SPACE_A, matrix.account_a):
        assert scheduled_refusal() is None
    assert asked == []  # the older mode's rule is not asked once Spaces is enforced


def test_the_older_multi_user_mode_keeps_its_refusal(monkeypatch) -> None:
    from kazma_core.cron.single_operator import MULTI_USER_REFUSAL, scheduled_refusal

    monkeypatch.setattr("kazma_core.security.single_operator.single_operator_refusal", lambda refusal: refusal)
    assert scheduled_refusal() == MULTI_USER_REFUSAL


# ── one account: exactly as before ───────────────────────────────────────


async def test_one_account_books_and_fires_as_before(tmp_path, sent) -> None:
    assert not scope.spaces_enforced()
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        booked = await sched.schedule(timing="2h", prompt="water the plants", kind="reminder",
                                      delivery_target=OWNER_CHAT)
        job = await store.get(booked["job_id"])
        assert job is not None and job.tenant_id == "default" and job.creator == ""
        assert sm._fire_binding(job) is None
        assert sm.scheduled_tenant_filter() is None
        # A person bound with Spaces not enforced: stamped, never refused.
        with scope.bind_scope(OWNER_SPACE_ID, "someone"):
            stamped = await sched.schedule(timing="2h", prompt="feed the cat", kind="reminder",
                                           delivery_target=OWNER_CHAT)
        assert (await store.get(stamped["job_id"])).creator == "someone"  # type: ignore[union-attr]
        planted = await _plant(store, tenant="default", creator="", prompt="call the dentist")
        await _fire(sched, store, planted.job_id)
        assert [t for t, _ in sent] == [OWNER_CHAT]
    finally:
        await store.close()


# ── the S1-16 follow-up: a locked settings file holds the fire ──────────


def _settings_locked(_job: ScheduledJob) -> Any:
    """The identity store on a SQLite settings file another writer holds."""
    import sqlite3

    raise identity.IdentityUnavailable("the identity store could not be read: OperationalError") \
        from sqlite3.OperationalError("database is locked")


async def test_a_fire_held_by_a_locked_settings_file_waits(tmp_path, matrix, enforced, sent, watch,
                                                           monkeypatch) -> None:
    monkeypatch.setattr(sm, "_fire_binding", _settings_locked)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await store.get((await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID)).job_id)
        after = await _fire(sched, store, job.job_id)
        assert after.status == JobStatus.PENDING and after.next_run == job.next_run
        assert after.failure_count == 0 and after.last_outcome != "identity_unavailable"
        assert sent == [] and not watch.is_away(), "a SQLite lock is no Postgres outage"
    finally:
        await store.close()


async def test_control_a_locked_settings_file_failed_the_fire(tmp_path, matrix, enforced, sent, watch,
                                                              monkeypatch) -> None:
    monkeypatch.setattr(sm, "_fire_binding", _settings_locked)
    monkeypatch.setattr(sm, "_sqlite_lock_passes", lambda exc: False)
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _plant(store, tenant=OWNER_SPACE_ID, creator=OWNER_ACCOUNT_ID)
        after = await _fire(sched, store, job.job_id)
        assert after.last_outcome == "identity_unavailable" and after.failure_count == 1
    finally:
        await store.close()


# ── the S1-16 follow-up: someone else's new time is pinned to what was checked ──


def _after_the_check(monkeypatch, meanwhile) -> None:
    """Run *meanwhile* (an async function of the job id) between the rule's
    check and the write, as a fire or another change racing the edit."""
    real = sm.CronScheduler._not_theirs_to_change

    async def then(self, job_id: str, tenant_id: str | None, **change: Any) -> Any:
        found = await real(self, job_id, tenant_id, **change)
        await meanwhile(job_id)
        return found

    monkeypatch.setattr(sm.CronScheduler, "_not_theirs_to_change", then)


async def _unpinned(self, job_id: str, tenant_id: str | None, **change: Any) -> tuple[str, int | None]:
    """The rule as before the follow-up: allowed, with nothing pinned."""
    found = await self._not_theirs_to_change(job_id, tenant_id, **change)
    return ("", None) if isinstance(found, sm._SomeoneElses) else (str(found or ""), None)


async def _members_new_time_raced(tmp_path, matrix, monkeypatch, meanwhile, *, pinned: bool = True
                                  ) -> tuple[str, ScheduledJob]:
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)

        async def run(job_id: str) -> None:
            await meanwhile(store, job_id)

        _after_the_check(monkeypatch, run)
        if not pinned:
            monkeypatch.setattr(sm.CronScheduler, "_change_rule", _unpinned)
        with scope.bind_scope(SHARED, matrix.account_a):
            moved = await sched.reschedule(job.job_id, timing="3h", tenant_id=SHARED)
        return moved["status"], await store.get(job.job_id)
    finally:
        await store.close()


async def _a_fire_claims_it(store, job_id: str) -> None:
    assert await store.claim_job(job_id, owner="another-runtime")


async def _the_owner_rewords_it(store, job_id: str) -> None:
    assert await store.update_job(job_id, prompt="the owner's later words", tenant_id=SHARED) == "rescheduled"


async def test_a_members_new_time_never_lands_on_a_fire_that_began_meanwhile(tmp_path, matrix, enforced,
                                                                            monkeypatch) -> None:
    status, after = await _members_new_time_raced(tmp_path, matrix, monkeypatch, _a_fire_claims_it)
    assert status == "conflict" and not after.pending_edit, (status, after.pending_edit)


async def test_control_unpinned_the_new_time_is_recorded_on_the_fire(tmp_path, matrix, enforced,
                                                                    monkeypatch) -> None:
    """The edit recorded while it fires is applied when the fire ends: the
    finished one-off is put back, as the owner, by someone else."""
    status, after = await _members_new_time_raced(tmp_path, matrix, monkeypatch, _a_fire_claims_it,
                                                  pinned=False)
    assert status == "edit_pending" and after.pending_edit


async def test_a_members_new_time_never_lands_over_a_change_made_meanwhile(tmp_path, matrix, enforced,
                                                                          monkeypatch) -> None:
    status, after = await _members_new_time_raced(tmp_path, matrix, monkeypatch, _the_owner_rewords_it)
    assert status == "conflict", status
    assert after.prompt == "the owner's later words" and after.status == JobStatus.PENDING


async def test_control_unpinned_the_new_time_lands_over_the_owners_change(tmp_path, matrix, enforced,
                                                                         monkeypatch) -> None:
    status, _after = await _members_new_time_raced(tmp_path, matrix, monkeypatch, _the_owner_rewords_it,
                                                   pinned=False)
    assert status == "rescheduled"


async def test_the_creators_own_change_is_not_pinned(tmp_path, matrix, enforced, monkeypatch) -> None:
    """The person a job acts for changes it as before: nothing is pinned."""
    store = await _store(tmp_path)
    sched = sm.CronScheduler(store=store, graph_builder=None)
    try:
        job = await _owners_job_in_shared(sched, store, matrix)
        with scope.bind_scope(SHARED, OWNER_ACCOUNT_ID):
            assert await sched._change_rule(job.job_id, SHARED) == ("", None)
            assert await sched._change_rule(job.job_id, SHARED, prompt=False) == ("", None)
        with scope.bind_scope(SHARED, matrix.account_a):
            assert await sched._change_rule(job.job_id, SHARED, prompt=False) == ("", job.version)
    finally:
        await store.close()
