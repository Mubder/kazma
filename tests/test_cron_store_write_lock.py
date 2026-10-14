"""The cron store never keeps cron.db's write lock between calls.

Live 2026-09-27: the store's connection was in Python's default transaction
mode, where a DELETE opens a transaction even when it matches nothing, and
``purge_terminal_jobs`` committed only when it had deleted something. The
purge runs when the scheduler starts and every hour, so from each boot until
the next reminder fired the server held cron.db's write lock, and every other
writer waited out its timeout and failed: the live-data cleanup stopped with
"database is locked" half way through. The store is in autocommit mode now,
every statement its own transaction.

Every public method is called -- the purge with nothing to purge, an insert
that fails -- and after each one another connection must get the write lock
at once. A public method added without a case here fails the enumeration.
The negative control opens the store in the old mode and shows the same
check catching the held lock.
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from kazma_core.cron.scheduler import JobStatus, ScheduledJob, SQLiteCronStore


def _write_lock_free(path: Path) -> bool:
    """Can another connection start a write transaction right now?"""
    other = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("ROLLBACK")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        other.close()


def _job(job_id: str, *, status: JobStatus = JobStatus.PENDING, days_old: int = 0) -> ScheduledJob:
    created = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    # Retention counts from when a job FINISHED (SCH-E07): an old finished
    # job here finished when it was made.
    finished = created if status not in (JobStatus.PENDING, JobStatus.RUNNING) else ""
    return ScheduledJob(
        job_id=job_id, timing="5m", prompt="p", platform="telegram", thread_id="t1",
        status=status, created_at=created, delivery_target="telegram:1",
        next_run=datetime.now(UTC).isoformat(), finished_at=finished,
    )


def _never(*_a, **_k):  # a recurrence that has ended
    return None


def _cases(store: SQLiteCronStore) -> list[tuple[str, str, object]]:
    """(method, what the call exercises, coroutine) in the order they run.

    The durable fire record (Scheduled audit SCH-E04, 2026-10-14) added the
    claim/finish/release methods, the delivery outbox and the event log; each
    is a group of writes in one BEGIN IMMEDIATE, and each is called here.
    """
    from kazma_core.cron.store import FireResult

    now = datetime.now(UTC)
    return [
        ("insert", "a new job", lambda: store.insert(_job("a"))),
        ("insert", "a duplicate id (the INSERT raises)", lambda: store.insert(_job("a"))),
        ("get", "", lambda: store.get("a")),
        ("list_active", "", lambda: store.list_active()),
        ("list_all", "", lambda: store.list_all()),
        ("due_jobs", "", lambda: store.due_jobs(now=now)),
        ("list_running", "", lambda: store.list_running()),
        ("count_jobs", "", lambda: store.count_jobs(tenant_id="default")),
        ("purge_terminal_jobs", "nothing to purge (the live case)", lambda: store.purge_terminal_jobs()),
        ("claim_job", "the claim wins", lambda: store.claim_job("a", owner="o1")),
        ("claim_job", "the claim loses", lambda: store.claim_job("a", owner="o1")),
        ("release_fire", "back to pending", lambda: store.release_fire("a", owner="o1")),
        ("claim_job", "claimed again", lambda: store.claim_job("a", owner="o1")),
        ("finish_fire", "with a delivery", lambda: store.finish_fire(
            "a", owner="o1", advance=_never,
            result=FireResult(success=True, outcome="delivering", body="hi", target="telegram:1"))),
        ("claim_delivery", "", lambda: store.claim_delivery(owner="o1")),
        ("finish_delivery", "refused, retried", lambda: store.finish_delivery(1, owner="o1", delivered=False,
                                                                                error="Error: no")),
        ("recover_deliveries", "", lambda: store.recover_deliveries(owner="o1")),
        ("unalerted_failures", "", lambda: store.unalerted_failures()),
        ("mark_alerted", "", lambda: store.mark_alerted(1)),
        ("deliveries_for", "", lambda: store.deliveries_for("a")),
        ("pending_delivery_count", "", lambda: store.pending_delivery_count()),
        ("outcome_counts", "", lambda: store.outcome_counts(since=0.0)),
        ("lateness", "", lambda: store.lateness(since=0.0)),
        ("insert", "a second job", lambda: store.insert(_job("b"))),
        ("mark_missed", "", lambda: store.mark_missed(
            "b", expected_next_run="", notice="missed", target="telegram:1", advance=_never)),
        ("update_result", "", lambda: store.update_result("a", "ok")),
        ("update_job", "a field changes", lambda: store.update_job("a", prompt="q")),
        ("update_job", "no field given", lambda: store.update_job("a")),
        ("restore_job", "", lambda: store.restore_job("a", timing="5m", next_run=now.isoformat())),
        ("job_exists", "", lambda: store.job_exists("a")),
        ("update_delivery_target", "", lambda: store.update_delivery_target("a", "telegram:2")),
        ("sibling_delivery_target", "", lambda: store.sibling_delivery_target("t1")),
        ("record_event", "", lambda: store.record_event("a", "note")),
        ("list_events", "", lambda: store.list_events("a")),
        ("cancel", "", lambda: store.cancel("a")),
        ("insert", "an old finished job", lambda: store.insert(_job("old", status=JobStatus.DONE, days_old=30))),
        ("purge_terminal_jobs", "one job to purge", lambda: store.purge_terminal_jobs()),
    ]


@pytest.mark.asyncio
async def test_no_method_returns_with_the_write_lock_held(tmp_path: Path) -> None:
    path = tmp_path / "cron.db"
    store = SQLiteCronStore(db_path=str(path))
    await store.init()
    held: list[str] = []
    try:
        assert _write_lock_free(path), "init"
        for name, what, call in _cases(store):
            try:
                await call()
            except sqlite3.IntegrityError:
                assert what.startswith("a duplicate id"), (name, what)
            label = f"{name} ({what})" if what else name
            if store._db.in_transaction or not _write_lock_free(path):  # noqa: SLF001
                held.append(label)
        assert held == [], f"these calls returned with cron.db's write lock held: {held}"
        assert await store.job_exists("a") and not await store.job_exists("old")
    finally:
        await store.close()
    assert _write_lock_free(path)


def test_every_public_method_has_a_case() -> None:
    public = {
        name for name, fn in inspect.getmembers(SQLiteCronStore, inspect.iscoroutinefunction)
        if not name.startswith("_")
    }
    covered = {name for name, _what, _call in _cases(None)} | {"init", "close"}  # type: ignore[arg-type]
    assert public - covered == set(), "add a case for each new method to _cases"
    assert covered - public == set(), "a case names a method the store no longer has"


@pytest.mark.asyncio
async def test_negative_control_the_old_mode_keeps_the_lock(tmp_path: Path, monkeypatch) -> None:
    """In Python's default mode the purge's old statement -- a DELETE that
    finds nothing -- leaves a transaction open, and the check above catches
    it. (The store's own writes are explicit BEGIN IMMEDIATE groups since
    2026-10-14, so the old statement is issued here by hand.)"""
    real_connect = aiosqlite.connect

    def default_mode(database, **kwargs):
        kwargs.pop("isolation_level", None)
        return real_connect(database, **kwargs)

    monkeypatch.setattr(aiosqlite, "connect", default_mode)
    path = tmp_path / "cron.db"
    store = SQLiteCronStore(db_path=str(path))
    await store.init()
    try:
        assert _write_lock_free(path)
        await store._db.execute(  # noqa: SLF001 — the purge as it was until 2026-09-27
            "DELETE FROM cron_jobs WHERE status IN ('done', 'failed', 'cancelled') AND created_at < ?", ("x",))
        assert store._db.in_transaction  # noqa: SLF001
        assert not _write_lock_free(path)
    finally:
        await store.close()
    assert _write_lock_free(path)
