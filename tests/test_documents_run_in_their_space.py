"""A document's work runs in the document's own space (S4-01 step 0a: rows 4,
5, 14, 15, 29, 30).

The worker's stages, the recoveries at start and the reconcile of the
Knowledge index ran with no space bound, or in the space of whoever's turn
started the workers. Each now runs in the space its own row names, whoever
started it; with Spaces off they bind nothing, as before.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.config_store import ConfigStoreUnavailableError, get_config_store
from kazma_core.db import availability
from kazma_core.documents import ingestion
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobRepository
from kazma_core.documents.knowledge import DocumentKnowledgeAdapter
from kazma_core.documents.models import DocumentJobState
from kazma_core.documents.worker import DocumentWorker, PermanentDocumentError
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.row_work import Door
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, Scope, bind_scope, current_scope
from kazma_core.stores.knowledge import KnowledgeStore
from kazma_core.stores.knowledge_index import KnowledgeIndex
from kazma_core.tenant_context import tenant_scope

from tests._documents_seed import seed_document, seeded_service
from tests._space_door import watched


@pytest.fixture(autouse=True)
def _quick_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop waits for a sweep that is asleep: not 30 s of it."""
    monkeypatch.setattr(ingestion, "_INTAKE_SWEEP_STOP_WAIT_S", 0.2)


@pytest.fixture
def world(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Spaces on: reem in space A, omar in space B (the owner made both)."""
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    store.delete(ingestion.MOVES_KEY)
    made = SimpleNamespace(svc=seeded_service(tmp_path / "documents"), root=tmp_path)
    for who, space in (("reem", "a"), ("omar", "b")):
        account = people.create_person(who.title(), "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        shared = people.create_shared_space(space.upper(), by=OWNER_ACCOUNT_ID)["id"]
        people.add_member(shared, account, by=OWNER_ACCOUNT_ID)
        setattr(made, who, account)
        setattr(made, space, shared)
    try:
        yield made
    finally:
        made.svc.close()
        store.delete(identity.REGISTRY_KEY)
        store.delete(ingestion.MOVES_KEY)
        reset_posture_cache()


@pytest.fixture
def off(tmp_path: Any) -> Any:
    """An install without Spaces, two tenants' documents."""
    made = SimpleNamespace(svc=seeded_service(tmp_path / "documents"), root=tmp_path,
                           a="acme", b="beta", reem="alice", omar="bob")
    assert not scope.spaces_enforced()
    try:
        yield made
    finally:
        made.svc.close()
        get_config_store().delete(ingestion.MOVES_KEY)


def _queued(svc: DocumentIngestionService, tenant: str, actor: str, marker: str) -> Any:
    """A document of *actor*, its job waiting for a worker."""
    seeded = seed_document(svc, tenant_id=tenant, owner=actor, marker=marker)
    _set(svc, seeded.job_id, "state = 'ready_to_parse', stage = 'ready_to_parse'")
    return seeded


def _set(svc: DocumentIngestionService, job_id: Any, columns: str) -> None:
    with svc.jobs._lock:
        svc.jobs._conn.execute(f"UPDATE document_jobs SET {columns} WHERE id = ?", (str(job_id),))
        svc.jobs._conn.commit()


# ── the worker's stages (row 4) ──────────────────────────────────────────


async def _work_both(world: Any, started_in: tuple[str, str] | None) -> dict[str, list[Any]]:
    """Run the real worker over every waiting job; the scope each stage saw."""
    seen: dict[str, list[Any]] = {}

    def stage(job: Any, _context: Any) -> Any:
        import time

        time.sleep(0.4)  # long enough for a heartbeat
        seen.setdefault(str(job.tenant_id), []).append(current_scope())
        raise PermanentDocumentError("end_of_test", "the stage was reached")

    worker = DocumentWorker(world.svc.jobs, {s: stage for s in DocumentJobState}, concurrency=2,
                            poll_interval=0.05, lease_seconds=5, heartbeat_interval=0.1)

    async def run() -> None:
        await worker.start()
        for _ in range(200):
            if len(seen) == 2:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.3)
        await worker.stop()

    if started_in is None:
        await run()
    else:
        with bind_scope(*started_in):
            await run()
    return seen


_OUTCOME = ((DocumentJobRepository, "renew_lease"), (DocumentJobRepository, "record_failure"))


@pytest.mark.parametrize("from_a_turn", [False, True], ids=["at start", "in B's turn"])
def test_a_stage_its_heartbeat_and_its_outcome_are_the_jobs_space(world: Any, from_a_turn: bool) -> None:
    _queued(world.svc, world.a, world.reem, "tides")
    # A job written before Spaces names no person.
    _set(world.svc, _queued(world.svc, world.b, world.omar, "stars").job_id, "actor_id = ''")
    with watched(*_OUTCOME) as calls:
        seen = asyncio.run(_work_both(world, (world.b, world.omar) if from_a_turn else None))
    assert seen == {world.a: [Scope(world.a, world.reem)],
                    # no person on the row: the space's own account, its maker
                    world.b: [Scope(world.b, OWNER_ACCOUNT_ID)]}
    assert calls.of("renew_lease") and len(calls.of("record_failure")) == 2
    for call in calls:
        assert call.space == call.kwargs["tenant_id"], call


def test_off_a_stage_binds_nothing(off: Any) -> None:
    _queued(off.svc, off.a, off.reem, "tides")
    _queued(off.svc, off.b, off.omar, "stars")
    assert asyncio.run(_work_both(off, None)) == {off.a: [None], off.b: [None]}


def test_whose_job_it_is_cannot_be_read_and_no_try_is_spent(world: Any, monkeypatch: Any) -> None:
    _queued(world.svc, world.a, world.reem, "tides")
    claimed = world.svc.jobs.claim_next(owner="w", lease_seconds=60)

    def away(*_a: Any, **_k: Any) -> Any:
        raise identity.IdentityUnavailable("the settings store is away")

    with monkeypatch.context() as patch, watched((DocumentJobRepository, "hand_back")) as calls:
        patch.setattr(identity, "read_registry", away)
        asyncio.run(DocumentWorker(world.svc.jobs, {})._process_claimed(claimed, "w"))
    assert calls.of("hand_back") == [world.a]
    job = world.svc.jobs.get(tenant_id=world.a, job_id=claimed.id)
    assert claimed.attempt == 1 and (job.attempt, job.error_code, job.lease_owner) == (0, None, None), job


def test_a_job_of_no_space_fails_and_is_not_retried(world: Any) -> None:
    _queued(world.svc, world.a, world.reem, "tides")
    claimed = world.svc.jobs.claim_next(owner="w", lease_seconds=60)
    with watched((DocumentJobRepository, "record_failure")) as calls:
        asyncio.run(DocumentWorker(world.svc.jobs, {})._process_claimed(
            dataclasses.replace(claimed, tenant_id="Not A Space!"), "w"))
    [call] = calls
    assert (call.kwargs["error_code"], call.kwargs["transient"]) == ("no_space", False)
    assert call.space == call.kwargs["tenant_id"] == "Not A Space!" and call.scope is None


def test_a_space_recorded_while_its_job_is_claimed_is_not_no_space(world: Any, monkeypatch: Any) -> None:
    """The import is read before the people (``importing_spaces``): read the
    other way round, a space recorded in between had neither, and a good
    document was rejected for good."""
    from kazma_core.spaces import space_move

    _queued(world.svc, world.a, world.reem, "tides")
    claimed = world.svc.jobs.claim_next(owner="w", lease_seconds=60)
    real = identity.read_registry
    read: list[str] = []

    def journal() -> Any:
        read.append("journal")  # emptied once the space is recorded
        return frozenset() if "people" in read else frozenset({world.a})

    def people_() -> Any:
        read.append("people")
        reg = real()
        reg["spaces"].pop(world.a)  # recorded just after this was read
        return reg

    monkeypatch.setattr(space_move, "importing_spaces", journal)
    monkeypatch.setattr(identity, "read_registry", people_)
    with watched((DocumentJobRepository, "hand_back"), (DocumentJobRepository, "record_failure")) as calls:
        asyncio.run(DocumentWorker(world.svc.jobs, {})._process_claimed(claimed, "w"))
    assert [(c.name, c.kwargs.get("reason")) for c in calls] == [("hand_back", "space_arriving")]
    assert read[0] == "journal"
    job = world.svc.jobs.get(tenant_id=world.a, job_id=claimed.id)
    assert (job.state, job.error_code, job.attempt) == (DocumentJobState.RETRY_WAIT, None, 0)


def test_an_import_journal_that_cannot_be_read_spends_no_try(world: Any, monkeypatch: Any) -> None:
    from kazma_core.spaces import space_move

    _queued(world.svc, world.a, world.reem, "tides")
    claimed = world.svc.jobs.claim_next(owner="w", lease_seconds=60)

    def locked() -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(space_move, "importing_spaces", locked)
    asyncio.run(DocumentWorker(world.svc.jobs, {})._process_claimed(claimed, "w"))
    job = world.svc.jobs.get(tenant_id=world.a, job_id=claimed.id)
    assert (job.state, job.error_code, job.attempt) == (DocumentJobState.RETRY_WAIT, None, 0)


def test_a_job_given_back_again_is_said_once_and_waits_longer(world: Any, monkeypatch: Any,
                                                              caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG", logger="kazma_core.documents.worker")
    _queued(world.svc, world.a, world.reem, "tides")
    claimed = world.svc.jobs.claim_next(owner="w", lease_seconds=60)
    identity.update_registry(lambda reg: reg["spaces"][world.a].update(erasing_at="now"))
    worker = DocumentWorker(world.svc.jobs, {})

    async def again() -> None:
        for _ in range(6):
            await worker._process_claimed(claimed, "w")

    with watched((DocumentJobRepository, "hand_back")) as calls:
        asyncio.run(again())
    assert [c.kwargs["delay_seconds"] for c in calls] == [30, 60, 120, 240, 300, 300]
    assert [r.levelname for r in caplog.records if "given back" in r.getMessage()] == ["WARNING"] + ["DEBUG"] * 5


def test_a_job_given_back_is_said(world: Any, monkeypatch: Any, caplog: pytest.LogCaptureFixture) -> None:
    _queued(world.svc, world.a, world.reem, "tides")
    claimed = world.svc.jobs.claim_next(owner="w", lease_seconds=60)

    def away(*_a: Any, **_k: Any) -> Any:
        raise identity.IdentityUnavailable("the settings store is away")

    monkeypatch.setattr(identity, "read_registry", away)
    asyncio.run(DocumentWorker(world.svc.jobs, {})._process_claimed(claimed, "w"))
    said = [r for r in caplog.records if "given back" in r.getMessage()]
    assert [r.levelname for r in said] == ["WARNING"] and "identity_unavailable" in said[0].getMessage()


@pytest.mark.parametrize(("case", "reason"), [("erasing", "space_erasing"), ("arriving", "space_arriving")])
def test_a_job_of_a_space_that_goes_or_comes_waits(world: Any, monkeypatch: Any, case: str, reason: str) -> None:
    """Nothing binds a space being erased (S1-27); one being brought in is
    recorded last. Neither job is failed, and neither spends a try."""
    from kazma_core.spaces import space_move

    _queued(world.svc, world.a, world.reem, "tides")
    claimed = world.svc.jobs.claim_next(owner="w", lease_seconds=60)
    if case == "erasing":
        identity.update_registry(lambda reg: reg["spaces"][world.a].update(erasing_at="2026-11-25T00:00:00Z"))
    else:
        monkeypatch.setattr(space_move, "importing_spaces", lambda: {world.a})
        real = identity.read_registry

        def without() -> Any:
            reg = real()
            reg["spaces"].pop(world.a, None)
            return reg

        monkeypatch.setattr(identity, "read_registry", without)
    seen: list[Any] = []
    with watched((DocumentJobRepository, "hand_back")) as calls:
        asyncio.run(DocumentWorker(world.svc.jobs, {s: lambda *_a: seen.append(1) for s in DocumentJobState})
                    ._process_claimed(claimed, "w"))
    assert [c.kwargs["reason"] for c in calls] == [reason] and calls.of("hand_back") == [world.a]
    job = world.svc.jobs.get(tenant_id=world.a, job_id=claimed.id)
    assert not seen and (job.attempt, job.error_code) == (0, None)


# ── the loops (row 30) ───────────────────────────────────────────────────


def _loops_started_in_a_turn(world: Any) -> list[Any]:
    async def run() -> Any:
        with watched((DocumentJobRepository, "claim_next")) as calls:
            with bind_scope(world.b, world.omar):
                await world.svc.start_workers()
            await asyncio.sleep(0.6)
            await world.svc.stop_workers()
        return list(calls)

    return asyncio.run(run())


def test_loops_started_in_a_turn_hold_no_persons_space(world: Any) -> None:
    calls = _loops_started_in_a_turn(world)
    assert calls and {(c.scope, c.space) for c in calls} == {(None, "<install>")}


def test_off_the_loops_keep_what_started_them(off: Any) -> None:
    calls = _loops_started_in_a_turn(off)
    assert calls and {c.scope for c in calls} == {Scope(off.b, off.omar)}


def test_a_job_started_from_anothers_turn_is_indexed_in_its_own_library(world: Any) -> None:
    """The fault of today: workers started in B's turn kept B, and Knowledge
    refused A's page (``KnowledgeLibraryGone``)."""
    seen: list[Any] = []
    store = KnowledgeStore(str(world.root / "knowledge.db"))
    with bind_scope(world.a, world.reem):
        store.create_library("docs", "Documents")

    def stage(_job: Any, _context: Any) -> Any:
        seen.append(store._writing_space())
        raise PermanentDocumentError("end_of_test", "the stage was reached")

    _queued(world.svc, world.a, world.reem, "tides")
    world.svc.stage_handlers = lambda: {s: stage for s in DocumentJobState}

    async def run() -> None:
        with bind_scope(world.b, world.omar):
            await world.svc.start_workers()
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.05)
        await world.svc.stop_workers()

    asyncio.run(run())
    assert seen == [world.a]


# ── interrupted intake (row 5) ───────────────────────────────────────────


def _interrupted(world: Any, monkeypatch: pytest.MonkeyPatch, fails: BaseException) -> list[Any]:
    """Recover two jobs a crash left in intake; the first one *fails*."""
    jobs = []
    for tenant, actor, marker in ((world.a, world.reem, "tides"), (world.b, world.omar, "stars")):
        seeded = seed_document(world.svc, tenant_id=tenant, owner=actor, marker=marker)  # its job: received
        jobs.append(world.svc.jobs.get(tenant_id=tenant, job_id=seeded.job_id))
    monkeypatch.setattr(world.svc.jobs, "interrupted_intake_jobs", lambda: jobs)
    monkeypatch.setattr(world.svc, "_intake_owner_is_live", lambda _job: False)
    seen: list[Any] = []

    def advance(job: Any, **_kw: Any) -> Any:
        seen.append((job.tenant_id, current_scope()))
        if len(seen) == 1:
            raise fails
        return SimpleNamespace(state=DocumentJobState.READY_TO_PARSE)

    monkeypatch.setattr(world.svc, "_advance_intake", advance)
    return seen


def test_one_interrupted_job_that_cannot_be_recovered_stops_no_other(world: Any, monkeypatch: Any) -> None:
    seen = _interrupted(world, monkeypatch, scope.NoSpaceBound("refused"))
    with bind_scope(world.b, world.omar):  # started from B's turn
        assert world.svc.recover_interrupted_intake() == 1
    assert seen == [(world.a, Scope(world.a, world.reem)), (world.b, Scope(world.b, world.omar))]


def test_a_stop_ends_the_pass_between_jobs(world: Any, monkeypatch: Any) -> None:
    """A stop waited for every interrupted intake's inspection."""
    seen = _interrupted(world, monkeypatch, RuntimeError("unused"))

    def advance(job: Any, **_kw: Any) -> Any:
        seen.append(job.tenant_id)
        world.svc._intake_sweep_stop.set()  # stop_workers, from another thread
        return SimpleNamespace(state=DocumentJobState.READY_TO_PARSE)

    monkeypatch.setattr(world.svc, "_advance_intake", advance)
    assert world.svc.recover_interrupted_intake() == 1 and seen == [world.a]


def test_a_start_after_a_stop_recovers_again(world: Any, monkeypatch: Any) -> None:
    ran: list[bool] = []
    monkeypatch.setattr(world.svc, "recover_interrupted_intake",
                        lambda **_kw: ran.append(world.svc._intake_sweep_stop.is_set()) or 0)
    world.svc._intake_sweep_stop.set()

    async def run() -> None:
        await world.svc.start_workers()
        await world.svc.stop_workers()

    asyncio.run(run())
    assert ran == [False], "the stop of before is over when the start's own pass runs"


def test_the_sweep_leaves_a_job_a_worker_holds(world: Any, monkeypatch: Any) -> None:
    """A worker validating a job under its lease: the sweep inspected it too."""
    seen = _interrupted(world, monkeypatch, RuntimeError("unused"))
    jobs = world.svc.jobs.interrupted_intake_jobs()
    leased = dataclasses.replace(jobs[0], lease_owner="document-worker-x:0")
    monkeypatch.setattr(world.svc.jobs, "interrupted_intake_jobs", lambda: [leased, jobs[1]])
    monkeypatch.setattr(world.svc, "_advance_intake", lambda job, **_kw: seen.append(
        (job.tenant_id, None)) or SimpleNamespace(state=DocumentJobState.READY_TO_PARSE))
    assert world.svc.recover_interrupted_intake(unleased_only=True) == 1
    assert [tenant for tenant, _scope in seen] == [world.b]
    assert world.svc.recover_interrupted_intake() == 2, "the start's own pass is as before"


def _many(world: Any, monkeypatch: pytest.MonkeyPatch, fails: BaseException, count: int = 12) -> list[Any]:
    _interrupted(world, monkeypatch, fails)
    jobs = world.svc.jobs.interrupted_intake_jobs()
    rows = [jobs[0]] * count
    monkeypatch.setattr(world.svc.jobs, "interrupted_intake_jobs", lambda: rows)
    tried: list[Any] = []

    def advance(job: Any, **_kw: Any) -> Any:
        tried.append(job.id)
        raise fails

    monkeypatch.setattr(world.svc, "_advance_intake", advance)
    return tried


@pytest.mark.parametrize("fails", [sqlite3.OperationalError("database is locked"),
                                   ConfigStoreUnavailableError(),
                                   identity.IdentityUnavailable("away")], ids=lambda e: type(e).__name__)
def test_a_store_that_answers_nothing_ends_the_pass(world: Any, monkeypatch: Any, fails: BaseException,
                                                    caplog: pytest.LogCaptureFixture) -> None:
    tried = _many(world, monkeypatch, fails)
    assert world.svc.recover_interrupted_intake() == 0 and len(tried) == 3
    assert world.svc._interrupted_left, "the sweep takes them up again"
    said = [r for r in caplog.records if r.name.endswith("ingestion") and r.levelname == "WARNING"]
    assert len(said) == 2 and "stopped after 3 in a row" in said[1].getMessage()
    traced = said[0].exc_info is not None and said[0].exc_info[0] is not None
    assert traced is not isinstance(fails, identity.IdentityUnavailable), "unreadable people need no traceback"


def test_rows_that_fail_each_for_itself_are_all_tried(world: Any, monkeypatch: Any) -> None:
    tried = _many(world, monkeypatch, scope.NoSpaceBound("refused"))
    assert world.svc.recover_interrupted_intake() == 0 and len(tried) == 12


def test_a_row_that_goes_on_starts_the_count_again() -> None:
    from kazma_core.documents.ingestion import _Pass

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        rows = _Pass(set(), "rows")
        locked = sqlite3.OperationalError("locked")
        assert not rows.failed(locked, "a") and not rows.failed(locked, "b")
        rows.went_on()
        assert not rows.failed(locked, "c") and not rows.failed(locked, "d") and rows.failed(locked, "e")


def test_one_rows_failure_is_said_once(world: Any, monkeypatch: Any, caplog: pytest.LogCaptureFixture) -> None:
    """Every pass over several spaces' rows says what it left, the first time
    as a WARNING with the traceback."""
    caplog.set_level("DEBUG", logger="kazma_core.documents.ingestion")
    _many(world, monkeypatch, KeyError("metadata"), count=1)
    for _ in range(3):
        world.svc.recover_interrupted_intake()
    said = [r for r in caplog.records if "was not resumed" in r.getMessage()]
    assert [r.levelname for r in said] == ["WARNING", "DEBUG", "DEBUG"] and said[0].exc_info[0] is KeyError


@pytest.mark.parametrize("case", ["arriving", "erasing"])
def test_an_interrupted_intake_of_a_space_that_comes_or_goes_is_tried_again(world: Any, monkeypatch: Any,
                                                                           case: str) -> None:
    from kazma_core.spaces import space_move

    _interrupted(world, monkeypatch, RuntimeError("unused"))
    if case == "arriving":
        monkeypatch.setattr(space_move, "importing_spaces", lambda: frozenset({world.a}))
        real = identity.read_registry

        def without() -> Any:
            reg = real()
            reg["spaces"].pop(world.a)
            return reg

        monkeypatch.setattr(identity, "read_registry", without)
    else:
        identity.update_registry(lambda reg: reg["spaces"][world.a].update(erasing_at="now"))
    world.svc.recover_interrupted_intake()
    assert world.svc._interrupted_left


def test_a_job_that_is_not_as_read_stops_no_other(world: Any, monkeypatch: Any) -> None:
    _interrupted(world, monkeypatch, KeyError("metadata"))
    assert world.svc.recover_interrupted_intake() == 1
    assert not world.svc._interrupted_left, "nothing the sweep can do better"


def test_interrupted_intakes_whose_people_were_unread_are_tried_by_the_sweep(world: Any, monkeypatch: Any) -> None:
    seen = _interrupted(world, monkeypatch, identity.IdentityUnavailable("one answer was lost"))
    assert world.svc.recover_interrupted_intake() == 1 and world.svc._interrupted_left
    from kazma_core import shutdown

    async def soon(_seconds: float) -> bool:
        return len(seen) > 2  # one pass of the sweep, then stop

    monkeypatch.setattr(shutdown, "sleep_or_shutdown", soon)
    asyncio.run(world.svc._sweep_stranded_intakes())
    assert [tenant for tenant, _scope in seen] == [world.a, world.b, world.a, world.b]
    assert not world.svc._interrupted_left


def test_an_outage_while_recovering_is_not_one_jobs_failure(world: Any, monkeypatch: Any) -> None:
    _interrupted(world, monkeypatch, RuntimeError("the database is away"))
    monkeypatch.setattr(availability, "is_outage", lambda _exc: True)
    with pytest.raises(RuntimeError, match="away"):
        world.svc.recover_interrupted_intake()


def test_off_an_interrupted_jobs_failure_is_the_starts(off: Any, monkeypatch: Any) -> None:
    seen = _interrupted(off, monkeypatch, RuntimeError("the store refused"))
    with pytest.raises(RuntimeError, match="refused"):
        off.svc.recover_interrupted_intake()
    assert seen == [(off.a, None)]


# ── moves cut off (row 14) ───────────────────────────────────────────────


def _cut_off_moves(world: Any, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Two moves a stop cut off, each copied into B; the first copy's taking
    back fails."""
    for n in (1, 2):
        source = seed_document(world.svc, tenant_id=world.a, owner=world.reem, marker=f"source {n}")
        copy = seed_document(world.svc, tenant_id=world.b, owner=world.omar, marker=f"copy {n}")
        ingestion._record_move(f"{n}" * 8, {"source": world.a, "target": world.b, "copy": str(copy.document_id),
                                            "document_id": str(source.document_id), "actor": world.omar,
                                            "key": f"move:{n}", "at": 1.0})
    seen: list[Any] = []

    def tombstone(**kw: Any) -> Any:
        seen.append((kw["tenant_id"], current_scope()))
        if len(seen) == 1:
            raise RuntimeError("the store refused")

    monkeypatch.setattr(world.svc, "_tombstone", tombstone)
    return seen


def test_one_move_that_cannot_be_settled_stops_no_other(world: Any, monkeypatch: Any) -> None:
    seen = _cut_off_moves(world, monkeypatch)
    with watched((world.svc.repository, "get_document")) as calls:
        settled = world.svc.settle_moves()
    assert [s["outcome"] for s in settled] == ["copy_taken_back"]
    assert seen == [(world.b, Scope(world.b, world.omar))] * 2, "as its person, where the copy is"
    assert len(get_config_store().get(ingestion.MOVES_KEY)) == 1, "the failed one stays for the next start"
    assert [(c.space, c.kwargs["tenant_id"]) for c in calls] == [
        (world.a, world.a), (world.b, world.b)] * 2, "the source's half is the source's"


def test_the_copy_a_cut_off_move_did_not_record_is_looked_up_in_the_target(world: Any, monkeypatch: Any) -> None:
    """The journal's copy was never written: it is found by the move's key,
    among the target's jobs (the case a lookup in another space misses, and
    settles as "nothing copied" with the copy left behind)."""
    _cut_off_moves(world, monkeypatch)
    ingestion._settle_move("2" * 8)
    ingestion._record_move("1" * 8, {"copy": ""})
    with watched((DocumentJobRepository, "get_by_idempotency_key")) as calls, bind_scope(world.a, world.reem):
        world.svc.settle_moves()
    assert [(c.space, c.kwargs["tenant_id"]) for c in calls] == [(world.b, world.b)]


def test_off_settling_binds_nothing_and_fails_as_before(off: Any, monkeypatch: Any) -> None:
    seen = _cut_off_moves(off, monkeypatch)
    with pytest.raises(RuntimeError, match="refused"):
        off.svc.settle_moves()
    assert seen == [(off.b, None)]


# ── intakes a dead process left (row 15) ─────────────────────────────────


def _stranded(world: Any, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    rows = [{"token": f"t{n}", "tenant_id": tenant, "owner": "dead", "job_id": "j", "publishing": 0}
            for n, tenant in enumerate((world.a, world.b))]
    admissions = world.svc.admissions
    monkeypatch.setattr(admissions, "unpublished", lambda: rows)
    monkeypatch.setattr(admissions, "claim", lambda token, **_kw: True)
    monkeypatch.setattr(admissions, "fenced_recent", lambda: rows)
    monkeypatch.setattr(ingestion, "owner_state", lambda _owner: "dead")
    seen: list[Any] = []
    for name in ("_rollback_intake", "_discard_tagged"):
        monkeypatch.setattr(world.svc, name, lambda row, name=name: seen.append(
            (name, row["tenant_id"], getattr(Door.now(), "space", None), current_scope())) or True)
    return seen


@pytest.mark.parametrize("from_a_turn", [False, True], ids=["at start", "in B's turn"])
def test_a_stranded_intake_is_rolled_back_in_its_own_space(world: Any, monkeypatch: Any, from_a_turn: bool) -> None:
    seen = _stranded(world, monkeypatch)
    bound = None
    if from_a_turn:
        with bind_scope(world.b, world.omar) as bound:
            assert world.svc._recover_stranded_intakes() == 2
    else:
        assert world.svc._recover_stranded_intakes() == 2
    assert seen == [(name, tenant, tenant, bound) for name in ("_rollback_intake", "_discard_tagged")
                    for tenant in (world.a, world.b)], "its statements; nobody acts as anyone else"


@pytest.mark.parametrize("what", ["claim", "_discard_tagged"])
def test_one_stranded_row_that_fails_stops_no_other(world: Any, monkeypatch: Any, what: str) -> None:
    """A claim that raised was not caught: Documents did not start."""
    seen = _stranded(world, monkeypatch)
    owner = world.svc.admissions if what == "claim" else world.svc
    calls: list[Any] = []

    def first_fails(row: Any, **_kw: Any) -> bool:
        calls.append(row)
        if len(calls) == 1:
            raise RuntimeError("this row was refused")
        return True

    monkeypatch.setattr(owner, what, first_fails)
    assert world.svc._recover_stranded_intakes() == (1 if what == "claim" else 2)
    assert len(calls) == 2, "the second row is tried"
    assert seen


def test_an_outage_at_a_claim_is_raised_as_before(world: Any, monkeypatch: Any) -> None:
    _stranded(world, monkeypatch)

    def away(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("the database is away")

    monkeypatch.setattr(world.svc.admissions, "claim", away)
    monkeypatch.setattr(availability, "is_outage", lambda _exc: True)
    with pytest.raises(RuntimeError, match="away"):
        world.svc._recover_stranded_intakes()


def test_off_a_claim_that_fails_is_raised_as_before(off: Any, monkeypatch: Any) -> None:
    _stranded(off, monkeypatch)

    def refused(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("this row was refused")

    monkeypatch.setattr(off.svc.admissions, "claim", refused)
    with pytest.raises(RuntimeError, match="refused"):
        off.svc._recover_stranded_intakes()


def test_off_a_stranded_intake_binds_nothing(off: Any, monkeypatch: Any) -> None:
    seen = _stranded(off, monkeypatch)
    assert off.svc._recover_stranded_intakes() == 2
    assert {s[3] for s in seen} == {None}


# ── the Knowledge index, brought back (row 29) ───────────────────────────


class _NoVector:
    available = False


def _restored(world: Any) -> DocumentIngestionService:
    """A document of A, searchable by the documents store's record, on a
    Knowledge store that never saw it (what a restore leaves)."""
    def entered() -> Any:
        return bind_scope(world.a, world.reem) if scope.spaces_enforced() else tenant_scope(world.a)

    def on(name: str) -> str:
        store = KnowledgeStore(str(world.root / name))
        with entered():
            library = store.create_library("docs", "Documents")
        library_id = str(library["id"] if isinstance(library, dict) else getattr(library, "id", library))
        index = KnowledgeIndex(store=store)
        index._vector_stores[library_id] = _NoVector()
        world.svc.knowledge_adapter = world.svc.service.knowledge_adapter = DocumentKnowledgeAdapter(
            repository=world.svc.repository, knowledge_store=store, knowledge_index=index,
            config=world.svc._policy.initial, policy=world.svc._policy)
        return library_id

    doc = seed_document(world.svc, tenant_id=world.a, owner=world.reem, marker="turbine checklist")
    library_id = on("source.db")
    with entered():
        world.svc.index_document(tenant_id=world.a, actor_id=world.reem, document_id=doc.document_id,
                                 library_id=library_id)
    assert on("restored.db") == library_id
    return world.svc


def test_a_restored_index_is_brought_back_whoevers_turn_started_it(world: Any) -> None:
    svc = _restored(world)
    with watched((svc.knowledge_adapter, "index_document_ir")) as calls, bind_scope(world.b, world.omar):
        report = svc.reconcile_index()
    assert len(report["reindexed"]) == 1 and not report["failed"], report
    assert calls.scopes("index_document_ir") == [Scope(world.a, world.reem)], "as its owner, in its space"


def _torn(world: Any, monkeypatch: pytest.MonkeyPatch) -> DocumentIngestionService:
    """The restored index, and a second entry whose manifest is torn, first."""
    svc = _restored(world)
    real_entries = svc.repository.active_index_entries()
    torn = seed_document(svc, tenant_id=world.b, owner=world.omar, marker="torn")
    entry = (world.b, real_entries[0][1], str(torn.document_id), str(torn.version_id))
    monkeypatch.setattr(svc.repository, "active_index_entries", lambda: [entry, *real_entries])
    monkeypatch.setattr(svc.repository, "list_document_chunks", lambda **_kw: ["recorded"])
    monkeypatch.setattr(svc.knowledge_adapter.store, "get_library_for_tenant", lambda *_a: {"id": "docs"})
    manifest = next(p for p in (world.root / "documents").rglob("*") if p.is_file() and torn.version_id in str(p))
    manifest.write_text("{torn", encoding="utf-8")
    return svc


def test_one_torn_manifest_stops_no_other(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    report = _torn(world, monkeypatch).reconcile_index()
    assert len(report["failed"]) == 1 and report["failed"][0]["tenant_id"] == world.b, report
    assert len(report["reindexed"]) == 1, report


def test_off_a_torn_manifest_is_raised_as_before(off: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        _torn(off, monkeypatch).reconcile_index()


def test_a_deleted_documents_pages_are_removed_in_its_own_space(world: Any) -> None:
    """The index's other direction: a tombstoned document still searchable (a
    restored Knowledge store) is removed, whoever's turn the pass is in."""
    svc = _restored(world)
    with bind_scope(world.a, world.reem):
        svc.reconcile_index()
    [document] = {d for _t, _l, d, _v in svc.knowledge_adapter.store.document_index_entries()}
    with svc.repository._lock:
        svc.repository._conn.execute("UPDATE documents SET deleted_at = ? WHERE id = ?",
                                     ("2026-11-25T00:00:00+00:00", document))
        svc.repository._conn.execute("UPDATE document_chunks SET active = 0, index_status = 'tombstoned'")
        svc.repository._conn.commit()
    with watched((svc.knowledge_adapter.index, "unindex_document"),
                 (svc.repository, "list_indexed_libraries")) as calls, bind_scope(world.b, world.omar):
        report = svc.reconcile_index()
    assert len(report["pruned"]) == 1 and not report["failed"], report
    there = Scope(world.a, OWNER_ACCOUNT_ID)  # no person asked: the space's own account
    assert calls.scopes("unindex_document") == [there] == calls.scopes("list_indexed_libraries")


def test_one_entry_that_cannot_be_removed_stops_no_other(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reconcile's other direction: two documents the store does not
    record, the first one's removal fails."""
    svc = _restored(world)
    adapter = svc.knowledge_adapter
    entries = {(world.a, "docs", f"0000000{n}-0000-4000-8000-000000000000", "v") for n in (1, 2)}
    monkeypatch.setattr(adapter.store, "document_index_entries", lambda: entries)
    monkeypatch.setattr(svc.repository, "active_index_entries", lambda: [])
    tried: list[str] = []

    def unindex(**kw: Any) -> None:
        tried.append(kw["document_id"])
        if len(tried) == 1:
            raise RuntimeError("this one could not be removed")

    monkeypatch.setattr(adapter.index, "unindex_document", unindex)
    report = svc.reconcile_index(prune=True)
    assert len(tried) == 2 and len(report["pruned"]) == 1, report
    assert [f["code"] for f in report["failed"]] == ["RuntimeError"]


def test_an_entry_of_no_space_stops_no_other(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """An id from before Spaces that is no space: reported, never anyone's."""
    svc = _restored(world)
    real = svc.repository.active_index_entries()
    odd = ("Not A Space!", *real[0][1:])
    monkeypatch.setattr(svc.repository, "active_index_entries", lambda: [odd, *real])
    monkeypatch.setattr(svc.repository, "list_document_chunks", lambda **_kw: ["held"])
    monkeypatch.setattr(svc.repository, "get_document", lambda real=svc.repository.get_document, **kw: real(
        **{**kw, "tenant_id": world.a}))
    monkeypatch.setattr(svc.knowledge_adapter.store, "get_library_for_tenant", lambda *_a: {"id": "docs"})
    monkeypatch.setattr(svc.storage, "for_space", lambda _t, real=svc.storage.for_space: real(world.a))
    report = svc.reconcile_index()
    assert [f["code"] for f in report["failed"]] == ["no_space"] and len(report["reindexed"]) == 1, report


def test_off_the_index_is_brought_back_with_nothing_bound(off: Any) -> None:
    svc = _restored(off)
    with watched((svc.knowledge_adapter, "index_document_ir")) as calls:
        report = svc.reconcile_index()
    assert len(report["reindexed"]) == 1, report
    assert calls.scopes("index_document_ir") == [None]
