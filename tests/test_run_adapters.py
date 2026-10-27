"""Each run adapter reads its kind's REAL store and reports every state right.

Phase 1 of ``docs/plans/RUN_SUPERVISION_PLAN.md``: the registry is a read
model, so what it says must be what the store holds. Each kind is seeded in
its own store -- the swarm task store, ``cron.db``, the document job queue,
``research_sessions.db``, the X publication store, the memory task queue --
with one row per native status, and the registry's answer is compared with an
EXPECTED table written here, independently of the adapters' own maps. The
negative control swaps one adapter mapping and the comparison catches it.
"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from kazma_core.runs import registry
from kazma_core.runs.contract import RunState, Verdict

NOW = time.time()
#: An hour ago, as the stores write it (inside the registry's one-day window).
HOUR_AGO = datetime.fromtimestamp(NOW - 3600, UTC).isoformat()


def _listing(kind: str):
    return asyncio.run(registry.list_runs(kinds=(kind,), now=NOW))


def _by_native(kind: str) -> dict[str, object]:
    listing = _listing(kind)
    assert not listing.errors, listing.errors
    return {r.native_state: r for r in listing.runs}


def _check(kind: str, expected: dict[str, RunState]) -> None:
    runs = _by_native(kind)
    assert set(runs) == set(expected), (sorted(runs), sorted(expected))
    for native, state in expected.items():
        run = runs[native]
        assert run.kind == kind
        # A stalled/dead run shows as stalled; compare the adapter's own state.
        shown = run.state if run.verdict not in (Verdict.STALLED, Verdict.DEAD, Verdict.SLOW) else (
            registry.adapter_for(kind).STATE_MAP[native])
        assert RunState(shown) == state, f"{kind} {native}: {shown} != {state}"


# ── swarm ────────────────────────────────────────────────────────────────

SWARM_EXPECTED = {
    "pending": RunState.QUEUED, "running": RunState.RUNNING, "paused": RunState.WAITING_HUMAN,
    "completed": RunState.DONE, "failed": RunState.FAILED, "timeout": RunState.FAILED,
    "cancelled": RunState.CANCELLED,
}


@pytest.fixture
def swarm_store(tmp_path, monkeypatch):
    from kazma_core.swarm.task import SwarmTask, TaskStatus
    from kazma_core.swarm.task_store import TaskStore

    store = TaskStore(str(tmp_path / "swarm.db"))
    stamp = HOUR_AGO
    for status in TaskStatus:
        task = SwarmTask(prompt=f"task {status.value}", status=status, created_at=stamp, started_at=stamp,
                         metadata={"source_delivery_target": "telegram:42", "source_thread_id": "gw-1"})
        if status.value in ("completed", "failed", "timeout", "cancelled"):
            task.completed_at = stamp
        assert store.persist_task(task)
    running = store.list_tasks(status="running")[0]
    engine = SimpleNamespace(_task_store=store, _active_tasks={running.id: running})
    monkeypatch.setattr("kazma_core.runs.adapters.swarm._engine", lambda: engine)
    yield store
    store.close()


def test_swarm_tasks_are_read_from_the_task_store(swarm_store):
    _check("swarm_task", SWARM_EXPECTED)
    runs = _by_native("swarm_task")
    assert runs["running"].owner_alive is True
    assert runs["running"].origin == {"platform": "telegram", "target": "telegram:42", "thread_id": "gw-1"}
    assert runs["running"].actions == ("cancel",)
    assert runs["failed"].actions == ("retry",)


def test_a_running_row_no_engine_owns_is_dead(swarm_store, monkeypatch):
    """A RUNNING row left by a process that died: no owner here."""
    engine = SimpleNamespace(_task_store=swarm_store, _active_tasks={})
    monkeypatch.setattr("kazma_core.runs.adapters.swarm._engine", lambda: engine)
    run = _by_native("swarm_task")["running"]
    assert run.owner_alive is False
    assert run.verdict == Verdict.DEAD
    assert run.actions == ()


def test_a_task_with_unknown_effects_offers_only_the_checked_retry(swarm_store, monkeypatch):
    from kazma_core.swarm.task import SwarmTask, TaskStatus

    task = SwarmTask(prompt="sent mail?", status=TaskStatus.FAILED, created_at=HOUR_AGO,
                     completed_at=HOUR_AGO,
                     metadata={"execution_outcome": "unknown", "invoked_tools": ["email_send"]})
    swarm_store.persist_task(task)
    run = next(r for r in _listing("swarm_task").runs if r.native_id == task.id)
    assert run.effects == "unknown"
    assert run.actions == ("retry_checked",)


def test_a_wrong_mapping_is_caught(swarm_store, monkeypatch):
    """Negative control: the comparison fails when an adapter maps one state wrong."""
    adapter = registry.adapter_for("swarm_task")
    monkeypatch.setitem(adapter.STATE_MAP, "timeout", RunState.DONE)
    with pytest.raises(AssertionError):
        _check("swarm_task", SWARM_EXPECTED)


# ── scheduled jobs ───────────────────────────────────────────────────────

CRON_EXPECTED = {
    "running": RunState.RUNNING, "delivering": RunState.RUNNING, "done": RunState.DONE,
    "failed": RunState.FAILED, "cancelled": RunState.CANCELLED, "undeliverable": RunState.FAILED,
    "missed": RunState.FAILED, "delivery_unknown": RunState.UNKNOWN,
}


def test_scheduled_jobs_are_read_from_cron_db(tmp_path, monkeypatch):
    from kazma_core.cron.store import JobStatus, ScheduledJob, SQLiteCronStore

    async def seed():
        store = SQLiteCronStore(str(tmp_path / "cron.db"))
        await store.init()
        finished = HOUR_AGO
        for status in JobStatus:
            job = ScheduledJob(job_id=f"job-{status.value}", timing="in 5m", prompt=f"prompt {status.value}",
                               platform="telegram", thread_id="gw-1", status=status,
                               delivery_target="telegram:42")
            await store.insert(job)
            await store._conn().execute(
                "UPDATE cron_jobs SET status = ?, finished_at = ?, last_fire_at = ?, lease_until = ? "
                "WHERE job_id = ?",
                (status.value, finished if status.value not in ("pending", "running", "delivering") else "",
                 finished, NOW + 300, job.job_id))
        return store

    # aiosqlite connections are loop-bound: seed and read on one loop.
    async def scenario():
        cron_store = await seed()
        # A scheduler that does not own the seeded fire: its lease decides.
        monkeypatch.setattr("kazma_core.cron.scheduler.get_cron_scheduler",
                            lambda: SimpleNamespace(_store=cron_store, fire_owner_alive=lambda job: None))
        listing = await registry.list_runs(kinds=("scheduled_job",), now=NOW)
        await cron_store.close()
        return listing

    listing = asyncio.run(scenario())
    assert not listing.errors
    runs = {r.native_state: r for r in listing.runs}
    assert "pending" not in runs  # a job waiting for its time is a plan, not a run
    assert {k: RunState(registry.adapter_for("scheduled_job").STATE_MAP[k]) for k in runs} == CRON_EXPECTED
    assert runs["running"].owner_alive is True and runs["running"].actions == ("cancel",)
    assert runs["running"].origin["target"] == "telegram:42"


# ── document jobs ────────────────────────────────────────────────────────

DOC_EXPECTED = {
    "received": RunState.QUEUED, "quarantined": RunState.QUEUED, "validating": RunState.RUNNING,
    "ready_to_parse": RunState.QUEUED, "parsing": RunState.RUNNING, "ocr_required": RunState.QUEUED,
    "ocr_running": RunState.RUNNING, "normalizing": RunState.RUNNING, "indexing": RunState.RUNNING,
    "verifying": RunState.RUNNING, "ready": RunState.DONE, "retry_wait": RunState.QUEUED,
    "rejected": RunState.FAILED, "cancelled": RunState.CANCELLED, "dead_letter": RunState.FAILED,
}


def test_document_jobs_are_read_from_the_job_queue(tmp_path, monkeypatch):
    from kazma_core.documents.jobs import DocumentJobRepository
    from kazma_core.documents.models import DocumentJobState
    from kazma_core.documents.repository import DocumentRepository

    metadata = DocumentRepository(tmp_path / "docs.db", tenant_quota_bytes=1_000_000)
    jobs = DocumentJobRepository(metadata, jitter=lambda _b: 0)
    for state in DocumentJobState:
        document = metadata.create_document(tenant_id="t", owner_id="o", title=f"doc {state.value}.pdf")
        payload = state.value.encode()
        digest = hashlib.sha256(payload).hexdigest()
        blob = metadata.register_blob(tenant_id="t", sha256=digest, byte_size=len(payload), storage_kind="originals")
        version = metadata.create_version(tenant_id="t", document_id=document.id, actor_id="o",
                                          source_blob_id=blob.id, source_sha256=digest,
                                          original_filename="d.pdf", mime_type="application/pdf")
        job = jobs.enqueue(tenant_id="t", workspace_id="w", document_id=document.id, version_id=version.id,
                           idempotency_key=state.value, max_attempts=3)
        with metadata._lock:
            metadata._conn.execute("UPDATE document_jobs SET state = ? WHERE id = ?", (state.value, str(job.id)))
            metadata._conn.commit()
    service = SimpleNamespace(jobs=jobs, repository=metadata)
    monkeypatch.setattr("kazma_core.documents.ingestion.peek_ingestion_service", lambda: service)
    _check("document_job", DOC_EXPECTED)
    runs = _by_native("document_job")
    assert runs["dead_letter"].actions == ("retry",)
    assert runs["parsing"].actions == ("cancel",)
    assert runs["parsing"].title == "doc parsing.pdf"
    assert runs["parsing"].owner_alive is False  # no lease: a processing job nothing holds


# ── research sessions and crawls ─────────────────────────────────────────

SESSION_EXPECTED = {
    "pending": RunState.QUEUED, "running": RunState.RUNNING, "cancelling": RunState.RUNNING,
    "cancelled": RunState.CANCELLED, "done": RunState.DONE, "partial": RunState.DONE,
    "error": RunState.FAILED, "interrupted": RunState.FAILED,
}


def test_research_sessions_are_read_from_their_store(tmp_path, monkeypatch):
    from kazma_core.tools import research_session

    monkeypatch.setattr(research_session, "_db_path", lambda: tmp_path / "research.db")
    monkeypatch.setattr("kazma_core.web_acquire.monitor._read", lambda: {"jobs": {}})
    conn = research_session._conn()
    try:
        for status in SESSION_EXPECTED:
            conn.execute("INSERT INTO research_sessions (id, topic, depth, status, stage, sources, created_at, "
                         "updated_at, error) VALUES (?, ?, 'deep', ?, 'search', 3, ?, ?, ?)",
                         (f"s-{status}", f"topic {status}", status, NOW - 60, NOW - 30,
                          "boom" if status == "error" else ""))
        conn.commit()
    finally:
        conn.close()
    _check("research_session", SESSION_EXPECTED)
    runs = _by_native("research_session")
    assert runs["running"].owner_alive is False  # nothing in this process runs it
    assert runs["error"].detail == "boom"


def test_crawls_are_read_from_the_crawl_journal(monkeypatch):
    rows = {f"j{i}": {"job_id": f"j{i}", "status": status, "kind": "site", "library_id": "lib",
                      "seed": "https://example.com", "pages": [], "fetched": 2, "failed": 0, "owner": None,
                      "claim": "", "created_at": NOW - 60, "updated_at": NOW - 10,
                      "progress": {"phase": "fetching"}}
            for i, status in enumerate(("running", "completed", "partial", "failed", "interrupted"))}
    monkeypatch.setattr("kazma_core.web_acquire.crawl_jobs._read", lambda: (None, rows, 0))
    monkeypatch.setattr("kazma_core.web_acquire.monitor._read", lambda: {"jobs": {}})
    _check("crawl", {"running": RunState.RUNNING, "completed": RunState.DONE, "partial": RunState.DONE,
                     "failed": RunState.FAILED, "interrupted": RunState.FAILED})


def test_an_agents_own_crawl_is_announced_by_that_agent(monkeypatch):
    row = {"job_id": "r1", "status": "failed", "kind": "research_crawl", "library_id": "", "seed": "x",
           "pages": [], "fetched": 0, "failed": 1, "owner": None, "claim": "", "created_at": NOW,
           "updated_at": NOW, "progress": {}}
    monkeypatch.setattr("kazma_core.web_acquire.crawl_jobs._read", lambda: (None, {"r1": row}, 0))
    monkeypatch.setattr("kazma_core.web_acquire.monitor._read", lambda: {"jobs": {}})
    run = _listing("crawl").runs[0]
    assert run.announced_by


# ── X posts ──────────────────────────────────────────────────────────────

X_EXPECTED = {
    "sending": RunState.RUNNING, "awaiting_approval": RunState.WAITING_HUMAN,
    "outcome_unknown": RunState.UNKNOWN, "failed_permanent": RunState.FAILED, "expired": RunState.FAILED,
    "published": RunState.DONE, "cancelled": RunState.CANCELLED,
}


def _x_store(tmp_path, states, *, tenant="t"):
    from kazma_core.x_api.publication_store import _PublicationStore

    store = _PublicationStore(tmp_path / "x.db")
    with store._connection(transaction=True) as conn:
        for i, state in enumerate(states):
            conn.execute(
                "INSERT INTO x_operations (id, tenant_id, account_id, credential_revision, idempotency_key, "
                "payload_hash, text, text_hash, origin, state, due_at, created_at, updated_at, lease_until) "
                "VALUES (?, ?, 'a', 'r', ?, 'h', ?, 'th', 'schedule', ?, ?, ?, ?, ?)",
                (f"op{i}", tenant, f"k{i}", f"post {state}", state, NOW, NOW - 60, NOW - 30, NOW + 60))
    return store


def test_x_operations_are_read_from_the_publication_store(tmp_path, monkeypatch):
    store = _x_store(tmp_path, [*X_EXPECTED, "scheduled", "deferred"])
    monkeypatch.setattr("kazma_core.runs.adapters.x_posts._store_and_tenants", lambda: (store, ["t"]))
    _check("x_post", X_EXPECTED)  # a booking waiting for its time is not a run
    runs = _by_native("x_post")
    assert runs["sending"].effects == "unknown" and runs["sending"].owner_alive is True
    assert runs["awaiting_approval"].actions == ("cancel",)


def test_x_operations_are_read_with_no_request_tenant_in_production(tmp_path, monkeypatch):
    """Live 2026-10-25: the supervisor runs with no request tenant, and in
    production ``x_tenant_id()`` refuses that, so every pass failed. The
    adapter reads the tenants holding operations, as the X workers do, and a
    cancel runs in the operation's own tenant."""
    from kazma_core.tenant_context import get_current_tenant_id
    from kazma_core.x_api import ownership

    store = _x_store(tmp_path, ["sending", "awaiting_approval"], tenant="default")
    monkeypatch.setattr("kazma_core.x_api.publication_store._test_store", store)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    assert get_current_tenant_id() in (None, "")
    with pytest.raises(PermissionError):  # the old read: the request tenant only
        ownership.x_tenant_id()
    assert {r.native_state for r in _listing("x_post").runs} == {"sending", "awaiting_approval"}

    seen = []

    def update_schedule(ident, *, expected_version, action):
        seen.append((ident, ownership.x_tenant_id(), expected_version, action))
        return True

    monkeypatch.setattr("kazma_core.x_api.publication_service.update_schedule", update_schedule)
    result = asyncio.run(registry.adapter_for("x_post").act("op1", "cancel", actor="owner"))
    assert result.ok and seen == [("op1", "default", 1, "cancel")]
    missing = asyncio.run(registry.adapter_for("x_post").act("nope", "cancel", actor="owner"))
    assert missing.code == "not_found"


# ── memory queue ─────────────────────────────────────────────────────────

def test_memory_tasks_are_read_from_the_queue(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema

    path = tmp_path / "memory_ops.db"
    monkeypatch.setattr("kazma_core.runs.adapters.memory_queue._ops_db", lambda: str(path))
    conn = sqlite3.connect(path)
    ensure_ops_schema(conn)
    rows = [("t-proc", "processing", 1, NOW - 30), ("t-stuck", "processing", 1, NOW - 4000),
            ("t-retry", "pending", 2, NOW - 30), ("t-fresh", "pending", 0, NOW - 30),
            ("t-dead", "failed", 6, NOW - 30), ("t-done", "completed", 1, NOW - 30)]
    for ident, status, attempts, updated in rows:
        conn.execute("INSERT INTO memory_task_queue (id, task_type, payload_json, status, attempts, max_attempts, "
                     "created_at, updated_at) VALUES (?, 'micro_consolidation', '{}', ?, ?, 6, ?, ?)",
                     (ident, status, attempts, updated, updated))
    conn.commit()
    conn.close()
    runs = {r.native_id: r for r in _listing("memory_task").runs}
    assert set(runs) == {"t-proc", "t-stuck", "t-retry", "t-dead"}  # first-time and done work is routine
    assert runs["t-proc"].owner_alive is True and runs["t-stuck"].owner_alive is False
    assert runs["t-dead"].state == RunState.FAILED and runs["t-dead"].actions == ("retry",)
    assert runs["t-retry"].state == RunState.QUEUED


# ── chat turns and maintenance ───────────────────────────────────────────

def test_chat_turns_are_read_from_the_turn_registry(monkeypatch):
    from kazma_ui import active_turns, reply_sink, runs_chat_turn

    active_turns.reset_active_turns()
    reply_sink.reset_reply_turns()
    monkeypatch.setattr("kazma_ui.turn_liveness._is_awaiting_human", lambda tid: tid == "t-wait")
    monkeypatch.setattr("kazma_ui.session_manager.peek_session_manager", lambda: None)
    runs_chat_turn._abandoned_since.clear()
    try:
        active_turns.register_turn("t-run", SimpleNamespace(done=lambda: False))
        for tid in ("t-run", "t-wait", "t-gone"):
            reply_sink.open_reply_turn(tid, f"turn-{tid}")
        runs = {r.native_id: r for r in _listing("chat_turn").runs}
        assert runs["t-run"].state == RunState.RUNNING and runs["t-run"].actions == ("cancel",)
        assert runs["t-wait"].state == RunState.WAITING_HUMAN and runs["t-wait"].actions == ()
        assert runs["t-gone"].owner_alive is False and runs["t-gone"].actions == ("close",)
    finally:
        active_turns.reset_active_turns()
        reply_sink.reset_reply_turns()
        runs_chat_turn._abandoned_since.clear()


def test_a_failing_maintenance_sweep_is_a_failed_run(monkeypatch):
    from kazma_core.memory import worker_bootstrap

    monkeypatch.setattr(worker_bootstrap, "_SWEEP_STATE", {
        "memory mirror sync": {"last_ok_at": NOW - 9000, "last_failed_at": NOW - 10,
                               "consecutive_failures": 4, "last_error": "OperationalError: locked"},
        "artifact GC": {"last_ok_at": NOW - 10, "last_failed_at": None, "consecutive_failures": 0,
                        "last_error": None},
    })
    runs = _listing("maintenance").runs
    assert [r.native_id for r in runs] == ["memory mirror sync"]
    assert runs[0].state == RunState.FAILED and runs[0].actions == ()


def test_one_unreadable_kind_does_not_hide_the_others(monkeypatch):
    """A store that cannot be read is reported, never "nothing running"."""
    from kazma_core.memory import worker_bootstrap

    monkeypatch.setattr(worker_bootstrap, "_SWEEP_STATE", {
        "x": {"last_ok_at": None, "last_failed_at": NOW, "consecutive_failures": 3, "last_error": "e"}})

    async def broken(now, *, window_s):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(registry.adapter_for("memory_task"), "list_runs", broken)
    listing = asyncio.run(registry.list_runs(kinds=("memory_task", "maintenance"), now=NOW))
    assert listing.errors == {"memory_task": "OperationalError"}
    assert [r.kind for r in listing.runs] == ["maintenance"]
