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
import sqlite3
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
                 thread: str = "thread-x", target: str = OWNER_CHAT) -> ScheduledJob:
    job = ScheduledJob(
        job_id=f"cron-{secrets.token_hex(6)}", timing="2h",
        prompt=prompt, platform="telegram", thread_id=thread, kind=kind,
        next_run=(datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
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


# ── a sibling's address ──────────────────────────────────────────────────


async def test_a_job_never_takes_another_spaces_address(tmp_path) -> None:
    store = await _store(tmp_path)
    try:
        await _plant(store, tenant=Matrix.SPACE_B, creator="b", thread="one-thread", target="telegram:b-chat",
                     prompt="b's")
        assert await store.sibling_delivery_target("one-thread", Matrix.SPACE_A) == ""
        assert await store.sibling_delivery_target("one-thread", Matrix.SPACE_B) == "telegram:b-chat"
    finally:
        await store.close()
    # Control: the lookup as it was (by thread alone) hands A B's address.
    conn = sqlite3.connect(str(tmp_path / "cron.db"))
    try:
        row = conn.execute("SELECT delivery_target FROM cron_jobs WHERE thread_id = ? AND delivery_target "
                           "LIKE '%:%' ORDER BY created_at DESC LIMIT 1", ("one-thread",)).fetchone()
    finally:
        conn.close()
    assert row[0] == "telegram:b-chat"


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
