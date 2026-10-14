"""Lifecycle failures surface above DEBUG and in health; the nightly export
reports what failed and is a portable, whole snapshot (Memory audits M20 and
M30: MEM-L10, MEM-L15).

Found 2026-10-13: vector repair and recovery reported a locked database as
"nothing to do"; the schedulers ignored a refused enqueue (a broken
``memory_ops.db`` stopped macro sleep, backups and reconsolidation while
health showed an empty queue); the export swallowed failures and counted
done; health had no queue ages and no last success; the export wrote vectors
as Python ``b'...'`` literals and only the newest 20,000 audit rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from types import SimpleNamespace

import pytest


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), check_same_thread=False)
    ops.row_factory = sqlite3.Row
    ensure_ops_schema(ops)
    yield SimpleNamespace(db=db, ops=ops, mp=monkeypatch, dir=tmp_path)
    ops.close()
    db.close()


def _locked(*a, **k):
    raise sqlite3.OperationalError("database is locked")


# ── M20: a locked database is a failure the runner sees ───────────────────


def test_a_locked_database_reaches_the_runner(mem):
    from kazma_core.memory import reembed, rehydrate

    mem.mp.setattr(reembed, "repair_unsearchable_vectors", _locked)
    with pytest.raises(sqlite3.OperationalError):
        reembed.run_vector_repair_pass()
    mem.mp.setattr(rehydrate, "_rehydrate_erased", _locked)
    with pytest.raises(sqlite3.OperationalError):
        rehydrate.run_rehydrate_pass()


def test_a_missing_schema_is_still_nothing_to_do(mem):
    """Negative control: "no such table" (a fresh install) stays quiet."""
    from kazma_core.memory import reembed

    def fresh(*a, **k):
        raise sqlite3.OperationalError("no such table: episodes")

    mem.mp.setattr(reembed, "repair_unsearchable_vectors", fresh)
    assert reembed.run_vector_repair_pass() == {"episodes": 0, "beliefs": 0, "remaining": 0}


def test_the_runner_warns_from_the_third_busy_pass_and_records_each(mem, caplog):
    from kazma_core.memory import worker_bootstrap as wb

    mem.mp.setattr(wb, "_SWEEP_STATE", {})
    caplog.set_level(logging.INFO, logger="kazma_core.memory.worker_bootstrap")

    def warnings() -> int:
        return sum(1 for r in caplog.records if r.levelno >= logging.WARNING and "probe sweep" in r.getMessage())

    for _ in range(2):
        asyncio.run(wb._run_sweeps((("probe sweep", _locked),)))
    assert warnings() == 0 and wb.sweep_health()["probe sweep"]["consecutive_failures"] == 2
    asyncio.run(wb._run_sweeps((("probe sweep", _locked),)))
    assert warnings() == 1
    asyncio.run(wb._run_sweeps((("probe sweep", lambda: None),)))
    state = wb.sweep_health()["probe sweep"]
    assert state["consecutive_failures"] == 0 and state["last_ok_at"] and state["last_failed_at"]


def test_any_other_failure_warns_at_once(mem, caplog):
    from kazma_core.memory import worker_bootstrap as wb

    mem.mp.setattr(wb, "_SWEEP_STATE", {})
    caplog.set_level(logging.INFO, logger="kazma_core.memory.worker_bootstrap")

    def broken():
        raise ValueError("bad row")

    asyncio.run(wb._run_sweeps((("probe sweep", broken),)))
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_a_refused_enqueue_warns_and_alerts(mem, caplog):
    from kazma_core.memory import task_queue as tq
    from kazma_core.memory import worker_bootstrap as wb

    alerts = []
    mem.mp.setattr("kazma_core.observability.ops_alerts.alert",
                   lambda key, title, detail="", **kw: alerts.append(key) or True)
    mem.mp.setattr(tq, "enqueue_task", lambda *a, **k: None)
    caplog.set_level(logging.WARNING, logger="kazma_core.memory.worker_bootstrap")
    assert wb._enqueue_checked("macro_sleep", {}) is None
    assert alerts == ["memory.enqueue_failed"]
    assert any("refused macro_sleep" in r.getMessage() for r in caplog.records)


def test_no_scheduler_calls_the_queue_unchecked():
    """Every enqueue in the worker bootstrap goes through ``_enqueue_checked``."""
    import ast
    import inspect

    from kazma_core.memory import worker_bootstrap as wb

    def raw_enqueues(source: str) -> list[int]:
        return [n.lineno for n in ast.walk(ast.parse(source))
                if (isinstance(n, ast.Name) and n.id == "enqueue_task")
                or (isinstance(n, ast.alias) and n.name == "enqueue_task")]

    helper = inspect.getsource(wb._enqueue_checked)
    source = inspect.getsource(wb).replace(helper, "")
    assert raw_enqueues(source) == []
    assert raw_enqueues("from x import enqueue_task\nenqueue_task('a', {})\n")  # negative control


def test_health_shows_queue_ages_last_successes_and_sweeps(mem):
    from kazma_core.memory import worker_bootstrap as wb
    from kazma_core.memory.v2_health import build_v2_health

    now = time.time()
    mem.ops.executemany(
        "INSERT INTO memory_task_queue (id, task_type, payload_json, status, created_at, updated_at) "
        "VALUES (?, ?, '{}', ?, ?, ?)",
        [("t1", "macro_sleep", "pending", now - 900, now - 900),
         ("t2", "native_backup", "processing", now - 120, now - 60),
         ("t3", "nightly_export", "completed", now - 7200, now - 7000)])
    mem.ops.commit()
    mem.mp.setattr(wb, "_SWEEP_STATE", {"vector repair": {"last_ok_at": None, "last_failed_at": now,
                                                         "consecutive_failures": 4, "last_error": "locked"}})
    health = build_v2_health()
    q = health["queue"]
    assert 890 < q["oldest_pending_s"] < 1000 and 50 < q["oldest_processing_s"] < 200
    assert q["last_success"]["nightly_export"] == pytest.approx(now - 7000, abs=1)
    assert health["sweeps"]["vector repair"]["consecutive_failures"] == 4
    assert health["status"] == "DEGRADED"


# ── M20 + M30: the nightly export ─────────────────────────────────────────


def test_a_failed_export_piece_fails_the_task(mem):
    from kazma_core.memory import export
    from kazma_core.memory.worker_bootstrap import _handle_nightly_export

    real = export._dump_query_jsonl

    def broken(path, conn, sql, params):
        if "episodes" in sql:
            raise sqlite3.OperationalError("database is locked")
        return real(path, conn, sql, params)

    mem.mp.setattr(export, "_dump_query_jsonl", broken)
    assert asyncio.run(_handle_nightly_export({"tenant_id": "default"})) is False
    mem.mp.setattr(export, "_dump_query_jsonl", real)
    assert asyncio.run(_handle_nightly_export({"tenant_id": "default"})) is True


def test_the_export_is_portable_text(mem):
    from kazma_core.memory.export import export_nightly_snapshots

    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, embedding) "
        "VALUES ('e1', 'default', 'c', 1, 'q', 'episodic', 1.0, ?)", (b"\x00\x00\x80?" * 8,))
    mem.db.commit()
    out = export_nightly_snapshots(tenant_id="default")
    assert out.failed == []
    line = json.loads(out["episodes"].read_text(encoding="utf-8").splitlines()[0])
    assert "embedding" not in line and line["user_text"] == "q"
    # Negative control: the old serializer wrote a Python literal.
    assert str(b"\x00\x00\x80?").startswith("b'")


def test_the_whole_audit_log_is_exported(mem):
    from kazma_core.memory.export import export_nightly_snapshots

    mem.ops.executemany(
        "INSERT INTO memory_audit_log (id, tenant_id, timestamp, event_type, target_table, target_id, actor, "
        "reason) VALUES (?, 'default', ?, 'append', 'beliefs', 'b', 'test', 'r')",
        [(f"a{i}", float(i)) for i in range(25_000)])
    mem.ops.commit()
    out = export_nightly_snapshots(tenant_id="default")
    assert out.counts["audit"] == 25_000  # the old export stopped at 20,000
    assert sum(1 for _ in out["audit"].open(encoding="utf-8")) == 25_000
