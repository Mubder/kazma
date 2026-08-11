"""Phase 2 — commitment store + TTL/GC (Commitment §3.9, the ship-blocker).

Every binding rule from §3.9 must be covered before the commitments table
ships to production. The store is isolated per test via KAZMA_MEMORY_OPS_DB.
"""

from __future__ import annotations

import time

import pytest

from kazma_core.safety.commitment.store import (
    Commitment,
    PENDING_CAP_PER_THREAD,
    abort_pending_for_thread,
    create_commitment,
    delete_retained,
    enforce_pending_cap,
    get_commitment,
    list_by_thread,
    sweep_expired,
    update_status,
)


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    """Redirect the ops DB to a tmp file so tests never touch real data."""
    db = tmp_path / "ops.db"
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(db))
    return db


def _cmt(thread_id="t1", *, act="remind", status="draft", **kw) -> Commitment:
    return Commitment(thread_id=thread_id, act=act, status=status, **kw)


# ── CRUD ───────────────────────────────────────────────────────────────────

def test_create_get_roundtrip(ops_db):
    cid = create_commitment(_cmt(act="remind", goal_text="remind X",
                                 slots={"fire_at": "2026-08-30"}))
    got = get_commitment(cid)
    assert got is not None
    assert got.act == "remind"
    assert got.slots == {"fire_at": "2026-08-30"}
    assert got.goal_text == "remind X"
    assert got.status == "draft"


def test_expires_at_set_by_status(ops_db):
    """§3.9 TTL: draft → +3600s; needs_confirm → +86400s; ready → +900s."""
    before = time.time()
    cid_draft = create_commitment(_cmt(status="draft"))
    cid_confirm = create_commitment(_cmt(status="needs_confirm"))
    cid_ready = create_commitment(_cmt(status="ready"))
    d, n, r = get_commitment(cid_draft), get_commitment(cid_confirm), get_commitment(cid_ready)
    assert d.expires_at and abs(d.expires_at - before - 3600) < 5
    assert n.expires_at and abs(n.expires_at - before - 86400) < 5
    assert r.expires_at and abs(r.expires_at - before - 900) < 5


def test_update_status_to_terminal_clears_expiry(ops_db):
    """committed/aborted/expired → resolved_at stamped, expires_at cleared."""
    cid = create_commitment(_cmt(status="needs_confirm"))
    assert get_commitment(cid).expires_at is not None
    updated = update_status(cid, "committed", event_type="approved",
                            result={"job_id": "j1"})
    assert updated.status == "committed"
    assert updated.resolved_at is not None
    assert updated.expires_at is None
    assert updated.result == {"job_id": "j1"}


# ── sweep_expired (§3.9 rule 1+2) ──────────────────────────────────────────

def test_sweep_expired_marks_pending_past_ttl(ops_db):
    cid = create_commitment(_cmt(status="needs_confirm"))
    # jump past the 24h TTL
    future = time.time() + 100000
    n = sweep_expired(now=future)
    assert n == 1
    assert get_commitment(cid).status == "expired"


def test_sweep_expired_leaves_terminal_and_unexpired_alone(ops_db):
    fresh = create_commitment(_cmt(status="needs_confirm"))            # not expired
    committed = create_commitment(_cmt(status="needs_confirm"))
    update_status(committed, "committed")                              # terminal
    n = sweep_expired(now=time.time() + 100000)
    assert n == 1  # only the fresh pending one (committed has no expires_at)
    assert get_commitment(fresh).status == "expired"
    assert get_commitment(committed).status == "committed"


# ── pending cap (§3.9 rule 6) ──────────────────────────────────────────────

def test_enforce_pending_cap_aborts_oldest(ops_db):
    ids = [create_commitment(_cmt(status="needs_confirm")) for _ in range(PENDING_CAP_PER_THREAD + 3)]
    aborted = enforce_pending_cap("t1", cap=PENDING_CAP_PER_THREAD)
    assert aborted == 3
    statuses = [get_commitment(i).status for i in ids]
    # the 3 OLDEST (first created) aborted; rest still pending
    assert statuses[:3] == ["aborted", "aborted", "aborted"]
    assert all(s == "needs_confirm" for s in statuses[3:])


# ── supersede on new turn (§3.9 rule 5) ────────────────────────────────────

def test_abort_pending_for_thread(ops_db):
    p1 = create_commitment(_cmt(status="needs_clarify"))
    p2 = create_commitment(_cmt(status="needs_confirm"))
    done = create_commitment(_cmt(status="needs_confirm"))
    update_status(done, "committed")
    aborted = abort_pending_for_thread("t1")
    assert aborted == 2  # only pending, not the committed one
    assert get_commitment(p1).status == "aborted"
    assert get_commitment(p2).status == "aborted"
    assert get_commitment(done).status == "committed"  # untouched


# ── tiered retention (§3.9 rule 3+4) ───────────────────────────────────────

def test_delete_retained_tiered(ops_db):
    # ephemeral act (remind) committed 31 days ago → eligible at ephemeral (30d)
    eph = create_commitment(_cmt(act="remind", status="needs_confirm"))
    update_status(eph, "committed")
    # critical act (config_change) committed 31 days ago → NOT yet (needs 365d)
    crit = create_commitment(_cmt(act="config_change", status="needs_confirm"))
    update_status(crit, "committed")
    # artificially age both resolved_at to 31 days ago via direct SQL
    import sqlite3
    from kazma_core.paths import memory_ops_db
    cut = time.time() - 31 * 86400
    with sqlite3.connect(memory_ops_db()) as conn:
        conn.execute("UPDATE commitments SET resolved_at=? WHERE commitment_id IN (?,?)",
                     (cut, eph, crit))
        conn.commit()
    deleted = delete_retained(ephemeral_days=30, critical_days=365)
    assert deleted == 1  # only the ephemeral remind; critical retained
    assert get_commitment(eph) is None
    assert get_commitment(crit) is not None
    # now age the critical one past 365d → deleted
    cut2 = time.time() - 366 * 86400
    with sqlite3.connect(memory_ops_db()) as conn:
        conn.execute("UPDATE commitments SET resolved_at=? WHERE commitment_id=?", (cut2, crit))
        conn.commit()
    assert delete_retained(ephemeral_days=30, critical_days=365) == 1
    assert get_commitment(crit) is None


def test_list_by_thread_filters_status(ops_db):
    create_commitment(_cmt(thread_id="t1", status="needs_confirm"))
    create_commitment(_cmt(thread_id="t1", status="committed"))
    create_commitment(_cmt(thread_id="t2", status="needs_confirm"))
    assert len(list_by_thread("t1")) == 2
    assert len(list_by_thread("t1", status="needs_confirm")) == 1
    assert len(list_by_thread("t2")) == 1
