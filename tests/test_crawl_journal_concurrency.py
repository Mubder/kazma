"""Writers of one crawl job in one process take turns (2026-10-31).

Every live deep research failed right after its searches with "Durable crawl
journal is busy": the pipeline fetches four pages at once and each records its
start, its receipt and its end on the job's row; the row's compare-and-set
retried 8 times with no wait, and the writers starved each other (on Postgres
16% of writes gave up with 4 writers, 35% with 8; on SQLite 7% and 22%). A
job's writers in one process now hold its lock; the compare-and-set is left
for other processes, with a random pause between attempts.
"""
from __future__ import annotations

import os
import threading

import pytest

from kazma_core import config_store
from kazma_core.config_store import ConfigRevisionConflict
from kazma_core.web_acquire import crawl_jobs
from kazma_core.web_acquire.result import AcquisitionError

POLICY = dict(mode="local_only", robots=True, max_pages=10, scope="tree", delay_ms=0)


@pytest.fixture
def journal(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "journal.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    yield store
    store.close()


def _counting_conflicts(store, monkeypatch) -> list[int]:
    conflicts = [0]
    real = store.batch_set

    def batch_set(items, *, expected=None):
        try:
            return real(items, expected=expected)
        except ConfigRevisionConflict:
            conflicts[0] += 1
            raise

    monkeypatch.setattr(store, "batch_set", batch_set)
    return conflicts


def _storm(job, writers: int, each: int) -> list[str]:
    failures: list[str] = []

    def write(n: int) -> None:
        for i in range(each):
            try:
                job.update(lambda row, n=n, i=i: row["progress"].update(**{f"w{n}": i}))
            except AcquisitionError as exc:
                failures.append(str(exc))

    threads = [threading.Thread(target=write, args=(n,)) for n in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return failures


def test_writers_in_one_process_never_collide(journal, monkeypatch):
    job = crawl_jobs.start("docs", "https://docs.example.test/", POLICY, job_id="storm")
    conflicts = _counting_conflicts(journal, monkeypatch)
    assert _storm(job, writers=8, each=25) == []
    assert conflicts[0] == 0
    progress = crawl_jobs.inspect_jobs("storm")["progress"]
    assert all(progress[f"w{n}"] == 24 for n in range(8))  # every writer's last write landed


def test_without_the_lock_a_writer_loses_its_turn(journal, monkeypatch):
    """Negative control: each writer its own lock (none shared), and a writer
    that reads, then waits while another writes, conflicts every time."""
    monkeypatch.setattr(crawl_jobs, "_job_lock", lambda job_id: threading.Lock())
    monkeypatch.setattr(crawl_jobs, "_retry_pause", lambda attempt: None)
    job = crawl_jobs.start("docs", "https://docs.example.test/", POLICY, job_id="race")
    conflicts = _counting_conflicts(journal, monkeypatch)
    real_read = crawl_jobs._read_job
    other_wrote = threading.Event()
    first = threading.Event()

    def read_then_let_the_other_write(store, job_id):
        answer = real_read(store, job_id)
        if threading.current_thread().name == "slow" and not first.is_set():
            first.set()
            other_wrote.wait(timeout=5)
        return answer

    monkeypatch.setattr(crawl_jobs, "_read_job", read_then_let_the_other_write)
    slow = threading.Thread(target=lambda: job.update(lambda row: row["progress"].update(slow=1)), name="slow")
    slow.start()
    first.wait(timeout=5)
    job.update(lambda row: row["progress"].update(fast=1))
    other_wrote.set()
    slow.join()
    assert conflicts[0] >= 1


@pytest.mark.postgres
@pytest.mark.skipif(os.environ.get("KAZMA_DB_BACKEND") != "postgres", reason="needs the Postgres test database")
def test_writers_never_collide_on_postgres(tmp_path, monkeypatch):
    store = config_store.ConfigStore(str(tmp_path / "unused.db"))
    assert store._use_postgres()
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    job_id = "storm-" + os.urandom(4).hex()
    job = crawl_jobs.start("docs-" + job_id, "https://docs.example.test/", POLICY, job_id=job_id)
    try:
        conflicts = _counting_conflicts(store, monkeypatch)
        assert _storm(job, writers=8, each=10) == []
        assert conflicts[0] == 0
    finally:
        job.finish("completed")
        crawl_jobs.purge_terminal()
        store.close()
