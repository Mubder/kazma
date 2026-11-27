"""The product's own flows with the second lock armed (Spaces S4-01 step 3).

Spaces enforced and the lock armed, on the throwaway Postgres with the rules
in place: each checkout of the shared pool goes through its space's door
(the space pool, signed in as ``kazma_space``), the install's, or none
(refused). Every flow here is seeded as a request would seed it -- bound to
the person's space -- so a refusal is a product path that does not say whose
statements it runs; what each flow reads is checked against what its space
may see. Documents first, then the swarm and the chats (the review's order).

Needs ``KAZMA_DATABASE_URL`` (a throwaway server whose login owns its tables
and may make roles) and ``KAZMA_TEST_ALLOW_REAL_DB=1``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import bind_scope

try:
    import psycopg
except ImportError:  # CI's light job has no psycopg: collected and skipped, so the counts agree
    psycopg = None

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="psycopg is not installed")]


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Spaces enforced, the rules in place, the lock armed, one shared pool."""
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    from kazma_core.db import row_security
    from kazma_core.db.postgres_pool import PostgresPool

    from tests.test_pg_app_role import _every_store_set_up

    # As a start: the stores' schema, then the rules, then arming. In an armed
    # run (tests/_second_lock_armed.py) the setup's writes would otherwise go
    # through a space door before this test's rules are back in place (the
    # last test's teardown took the space role's rights).
    monkeypatch.setattr(row_security, "_armed", False)
    _every_store_set_up(dsn)
    monkeypatch.setattr(row_security, "_in_place", row_security.in_place())  # given back after the test
    # Every table's rule, but not the checkpoints' backfill: the session's
    # thread records are its own data folder's, not the shared test database's,
    # and a whole pass reads a missing or empty store as unresolved there
    # (step 4 review M1; tests/_second_lock_armed.py does the same).
    done = row_security.apply(dsn, tables=list(row_security.rules("")), vector_table="")
    if done.refused:
        pytest.skip(f"the rules cannot be put in place here: {done.refused}")
    assert done.complete, done.left
    pool = PostgresPool(dsn, min_size=1, max_size=4)
    monkeypatch.setattr("kazma_core.db.postgres_pool.get_postgres_pool", lambda: pool)
    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(row_security, "_armed", True)
    try:
        yield pool
    finally:
        pool.close()
        # The space role's rights in this shared database go with the test:
        # the role is the server's, and a later drop of it (the remove file's
        # drill) must find no right left here.
        with psycopg.connect(dsn, autocommit=True) as conn:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kazma_space'").fetchone():
                conn.execute("DROP OWNED BY kazma_space")


def _install(statement: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """The test's own statement: the install's (a test is no space)."""
    from kazma_core.db.postgres_pool import get_postgres_pool
    from kazma_core.spaces.row_work import install_statements

    with install_statements("the test's own setup and checks"):
        return get_postgres_pool().execute(statement, params or None)


# ── documents ────────────────────────────────────────────────────────────


@pytest.fixture
def documents(armed: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Any]:
    """Two spaces, a person in each, a document uploaded in each -- as the
    upload route would, bound to that person's space."""
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import identity, people
    from kazma_core.spaces.row_work import install_statements
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    from tests._documents_seed import seed_document, seeded_service

    monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "postgres")
    monkeypatch.setenv("KAZMA_DOCUMENTS_JOBS_BACKEND", "postgres")
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    with install_statements("the service's start: its stores' schema and its workers"):
        svc = seeded_service(tmp_path / "documents")
    world = SimpleNamespace(svc=svc, pool=armed, jobs={})
    for key in ("a", "b"):
        who = people.create_person(f"P{uuid.uuid4().hex[:8]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        space = people.create_shared_space(key.upper(), by=OWNER_ACCOUNT_ID)["id"]
        people.add_member(space, who, by=OWNER_ACCOUNT_ID)
        with bind_scope(space, who):  # the upload request
            world.jobs[key] = seed_document(svc, tenant_id=space, owner=who, marker=f"{key} notes")
        setattr(world, key, space)
        setattr(world, f"person_{key}", who)
    try:
        yield world
    finally:
        svc.close()
        _install("DELETE FROM document_jobs WHERE tenant_id IN (%s, %s)", (world.a, world.b))
        store.delete(identity.REGISTRY_KEY)


def test_each_space_lists_its_own_documents(documents: Any) -> None:
    w = documents
    with bind_scope(w.a, w.person_a):
        mine = w.svc.repository.list_documents(tenant_id=w.a)
        theirs = w.svc.repository.list_documents(tenant_id=w.b)  # a forgotten filter: B's id asked from A
    assert [d.id for d in mine] == [w.jobs["a"].document_id]
    assert theirs == [], "another space's documents are not there for A's statements"


def test_not_armed_a_forgotten_filter_reads_another_spaces_document(documents: Any,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    # The control: the same statement as the owner, as Kazma signs in today.
    from kazma_core.db import row_security

    w = documents
    monkeypatch.setattr(row_security, "_armed", False)
    with bind_scope(w.a, w.person_a):
        assert [d.id for d in w.svc.repository.list_documents(tenant_id=w.b)] == [w.jobs["b"].document_id]


def test_the_worker_claims_as_the_install_and_works_in_the_jobs_space(documents: Any) -> None:
    import asyncio

    from kazma_core.documents.jobs import DocumentJobState
    from kazma_core.documents.worker import DocumentWorker, PermanentDocumentError

    w = documents
    _install("UPDATE document_jobs SET state = 'ready_to_parse', stage = 'ready_to_parse' WHERE id = %s",
             (w.jobs["a"].job_id,))
    seen: list[Any] = []

    def stage(job: Any, _context: Any) -> Any:
        seen.append(w.svc.repository.get_document(tenant_id=job.tenant_id, document_id=job.document_id))
        raise PermanentDocumentError("end_of_test", "the stage was reached")

    with bind_scope(w.b, w.person_b):  # the workers were started in B's turn
        claimed = w.svc.jobs.claim_next(owner="w", lease_seconds=60, tenant_id=w.a)
        asyncio.run(DocumentWorker(w.svc.jobs, {s: stage for s in DocumentJobState})._process_claimed(claimed, "w"))
    assert claimed is not None and seen and seen[0] is not None, "the stage read its own space's document"


def test_recoveries_counts_leaving_and_collection_run_armed(documents: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import time

    from kazma_core.documents.ingestion import set_ingestion_service
    from kazma_core.runs.adapters.documents import ADAPTER
    from kazma_core.spaces import people, space_limits

    w = documents
    monkeypatch.setattr(w.svc, "_advance_intake", lambda job, **_kw: job)
    with bind_scope(w.b, w.person_b):
        w.svc.recover_interrupted_intake()
        w.svc._recover_stranded_intakes()
    set_ingestion_service(w.svc)
    try:
        with bind_scope("default", "owner"):  # the admin's page
            assert space_limits._documents_used(w.b) == len("b notes")
            assert w.svc._storage_bytes()[0] >= len("a notes") + len("b notes")
            assert w.svc.jobs.queue_stats().depth >= 0
            w.svc.gc.collect(dry_run=True)
            w.svc.grant_member_access(tenant_id=w.b, member=w.person_a, force=True)
            people._after_leaving(w.b, w.person_a)
        asyncio.run(ADAPTER.list_runs(time.time(), window_s=3600))
    finally:
        set_ingestion_service(None)


def test_a_moved_document_is_in_the_target_space_only(documents: Any) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.documents.ingestion import MOVES_KEY
    from kazma_core.spaces import people

    w = documents
    people.add_member(w.b, w.person_a, by="owner")
    doc = w.jobs["a"].document_id
    with bind_scope(w.a, w.person_a):
        moved = w.svc.move_document(document_id=doc, from_tenant=w.a, to_tenant=w.b,
                                    actor_id=w.person_a, workspace_id="w", taken_out=False)
    get_config_store().delete(MOVES_KEY)
    with bind_scope(w.b, w.person_a):
        assert moved["document_id"] in [str(d.id) for d in w.svc.repository.list_documents(tenant_id=w.b)]
    with bind_scope(w.a, w.person_a):
        assert doc not in [str(d.id) for d in w.svc.repository.list_documents(tenant_id=w.a)], "never two copies"


# ── the swarm ────────────────────────────────────────────────────────────


def test_the_swarm_keeps_each_task_in_its_space_and_recovers_as_the_install(armed: Any, tmp_path: Any) -> None:
    from kazma_core.swarm import SwarmTask, TaskStatus
    from kazma_core.swarm.task_store import TaskStore

    a, b = (f"s{uuid.uuid4().hex[:12]}" for _ in range(2))
    with bind_scope(b, "omar"):  # a tool's own store, made in a turn
        store = TaskStore(str(tmp_path / "none.db"))
    tasks = {}
    for space, who in ((a, "reem"), (b, "omar")):
        task = SwarmTask(prompt=f"the {space} task", id=f"task-{uuid.uuid4().hex[:12]}", status=TaskStatus.RUNNING,
                         metadata={"tenant_id": space})
        with bind_scope(space, who):  # the turn that started it
            assert store.persist_task(task)
        tasks[space] = task
    try:
        with bind_scope(a, "reem"):
            assert store.get_task(tasks[a].id) is not None
            assert store.get_task(tasks[b].id) is None, "another space's task by its id alone"
            listed = {t.id for t in store.list_tasks(page_size=500)}
            assert tasks[a].id in listed and tasks[b].id not in listed
        # A start, nothing bound: the install lists, each task is written in its own space.
        recovered = store.requeue_orphaned_running()
        store.get_paused_tasks()
        store.prune_tasks(retention_days=3650)
        assert {tasks[a].id, tasks[b].id} <= set(sum(recovered.values(), []))
    finally:
        for task in tasks.values():
            _install("DELETE FROM kazma_swarm_tasks WHERE id = %s", (task.id,))


# ── the chats ────────────────────────────────────────────────────────────


def test_chats_are_kept_in_their_space_and_a_handover_writes_the_target(armed: Any, tmp_path: Any) -> None:
    from kazma_ui import session_manager as sm

    a, b = (f"s{uuid.uuid4().hex[:12]}" for _ in range(2))
    made = sm.SessionManager(db_path=str(tmp_path / "chats.db"))  # its start reads every space's chats
    assert made._pg
    messages = [{"role": "user", "content": "the trip", "ts": "2026-11-25T00:00:00+00:00"}]
    try:
        with bind_scope(a, "reem"):
            made.put(sm.ChatSession(session_id=f"chat-{a}", tenant_id=a, thread_id=f"t-{a}", messages=messages))
        with bind_scope(b, "omar"):
            made.put(sm.ChatSession(session_id=f"chat-{b}", tenant_id=b, thread_id=f"t-{b}", messages=messages))
            assert [s.session_id for s in made.list_all()] == [f"chat-{b}"]
        copy = sm.ChatSession(session_id=f"copy-{b}", tenant_id=b, thread_id=f"t-{b}2", messages=messages)
        with bind_scope(a, "reem"):
            assert made.hand_over(f"chat-{a}", messages=messages, to=copy, commit=lambda: True) is True
        again = sm.SessionManager(db_path=str(tmp_path / "chats2.db"))  # a restart reads them all back
        with bind_scope(b, "omar"):
            assert {s.session_id for s in again.list_all()} == {f"chat-{b}", f"copy-{b}"}
        with bind_scope(a, "reem"):
            assert again.get(f"copy-{b}") is None
            made.delete(f"chat-{a}")
    finally:
        _install("DELETE FROM kazma_chat_sessions WHERE tenant_id IN (%s, %s)", (a, b))


# ── the memory mirror on Kazma's own database ────────────────────────────


def test_the_mirror_on_kazmas_database_takes_the_shared_door(armed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.memory.state_backend import PostgresStateBackend

    dsn = os.environ["KAZMA_DATABASE_URL"]
    a, b = (f"s{uuid.uuid4().hex[:12]}" for _ in range(2))
    mirror = PostgresStateBackend(dsn)
    facts = {space: f"s401-{uuid.uuid4().hex[:12]}" for space in (a, b)}
    try:
        for space, who in ((a, "reem"), (b, "omar")):
            with bind_scope(space, who):  # a turn's own facts
                assert mirror.mirror_belief({"id": facts[space], "tenant_id": space, "subject": "user",
                                             "predicate": "lives_in", "object": space}) is True
        with bind_scope(a, "reem"):
            assert [r["id"] for r in mirror.fetch_beliefs([facts[a], facts[b]], tenant_id=a)] == [facts[a]]
            assert mirror.fetch_beliefs([facts[b]], tenant_id=b) == [], "B's fact, asked for from A"
            conn = mirror._connect()  # the door held: the space role, in A
            try:
                assert conn.execute("SELECT current_user::text").fetchone()[0] == "kazma_space"
            finally:
                conn.close()
        held = _install("SELECT id FROM kazma_beliefs WHERE id = ANY(%s)", (list(facts.values()),))
        assert {r["id"] for r in held} == set(facts.values())
        with bind_scope(b, "omar"):
            assert mirror.delete_belief(facts[b], tenant_id=b) is True
    finally:
        _install("DELETE FROM kazma_beliefs WHERE id = ANY(%s)", (list(facts.values()),))
        mirror.close()


# ── the step 3 review ────────────────────────────────────────────────────


def test_a_settings_read_inside_a_spaces_checkout_answers(armed: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """M2: a cold settings read inside a space checkout was refused as a
    nested install checkout, and the mirror, which reads its conflict policy
    inside its held connection, fell back to last_write_wins while the
    setting said fail_closed."""
    from kazma_core.config_store import get_config_store
    from kazma_core.memory.state_backend import state_conflict_policy

    store = get_config_store()
    if not store._use_postgres():
        pytest.skip("the settings are on Postgres only with KAZMA_DB_BACKEND=postgres")
    key = "memory.backends.state.conflict_policy"
    store.set(key, "fail_closed")
    space = f"s{uuid.uuid4().hex[:12]}"
    try:
        store._clear_cache()  # cold: as 15 s after a write elsewhere
        with bind_scope(space, "owner"), armed.connection():  # a space checkout held, as the mirror holds one
            assert state_conflict_policy() == "fail_closed"
            assert store.get(key) == "fail_closed"
    finally:
        store.delete(key)
