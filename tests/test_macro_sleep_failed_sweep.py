"""A macro_sleep sweep that fails leaves nothing half done (audit 2026-10-26
AUD-046).

``run_macro_sleep`` catches its own failure and reports ``sweep_error``. It
left the sweep's uncommitted tier moves open on the handler's connection, and
the handler's next step -- the ego anchor sweep -- committed that connection:
half a failed sweep landed (memories archived, their beliefs not), and the
queue then retried the whole task over it.
"""

from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import pytest
from kazma_core.memory import macro_sleep
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

from tests._no_models import keep_models_out

DAY = 86400.0


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    keep_models_out(monkeypatch, llm=False)
    state = tmp_path / "memory_state.db"
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(state))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    db = sqlite3.connect(str(state))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    old = time.time() - 400 * DAY
    # A memory past every TTL (archived by the sweep), and a fact superseded
    # long ago (moved to beliefs_archive by the same sweep, after the moves).
    db.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
               "created_at, last_accessed, metadata_json) VALUES ('e_old', 'default', 's1', 1, "
               "'Which supplier for the pump housings?', 'Nordvalve.', 'episodic', ?, ?, '{}')", (old, old))
    db.execute("INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
               "structural_importance, source_trust_weight, valid_from, valid_until, ingested_at, invalidated_at) "
               "VALUES ('b_old', 'default', 'user', 'lives_in', 'functional', 'Porto', 0.9, 2, 0.5, ?, ?, ?, ?)",
               (old, old, old, old))
    db.commit()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    db.close()


def _fail_at_the_fact_archive(mem) -> None:
    """The sweep fails after its tier moves, at the beliefs archive."""

    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")

    mem.mp.setattr(macro_sleep, "json", SimpleNamespace(dumps=boom))


def _tier(mem) -> str:
    return mem.db.execute("SELECT tier FROM episodes WHERE id = 'e_old'").fetchone()[0]


def test_the_handler_keeps_a_failed_sweep_out_and_retries(mem):
    from kazma_core.memory import ego_anchor
    from kazma_core.memory.worker_bootstrap import _handle_macro_sleep_sync

    _fail_at_the_fact_archive(mem)
    later: list[str] = []
    mem.mp.setattr(ego_anchor, "anchor_orphan_leaf_concepts",
                   lambda conn, **kw: later.append("anchor") or {"anchored": 0})
    assert _handle_macro_sleep_sync({"tenant_id": "default"}) is False  # the queue retries it
    assert later == []  # the steps after a failed sweep do not run
    assert _tier(mem) == "episodic"  # the move was undone, not committed
    assert mem.db.execute("SELECT COUNT(*) FROM beliefs WHERE id = 'b_old'").fetchone()[0] == 1


def test_a_failed_sweep_leaves_no_transaction_open(mem):
    _fail_at_the_fact_archive(mem)
    stats = macro_sleep.run_macro_sleep(mem.db, cfg={}, tenant_id="default")
    assert stats["sweep_error"] and stats["demoted_episodic"] == 1
    assert not mem.db.in_transaction
    mem.db.commit()  # whatever the caller commits next
    assert _tier(mem) == "episodic"


def test_negative_control_without_the_rollback_the_next_commit_lands_half_a_sweep(mem):
    """The old sweep: the failure was caught, the transaction left open."""
    _fail_at_the_fact_archive(mem)

    class _NoRollback:
        def __init__(self, conn):
            self._conn = conn

        def rollback(self):
            return None  # the old except had none

        def __getattr__(self, name):
            return getattr(self._conn, name)

    stats = macro_sleep.run_macro_sleep(_NoRollback(mem.db), cfg={}, tenant_id="default")
    assert stats["sweep_error"] and mem.db.in_transaction
    mem.db.commit()  # the ego anchor sweep's commit
    assert _tier(mem) == "archived"
