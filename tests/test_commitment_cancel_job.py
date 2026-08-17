"""Phase 4 — cancel_job act resolver (Commitment §3.5).

cancel_scheduled takes a job_id. The resolver verifies that id is a REAL
PENDING job (same thread first, then any thread of the same tenant — jobs are
booked from one interface and cancelled from another) — catches hallucinated /
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


def test_cross_thread_job_id_allows(cron):
    """Incident 2026-08-16: reminders are scheduled from one interface
    (Telegram) and cancelled from another (Web), and legacy jobs carried an
    empty thread_id. The old thread-scoped lookup never matched those, so a
    VALID cancel clarified forever. Matching the exact job_id against ALL
    pending jobs (still tenant-scoped) is the hallucination guard now."""
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t2"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "j1"}, thread_id="t1")
    assert d.decision == "allow"
    assert "cross-thread" in (d.reason or "")


def test_empty_thread_id_job_allows_from_any_thread(cron):
    """The exact incident shape: a job stored with thread_id='' (schedule_task
    did not capture threads before the fix) cancelled from a web thread."""
    _seed(cron, [{"job_id": "cron-38094e61", "prompt": "grok reset", "thread_id": ""}])
    d = authorize_effect("cancel_scheduled", {"job_id": "cron-38094e61"},
                         thread_id="web-801c4508")
    assert d.decision == "allow"


def test_clarify_options_never_make_approve_a_cancel(cron):
    """An option-less clarify maps Approve → 'cancel' in build_resume_value,
    which turned every approval into "cancelled by the user". The clarify must
    carry real pending jobs as options so Approve picks an actual job."""
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE-9999"}, thread_id="t1")
    assert d.decision == "clarify"
    assert d.options, "cancel_job clarify must carry discrete options"
    ids = [o["id"] for o in d.options]
    assert any(i != "cancel" for i in ids), "must offer at least one real job"
    # Approve (first non-cancel option) must NOT resolve to 'cancel'.
    from kazma_core.safety.commitment.resume import build_resume_value
    payload = {"kind": "semantic_clarify", "items": [
        {"tool_call_id": "tc1", "tool": "cancel_scheduled", "options": d.options},
    ]}
    rv = build_resume_value(payload, approved=True)
    assert rv.get("tc1") != "cancel"


def test_other_tenant_job_id_clarifies(cron):
    """Tenant isolation preserved: a job in a different tenant is invisible."""
    _seed(cron, [])  # ensure the schema exists
    conn = sqlite3.connect(str(cron))
    conn.execute(
        "INSERT INTO cron_jobs (job_id, timing, prompt, platform, thread_id, "
        "status, tenant_id) VALUES (?,?,?,?,?,?,?)",
        ("jX", "5m", "other tenant", "telegram", "", "pending", "tenantB"),
    )
    conn.commit(); conn.close()
    d = authorize_effect("cancel_scheduled", {"job_id": "jX"}, thread_id="t1",
                         tenant_id="default")
    assert d.decision == "clarify"  # jX belongs to tenantB, not visible here


def test_terminal_job_id_clarifies(cron):
    """A job_id that exists but is already done (not pending) → clarify."""
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1", "status": "done"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "j1"}, thread_id="t1")
    assert d.decision == "clarify"  # done jobs aren't cancellable


def test_no_scheduler_clarifies_not_allow(tmp_path, monkeypatch):
    """If get_cron_scheduler() is None, the resolver cannot verify — clarify."""
    from kazma_core.cron.scheduler import get_cron_scheduler, set_cron_scheduler

    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    prev = get_cron_scheduler()
    set_cron_scheduler(None)
    try:
        d = authorize_effect("cancel_scheduled", {"job_id": "j1"}, thread_id="t1")
        assert d.decision == "clarify"
        assert "scheduler unavailable" in (d.reason or "")
    finally:
        set_cron_scheduler(prev)


def test_yolo_mode_bypasses_cancel_gate(cron):
    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE"}, thread_id="t1",
                         cfg={"mode": "yolo"})
    assert d.decision == "allow"
    assert d.commitment_id is None  # bypassed, audit-only


def test_active_security_yolo_bypasses_cancel_gate(cron, monkeypatch):
    """Incident 2026-08-16 ("YOLO keeps asking"): an ACTIVE per-thread security
    YOLO must bypass the semantic gate even when the commitment mode is the
    default 'balanced'. Otherwise the user approves once and the next semantic
    check interrupts again."""
    import kazma_core.safety.yolo as yolo_mod

    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    monkeypatch.setattr(yolo_mod, "is_yolo_active", lambda tid: tid == "t1")
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE"}, thread_id="t1")
    assert d.decision == "allow"
    assert "yolo" in (d.reason or "").lower()
    assert d.commitment_id is None  # bypassed, audit-only


def test_inactive_security_yolo_still_enforces(cron, monkeypatch):
    """With no active security YOLO the balanced gate still verifies."""
    import kazma_core.safety.yolo as yolo_mod

    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    monkeypatch.setattr(yolo_mod, "is_yolo_active", lambda tid: False)
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE"}, thread_id="t1")
    assert d.decision == "clarify"


def test_security_yolo_active_bypasses_cancel_gate(cron, monkeypatch):
    """Incident 2026-08-16 (YOLO keeps asking): an ACTIVE per-thread security
    YOLO must silence the commitment gate too, even when the commitment mode is
    the default 'balanced'. Otherwise the user approves once and the next
    semantic check interrupts again."""
    import kazma_core.safety.yolo as yolo_mod

    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    monkeypatch.setattr(yolo_mod, "is_yolo_active", lambda tid: tid == "t1")
    # No cfg mode → 'balanced'; the active security YOLO still bypasses.
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE"}, thread_id="t1")
    assert d.decision == "allow"
    assert "yolo" in (d.reason or "").lower()
    assert d.commitment_id is None


def test_security_yolo_inactive_still_enforces(cron, monkeypatch):
    """Without an active security YOLO the balanced gate still verifies."""
    import kazma_core.safety.yolo as yolo_mod

    _seed(cron, [{"job_id": "j1", "prompt": "standup", "thread_id": "t1"}])
    monkeypatch.setattr(yolo_mod, "is_yolo_active", lambda tid: False)
    d = authorize_effect("cancel_scheduled", {"job_id": "FAKE"}, thread_id="t1")
    assert d.decision == "clarify"
