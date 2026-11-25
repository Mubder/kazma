"""Whose each statement is, at the real Postgres pool's checkout (S4-01 step 0a).

``tests/_space_door.py`` asks at ``PostgresPool.connection`` what the second
lock will ask there. Each test fails on the code before step 0a, where the
checkout answers nothing, or the space of whoever started the work.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import bind_scope

from tests._space_door import Calls, watched_pool

pytestmark = pytest.mark.postgres


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    dsn = os.environ.get("KAZMA_DATABASE_URL") or ""
    if not dsn or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (scripts/postgres_suite.py)")
    pytest.importorskip("psycopg")
    from kazma_core.db.postgres_pool import PostgresPool

    made = PostgresPool(dsn, min_size=1, max_size=3)
    monkeypatch.setattr("kazma_core.db.postgres_pool.get_postgres_pool", lambda: made)
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


def _said(calls: Calls) -> set[str]:
    return {c.space for c in calls}


# ── probes and schema ────────────────────────────────────────────────────


@pytest.mark.usefixtures("enforced")
def test_probes_and_schemas_are_the_installs(pool: Any) -> None:
    from kazma_core.db import pg_backup
    from kazma_core.documents.audit import DocumentAuditStore
    from kazma_core.documents.jobs_pg import PostgresDocumentJobRepository
    from kazma_core.documents.repository_pg import PostgresDocumentRepository
    from kazma_core.security import service_exposure

    for bound in (False, True):
        with watched_pool() as calls:
            def probe() -> None:
                documents = PostgresDocumentRepository(pool, tenant_quota_bytes=1_000_000)
                PostgresDocumentJobRepository(pool)
                DocumentAuditStore(documents)
                assert "documents" not in (pg_backup.verify_required_pg_tables(pool) or [])
                assert service_exposure._role_answer()["rolname"]

            if bound:
                with bind_scope("family", "layla"):
                    probe()
            else:
                probe()
        assert len(calls) >= 5 and _said(calls) == {"<install>"}


def test_a_real_refusal_is_one_and_an_away_server_is_not(pool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.db import pg_helpers
    from kazma_core.db.postgres_pool import PostgresPool

    def refused() -> BaseException:
        try:
            with pool.connection() as conn:
                conn.execute("SET LOCAL ROLE pg_read_all_settings")
                conn.execute("SELECT rolpassword FROM pg_authid")
        except Exception as exc:
            return exc
        raise AssertionError("Postgres allowed it")

    exc = refused()
    assert not pg_helpers._space_refusal(exc), "Spaces off: as before"
    monkeypatch.setattr(scope, "_enforced", True)
    assert pg_helpers._space_refusal(exc)
    with pytest.raises(Exception) as away:
        with PostgresPool("postgresql://demo@127.0.0.1:1/none", min_size=0, max_size=1).connection(timeout=2):
            pass
    assert not pg_helpers._space_refusal(away.value)


# ── documents ────────────────────────────────────────────────────────────


@pytest.fixture
def documents(pool: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Any]:
    """Two spaces' documents on real Postgres: catalog, queue and intake."""
    import uuid
    from types import SimpleNamespace

    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import identity, people
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    from tests._documents_seed import seed_document, seeded_service

    monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "postgres")
    monkeypatch.setenv("KAZMA_DOCUMENTS_JOBS_BACKEND", "postgres")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    made = SimpleNamespace(svc=seeded_service(tmp_path / "documents"), pool=pool, jobs={})
    assert made.svc.repository.backend_name == "postgres" and made.svc.jobs._pool is pool
    for key in ("a", "b"):
        who = people.create_person(f"P{uuid.uuid4().hex[:8]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)["id"]
        space = people.create_shared_space(key.upper(), by=OWNER_ACCOUNT_ID)["id"]
        people.add_member(space, who, by=OWNER_ACCOUNT_ID)
        seeded = seed_document(made.svc, tenant_id=space, owner=who, marker=f"{key} notes")
        setattr(made, key, space)
        setattr(made, f"person_{key}", who)
        made.jobs[key] = seeded
    try:
        yield made
    finally:
        made.svc.close()
        for space in (made.a, made.b):
            pool.execute("DELETE FROM document_jobs WHERE tenant_id = %s", (space,))
        store.delete(identity.REGISTRY_KEY)


def _ready_to_parse(world: Any, key: str) -> None:
    world.pool.execute("UPDATE document_jobs SET state = 'ready_to_parse', stage = 'ready_to_parse' WHERE id = %s",
                       (world.jobs[key].job_id,))


def _on(world: Any, calls: Calls) -> list[str]:
    """What each checkout of the documents' own pool said (the settings store
    has its own pool, outside the second lock)."""
    return [c.space for c in calls if c.args[0] is world.pool]


def test_the_claim_is_the_installs_and_the_jobs_checkouts_its_spaces(documents: Any) -> None:
    import asyncio

    from kazma_core.documents.jobs import DocumentJobState
    from kazma_core.documents.worker import DocumentWorker, PermanentDocumentError

    w = documents
    _ready_to_parse(w, "a")

    def stage(job: Any, _context: Any) -> Any:
        w.svc.repository.get_document(tenant_id=job.tenant_id, document_id=job.document_id)
        raise PermanentDocumentError("end_of_test", "the stage was reached")

    with bind_scope(w.b, w.person_b):  # the workers were started in B's turn
        with watched_pool() as claim:
            claimed = w.svc.jobs.claim_next(owner="w", lease_seconds=60, tenant_id=w.a)
        with watched_pool() as work:
            asyncio.run(DocumentWorker(w.svc.jobs, {s: stage for s in DocumentJobState})._process_claimed(claimed, "w"))
    assert _on(w, claim) and set(_on(w, claim)) == {"<install>"}
    assert len(_on(w, work)) >= 3 and set(_on(w, work)) == {w.a}


def test_recoveries_read_each_row_in_its_own_space(documents: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    w = documents
    monkeypatch.setattr(w.svc, "_advance_intake", lambda job, **_kw: job)
    with watched_pool() as calls, bind_scope(w.b, w.person_b):
        w.svc.recover_interrupted_intake()
        w.svc._recover_stranded_intakes()
    said = _on(w, calls)
    assert {w.a, w.b, "<install>"} <= set(said) and "" not in said
    # Both jobs are looked up in their own space, whoever's turn this was.
    assert said.count(w.a) >= 2


def test_counts_leaving_and_the_collection_say_whose_they_are(documents: Any) -> None:
    import asyncio
    import time

    from kazma_core.documents.ingestion import set_ingestion_service
    from kazma_core.runs.adapters.documents import ADAPTER
    from kazma_core.spaces import people, space_limits

    w = documents
    set_ingestion_service(w.svc)
    try:
        with bind_scope("default", "owner"):  # the admin's page
            with watched_pool() as used:
                assert space_limits._documents_used(w.b) == len("b notes")
            with watched_pool() as totals:
                assert w.svc._storage_bytes()[0] >= len("a notes") + len("b notes")
            with watched_pool() as depth:
                assert w.svc.jobs.queue_stats().depth >= 0
            with watched_pool() as own_depth:
                w.svc.jobs.queue_stats(tenant_id="default")
            with watched_pool() as collection:
                w.svc.gc.collect(dry_run=True)
            w.svc.grant_member_access(tenant_id=w.b, member=w.person_a, force=True)
            with watched_pool() as leaving:
                people._after_leaving(w.b, w.person_a)
        with watched_pool() as listed:
            asyncio.run(ADAPTER.list_runs(time.time(), window_s=3600))
    finally:
        set_ingestion_service(None)
    assert set(_on(w, used)) == {w.b}
    assert set(_on(w, totals)) == set(_on(w, collection)) == set(_on(w, depth)) == {"<install>"}
    assert set(_on(w, own_depth)) == {"default"}, "a tenant's own depth is the bound space's"
    assert _on(w, leaving) and set(_on(w, leaving)) == {w.b}
    # Every space's jobs (the database may hold other tests' too), each title
    # read as its own space's.
    assert {w.a, w.b, "<install>"} <= set(_on(w, listed)) and "" not in _on(w, listed)


def test_a_moves_copy_is_written_in_the_target(documents: Any) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.documents.ingestion import MOVES_KEY
    from kazma_core.spaces import people

    w = documents
    people.add_member(w.b, w.person_a, by="owner")
    with watched_pool() as calls, bind_scope(w.a, w.person_a):
        w.svc.move_document(document_id=w.jobs["a"].document_id, from_tenant=w.a, to_tenant=w.b,
                            actor_id=w.person_a, workspace_id="w", taken_out=False)
    get_config_store().delete(MOVES_KEY)
    assert {w.a, w.b} <= set(_on(w, calls)) and "" not in _on(w, calls)
    there = {c.scope.asker for c in calls if c.args[0] is w.pool and c.scope is not None and c.scope.space_id == w.b}
    assert there == {w.person_a}, "as the person who moves it, never the space's maker"
