"""Every store says how long it keeps what it holds (2026-11, FA-141).

The approval-gate registry, the tool receipts, the task ledgers, the
model-call ledger and the swarm's pipeline log were append-only with no
retention, and nothing declared it, so nothing noticed. Now every database
in ``store_registry`` has a ``RETENTION`` entry (a rule and where it runs,
or "kept:" and why), a sweep an entry names is a real maintenance sweep, and
the five stores have a prune that removes decided rows only.

Each prune is measured on its real store: an old decided row goes, a new one
stays, an undecided one (a live gate, a receipt of an unknown outcome, an
active ledger) stays whatever its age.
"""

from __future__ import annotations

import time

import pytest

DAY = 86400


def test_every_store_declares_its_retention():
    from kazma_core import store_registry as reg

    names = set(reg.STORES) | set(reg.STORE_FAMILIES)
    assert names - set(reg.RETENTION) == set(), "declare how long each store keeps its rows"
    assert set(reg.RETENTION) - names == set(), "a retention entry names no store"
    assert all(text.strip() for text in reg.RETENTION.values())


def test_every_sweep_a_retention_names_runs():
    from kazma_core import store_registry as reg
    from kazma_core.memory import worker_bootstrap as wb

    labels = {label for label, _ in wb._MAINTENANCE_SWEEPS}
    for name, text in reg.RETENTION.items():
        if " sweep" not in text or not text.startswith("the "):
            continue
        sweep = text.split(" sweep", 1)[0].removeprefix("the ")
        assert sweep in labels, f"{name}: no maintenance sweep called {sweep!r}"


def test_negative_control_an_undeclared_store_is_found(monkeypatch):
    from kazma_core import store_registry as reg

    monkeypatch.setitem(reg.STORES, "planted.db", reg.Store("a planted store", "bundle"))
    assert "planted.db" in (set(reg.STORES) | set(reg.STORE_FAMILIES)) - set(reg.RETENTION)


@pytest.fixture
def gates(tmp_path):
    from kazma_core.safety import hitl_gates as hg

    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    yield hg
    hg.set_db_path_for_tests(None)


def test_old_decided_gates_go_and_live_ones_stay(gates):
    old = gates.register_gate(gates.GateRow(gate_id="old", thread_id="t", tool="file_write"))
    gates.claim_gate(old.gate_id, "approve", "owner")
    gates.settle_gate(old.gate_id, "done")
    gates.register_gate(gates.GateRow(gate_id="live", thread_id="t", tool="shell_exec"))
    recent = gates.register_gate(gates.GateRow(gate_id="recent", thread_id="t", tool="file_write"))
    gates.claim_gate(recent.gate_id, "deny", "owner")
    gates.settle_gate(recent.gate_id, "denied")
    later = time.time() + 100 * DAY
    # The recent one was decided "now", the old one too: age them apart.
    conn = gates._connect()
    with conn:
        conn.execute("UPDATE hitl_gates SET settled_at = ?, created_at = ? WHERE gate_id = 'recent'",
                     (later - DAY, later - DAY))
        conn.execute("UPDATE hitl_gates SET created_at = 0 WHERE gate_id = 'live'")
    conn.close()
    assert gates.prune_decided_gates(now=later) == 1
    assert gates.gate_for("old") is None
    assert gates.gate_for("live") is not None and gates.gate_for("recent") is not None


def test_completed_receipts_go_and_unknown_ones_stay(tmp_path):
    from kazma_core.agent.effect_journal import _EffectJournal

    journal = _EffectJournal(tmp_path / "tool_effects.db")
    journal.begin("e-done", "h1", "t", "file_write")
    journal.begin("e-unknown", "h2", "t", "file_write")
    conn = journal._connect()
    with conn:
        conn.execute("UPDATE tool_effects SET state = 'completed', completed_at = 0, started_at = 0 "
                     "WHERE effect_id = 'e-done'")
        conn.execute("UPDATE tool_effects SET started_at = 0 WHERE effect_id = 'e-unknown'")
    conn.close()
    assert journal.prune_completed(days=90) == 1
    left = {row[0] for row in journal._connect().execute("SELECT effect_id FROM tool_effects")}
    assert left == {"e-unknown"}


def test_finished_ledgers_go_and_active_ones_stay(tmp_path):
    from kazma_core.agent.task_ledger import TaskLedger, TaskLedgerStore

    store = TaskLedgerStore(tmp_path / "task_ledgers.db")
    for thread, status in (("t-done", "done"), ("t-active", "active"), ("t-new", "superseded")):
        ledger = TaskLedger(thread_id=thread)
        ledger.status = status
        store.save(ledger)
    conn = store._connect()
    with conn:
        conn.execute("UPDATE task_ledgers SET updated_at = 0 WHERE thread_id IN ('t-done', 't-active')")
    conn.close()
    assert store.prune_finished(days=90) == 1
    left = {row[0] for row in store._connect().execute("SELECT thread_id FROM task_ledgers")}
    assert left == {"t-active", "t-new"}


def test_model_calls_past_a_year_go(tmp_path, monkeypatch):
    from kazma_core.observability import llm_ledger

    monkeypatch.setattr(llm_ledger, "_conn", None)
    conn = llm_ledger._get_conn(str(tmp_path / "llm_calls.db"))
    try:
        with conn:
            conn.execute("INSERT INTO llm_calls (ts, model) VALUES ('2020-01-01T00:00:00+00:00', 'old')")
            conn.execute("INSERT INTO llm_calls (ts, model) VALUES (?, 'new')",
                         (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),))
        assert llm_ledger.prune_old_calls() == 1
        assert [r[0] for r in conn.execute("SELECT model FROM llm_calls")] == ["new"]
    finally:
        conn.close()
        monkeypatch.setattr(llm_ledger, "_conn", None)


def test_old_pipeline_log_rows_go(tmp_path, monkeypatch):
    from kazma_core.swarm.memory import pipeline_logger as pl

    monkeypatch.setattr(pl, "_conn", None)
    conn = pl._get_conn(str(tmp_path / "pipeline_logs.db"))
    try:
        conn.execute("INSERT INTO pipeline_logs (timestamp, correlation_id, event_type) VALUES (0, 'c', 'step')")
        conn.execute("INSERT INTO pipeline_logs (timestamp, correlation_id, event_type) VALUES (?, 'c', 'step')",
                     (time.time(),))
        assert pl.prune_pipeline_logs(days=30) == 1
        assert pl.prune_pipeline_logs(days=0) == 0  # 0 keeps everything
    finally:
        conn.close()
        monkeypatch.setattr(pl, "_conn", None)
