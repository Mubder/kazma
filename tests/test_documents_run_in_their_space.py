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
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.db import availability
from kazma_core.documents import ingestion
from kazma_core.documents.ingestion import DocumentIngestionService
from kazma_core.documents.jobs import DocumentJobRepository
from kazma_core.documents.knowledge import DocumentKnowledgeAdapter
from kazma_core.documents.models import DocumentJobState
from kazma_core.documents.worker import DocumentWorker, PermanentDocumentError
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.row_work import current_door
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
            (name, row["tenant_id"], getattr(current_door(), "space", None), current_scope())) or True)
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


def test_off_the_index_is_brought_back_with_nothing_bound(off: Any) -> None:
    svc = _restored(off)
    with watched((svc.knowledge_adapter, "index_document_ir")) as calls:
        report = svc.reconcile_index()
    assert len(report["reindexed"]) == 1, report
    assert calls.scopes("index_document_ir") == [None]
