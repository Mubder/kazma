"""Spaces S2-07: background work uses its creator's tools and limits as
they are when it runs; a disabled creator pauses their jobs.

* **Rights read when it runs**: Y books a scheduled task while ``web_search``
  is Y's; the admin then switches it off for Y; the task fires (a real
  ``CronScheduler`` fire, its turn offering tools and calling through the
  job's own executor in Y's scope) and neither offers nor runs it. The
  control: the tools frozen at booking (Y's grants as they were) run it.
* **A disabled creator pauses**: while the admin has Y disabled, Y's due
  job is not fired -- not claimed by the poll, and a fire already started
  gives its claim back -- and nothing counts as a failure; the Scheduled
  list says why (``paused``); past its catch-up window the occurrence is
  missed, with the reason; enabled again, the job fires. Every other door
  (``features.work_refusal``) and every tool refuses a disabled person's
  work, as work of a feature that is off waits.
* People disables and enables, recorded; the owner cannot be disabled;
  without Spaces nothing more is read.

Until people link their own chat apps (S3-05) only the owner's jobs fire
(``no_own_address``); these tests let Y's fire by naming Y as the owner the
rule asks for (``scheduler._owner_account``), the one rule that is not
S2-07's.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from kazma_core.cron import scheduler as sm
from kazma_core.cron.store import JobStatus, ScheduledJob, SQLiteCronStore
from kazma_core.spaces import features, identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._scheduled_space_cases import OWNER_CHAT
from tests._space_matrix import Matrix

pytestmark = pytest.mark.real_features


@pytest.fixture(autouse=True)
def _no_real_models(monkeypatch):
    from tests._no_models import finish_turn_memory, keep_models_out

    keep_models_out(monkeypatch, llm=False)
    yield
    finish_turn_memory()


@pytest.fixture
def y(monkeypatch):
    """Y (person A) in a world where Spaces is enforced and Y's jobs fire."""
    import importlib

    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setattr(scope, "_enforced", True)
    messages = importlib.import_module("kazma_core.tools.send_message")
    ops = importlib.import_module("kazma_core.observability.ops_alerts")

    async def backend(target_id, text, **_kw):
        return f"sent:{target_id}"

    monkeypatch.setitem(messages._message_backends, "telegram", backend)
    monkeypatch.setattr(ops, "alert", lambda *_a, **_k: True)
    with Matrix() as matrix:
        matrix.telegram = (messages._message_backends, backend)
        monkeypatch.setattr(sm, "_owner_account", lambda: matrix.account_a)
        people.set_features(matrix.account_a, {"scheduled": True}, by=OWNER_ACCOUNT_ID)
        matrix.monkeypatch = monkeypatch
        yield matrix


class _JobGraph:
    """A scheduled task's turn: the tools its model is offered, and its
    declared executor calling ``web_search`` (the product's own executor and
    tool check, in the fire's scope).

    The registry is the one the matrix shares (``tests._chat_space_cases``),
    so the stand-in ``web_search`` goes in through the test's monkeypatch and
    leaves with the test. Written straight into ``_tools`` it stayed for every
    later test of the process: the matrix's ``tool:web_search`` case, in the
    same fast_test chunk at fae123ffd, called the stand-in and found no search
    recorded (2026-11-22)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.test_feature_grants import _registry

        self.registry = _registry()
        self.entered: list[str] = []
        self.offered: list[set[str]] = []
        self.answers: list[dict[str, Any]] = []
        tool = self.registry.get_tool("web_search")

        async def recorder(*_a: Any, **_k: Any) -> str:
            self.entered.append("web_search")
            return "results"

        monkeypatch.setitem(self.registry._tools, "web_search", replace(tool, func=recorder, is_async=True))

    async def ainvoke(self, state, config=None):
        from kazma_core.agent_runner import DeclaredToolExecutor
        from kazma_core.skills.switches import offered

        defs = await asyncio.to_thread(offered, self.registry.get_tool_definitions())
        self.offered.append({d["function"]["name"] for d in defs})
        executor = DeclaredToolExecutor(self.registry, allowed={"web_search"})
        self.answers.append(await executor.execute("web_search", {"query": "the news"}))
        messages = list(state.get("messages", []))
        messages.append({"role": "assistant", "content": "Done."})
        return {"messages": messages}


def _job_graph(matrix: Matrix) -> _JobGraph:
    """A :class:`_JobGraph`; building the tool registry reads the stored
    Spaces switch into the process and names the app's chat senders, so the
    test's switch and sender are set again."""
    graph = _JobGraph(matrix.monkeypatch)
    matrix.monkeypatch.setattr(scope, "_enforced", True)
    backends, backend = matrix.telegram  # the app the registry built named its own sender
    matrix.monkeypatch.setitem(backends, "telegram", backend)
    return graph


async def _store(tmp_path) -> SQLiteCronStore:
    store = SQLiteCronStore(db_path=str(tmp_path / "cron.db"))
    await store.init()
    return store


async def _plant(store, matrix: Matrix, *, due_ago: timedelta = timedelta(seconds=5),
                 timing: str = "2h") -> ScheduledJob:
    job = ScheduledJob(
        job_id=f"cron-{secrets.token_hex(6)}", timing=timing, prompt="read me the news",
        platform="telegram", thread_id="thread-y", kind="task",
        next_run=(datetime.now(UTC) - due_ago).isoformat(),
        tenant_id=Matrix.SPACE_A, delivery_target=OWNER_CHAT, creator=matrix.account_a,
    )
    await store.insert(job)
    stored = await store.get(job.job_id)  # as the store keeps it (its times normalised)
    assert stored is not None
    return stored


async def _fire(sched, store, job_id: str) -> ScheduledJob:
    job = await store.get(job_id)
    assert job is not None
    await sched._execute(job)
    after = await store.get(job_id)
    assert after is not None
    return after


def _at(value: str) -> datetime:
    return datetime.fromisoformat(str(value))


async def _poll(sched) -> None:
    sched._running = True
    await sched._poll_once()
    await asyncio.gather(*list(sched._exec_tasks), return_exceptions=True)


# ── rights read when the work runs ──────────────────────────────────────


async def test_a_job_booked_before_a_tool_was_taken_away_does_not_use_it(tmp_path, y) -> None:
    store = await _store(tmp_path)
    graph = _job_graph(y)
    sched = sm.CronScheduler(store=store, graph_builder=lambda: graph)
    try:
        job = await _plant(store, y)
        assert "web_search" not in features.grants_of(identity.read_registry(), y.account_a).tools_off
        people.set_tools(y.account_a, ["web_search"], by=OWNER_ACCOUNT_ID)  # taken away after booking
        done = await _fire(sched, store, job.job_id)
        assert done.last_outcome == "delivered", done.last_result
        assert graph.offered and "web_search" not in graph.offered[0]
        assert graph.entered == [] and graph.answers[0].get("refused"), graph.answers
    finally:
        await store.close()


async def test_control_the_tools_frozen_at_booking_run_it(tmp_path, y, monkeypatch) -> None:
    store = await _store(tmp_path)
    graph = _job_graph(y)
    sched = sm.CronScheduler(store=store, graph_builder=lambda: graph)
    try:
        job = await _plant(store, y)
        frozen = features.grants_of(identity.read_registry(), y.account_a)  # Y's grants at booking
        people.set_tools(y.account_a, ["web_search"], by=OWNER_ACCOUNT_ID)
        monkeypatch.setattr(features, "grants_here", lambda: frozen)
        await _fire(sched, store, job.job_id)
        assert graph.entered == ["web_search"]
    finally:
        await store.close()


# ── a disabled creator pauses their jobs ────────────────────────────────


async def test_a_disabled_creators_job_waits_and_says_why(tmp_path, y) -> None:
    store = await _store(tmp_path)
    graph = _job_graph(y)
    sched = sm.CronScheduler(store=store, graph_builder=lambda: graph)
    try:
        job = await _plant(store, y)
        people.set_enabled(y.account_a, False, by=OWNER_ACCOUNT_ID)
        await _poll(sched)  # not claimed: not fired
        held = await store.get(job.job_id)
        assert held.status == JobStatus.PENDING and _at(held.next_run) == _at(job.next_run)
        assert held.failure_count == 0
        assert graph.offered == []
        done = await _fire(sched, store, job.job_id)  # a fire that started anyway gives its claim back
        assert done.status == JobStatus.PENDING and _at(done.next_run) == _at(job.next_run)
        assert done.failure_count == 0
        assert graph.offered == []
        with y.as_a():
            [listed] = await sched.list_jobs(tenant_id=Matrix.SPACE_A, scoped=True)
        assert listed["paused"] == "creator_disabled"

        people.set_enabled(y.account_a, True, by=OWNER_ACCOUNT_ID)
        done = await _fire(sched, store, job.job_id)
        assert done.last_outcome == "delivered" and graph.entered == ["web_search"]
        with y.as_a():
            [listed] = await sched.list_jobs(tenant_id=Matrix.SPACE_A, scoped=True)
        assert "paused" not in listed
    finally:
        await store.close()


async def test_past_its_window_a_paused_occurrence_is_missed_with_the_reason(tmp_path, y) -> None:
    store = await _store(tmp_path)
    graph = _job_graph(y)
    sched = sm.CronScheduler(store=store, graph_builder=lambda: graph)
    try:
        one_off = await _plant(store, y, due_ago=timedelta(hours=3), timing="in 5 minutes")
        daily = await _plant(store, y, due_ago=timedelta(hours=3), timing="every day at 09:00")
        people.set_enabled(y.account_a, False, by=OWNER_ACCOUNT_ID)
        await _poll(sched)
        missed = await store.get(one_off.job_id)
        assert missed.status == JobStatus.MISSED and "disabled" in missed.last_result, missed.last_result
        moved = await store.get(daily.job_id)
        assert moved.status == JobStatus.PENDING and _at(moved.next_run) != _at(daily.next_run)
        assert "disabled" in moved.last_result and moved.failure_count == 0
        assert graph.offered == []
    finally:
        await store.close()


def test_every_other_door_refuses_a_disabled_persons_work(y) -> None:
    people.set_enabled(y.account_a, False, by=OWNER_ACCOUNT_ID)
    for feature in ("research", "documents", "knowledge", "swarm", "x_studio", "scheduled"):
        why = features.work_refusal(feature, y.account_a)
        assert why and "disabled" in why, (feature, why)
    grants = features.grants_of(identity.read_registry(), y.account_a)
    assert features.tool_refusal("current_datetime", grants), "an always-on tool too"
    assert features.work_refusal("research", y.account_b) is None, "another person's work runs"


def test_control_an_enabled_person_runs(y) -> None:
    assert features.work_refusal("research", y.account_a) is None
    grants = features.grants_of(identity.read_registry(), y.account_a)
    assert features.tool_refusal("current_datetime", grants) is None


# ── People ──────────────────────────────────────────────────────────────


def test_people_disables_and_enables_recorded(y, monkeypatch) -> None:
    ended: list[str] = []
    monkeypatch.setattr(people, "_end_sessions_of", lambda user_id: ended.append(user_id))
    people.set_enabled(y.account_a, False, by=OWNER_ACCOUNT_ID)
    listed = {p["id"]: p for p in people.list_people()}
    assert listed[y.account_a]["enabled"] is False and ended, ended
    # Their switches stay as they were (People shows them).
    assert any(f["on"] for f in listed[y.account_a]["features"])
    people.set_enabled(y.account_a, True, by=OWNER_ACCOUNT_ID)
    kinds = [e["kind"] for e in identity.read_registry()["events"]][-2:]
    assert kinds == ["account_disabled", "account_enabled"]
    with pytest.raises(people.PeopleRefused) as refused:
        people.set_enabled(OWNER_ACCOUNT_ID, False, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "is_the_owner"
    with pytest.raises(people.PeopleRefused) as refused:
        people.set_enabled(y.account_a, "no", by=OWNER_ACCOUNT_ID)
    assert refused.value.code == "bad_enabled" and refused.value.status == 400


def test_the_route_disables_a_person(y) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    app = FastAPI()
    app.include_router(create_people_router())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal",
                      lambda request: {"source": "secret", "actor": "owner", "role": "admin"})
        with TestClient(app) as client:
            got = client.put(f"/api/people/{y.account_a}/enabled", json={"enabled": False})
            bad = client.put(f"/api/people/{y.account_a}/enabled", json={"enabled": "off"})
    assert got.status_code == 200, got.text
    assert bad.status_code == 400 and bad.json()["error"] == "bad_enabled", bad.text
    assert y.account_a in identity.disabled_accounts()


async def test_without_spaces_the_poll_reads_nothing_more(tmp_path, y, monkeypatch) -> None:
    store = await _store(tmp_path)
    graph = _job_graph(y)
    sched = sm.CronScheduler(store=store, graph_builder=lambda: graph)
    try:
        await _plant(store, y)
        monkeypatch.setattr(scope, "_enforced", False)
        monkeypatch.setattr(identity, "disabled_accounts",
                            lambda: pytest.fail("the disabled accounts were read without Spaces"))
        await _poll(sched)
        assert graph.offered, "the job fired"
    finally:
        await store.close()
