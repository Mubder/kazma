"""Phase 4 — cancel_job act resolver (Commitment §3.5).

cancel_scheduled takes a job_id. The resolver verifies that id is a REAL PENDING
job on this thread before allowing — catches hallucinated / wrong-thread /
already-terminal ids — and clarifies WITH the actual pending list otherwise.
"""

from __future__ import annotations

import sqlite3

import pytest

from kazma_core.safety.commitment import authorize_effect

_SCHEMA = """CREATE TABLE IF NOT EXISTS cron_jobs (
  job_id TEXT PRIMARY KEY, timing TEXT NOT NULL, prompt TEXT NOT NULL,
  platform TEXT NOT NULL, thread_id TEXT NOT NULL, status TEXT DEFAULT 'pending',
  created_at TEXT, next_run TEXT, last_result TEXT,
  tenant_id TEXT NOT NULL DEFAULT 'default', delivery_target TEXT NOT NULL DEFAULT '')"""


def _seed(db_path, rows):
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    for r in rows:
        conn.execute(
            "INSERT INTO cron_jobs (job_id, timing, prompt, platform, thread_id, "
            "status, tenant_id) VALUES (?,?,?,?,?,?,?)",
            (r["job_id"], "5m", r["prompt"], "telegram", r["thread_id"],
             r.get("status", "pending"), "default"),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def cron(tmp_path, monkeypatch):
    """Isolated cron.db + ops.db, scheduler singleton pointed at the cron.db."""
    db = tmp_path / "cron.db"
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    from kazma_core.cron.scheduler import (
        CronScheduler, SQLiteCronStore, get_cron_scheduler, set_cron_scheduler,
    )

    prev = get_cron_scheduler()
    store = SQLiteCronStore(str(db))
    sched = CronScheduler(store=store)
    set_cron_scheduler(sched)
    yield db
    set_cron_scheduler(prev)


def test_valid_pending_job_id_allows(cron):
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "j1"}, thread_id="t1", tenant_id="default")
    assert d.decision == "allow"
    assert d.commitment_id


def test_hallucinated_job_id_clarifies_with_real_list(cron):
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"},
                 {"job_id": "j2", "prompt": "dentist", "thread_id": "t1"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE-9999"}, thread_id="t1")
    assert d.decision == "clarify"
    assert "j1" in d.clarify_question and "j2" in d.clarify_question
    assert "FAKE-9999" in d.clarify_question


def test_wrong_thread_job_id_clarifies(cron):
    """A job_id that belongs to a different thread is not cancellable here."""
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t2"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "j1"}, thread_id="t1")
    assert d.decision == "clarify"  # j1 isn't pending on t1
    assert "no pending jobs" in d.clarify_question  # t1 has none


def test_terminal_job_id_clarifies(cron):
    """A job_id that exists but is already done (not pending) → clarify."""
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1", "status": "done"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "j1"}, thread_id="t1")
    assert d.decision == "clarify"  # done jobs aren't cancellable


def test_no_scheduler_degrades_audit_only(tmp_path, monkeypatch):
    """If get_cron_scheduler() is None, the resolver can't check → audit-only
    (the gate never errors the turn)."""
    from kazma_core.cron.scheduler import get_cron_scheduler, set_cron_scheduler

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    prev = get_cron_scheduler()
    set_cron_scheduler(None)
    try:
        d = authorize_effect("cancel_scheduled", {"job_id": "j1"}, thread_id="t1")
        # No pending jobs readable (no scheduler) → clarify with empty list is
        # wrong here; the resolver degrades to audit-only allow instead.
        assert d.decision == "allow"
        assert d.commitment_id is None  # audit-only, nothing persisted
    finally:
        set_cron_scheduler(prev)


def test_yolo_mode_bypasses_cancel_gate(cron):
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE"}, thread_id="t1",
                         cfg={"mode": "yolo"})
    assert d.decision == "allow"
    assert d.commitment_id is None  # bypassed, audit-only
